#!/usr/bin/env python

from argparse import ArgumentError, ArgumentParser
from typing import Dict, List, Tuple, Iterator
from datetime import datetime
from pathlib import Path
import contextlib
import asyncio
import os

from pkgcheck import keywords

import messages

base_dir = Path('/tmp/tattoo')
comm_dir = base_dir / 'comm'
fetch_datetime_file = Path.cwd() / 'controller.datetime.txt'


def collect_ssh_hosts() -> Iterator[str]:
    with open(Path.cwd() / 'ssh_config') as f:
        for row in f:
            if row.startswith('Host '):
                yield row.removeprefix('Host ').strip()


def read_fetch_datetimes() -> Dict[str, datetime]:
    res = {}
    with contextlib.suppress(Exception):
        with fetch_datetime_file.open() as f:
            for line in f:
                with contextlib.suppress(Exception):
                    system, datetime_s = line.rstrip('\n').split('=', maxsplit=1)
                    res[system] = datetime.fromisoformat(datetime_s)
    return res


async def run_ssh(*extra_args) -> bool:
    with contextlib.suppress(Exception):
        proc = await asyncio.create_subprocess_exec(
            'ssh', '-F', 'ssh_config', '-T', *extra_args,
            preexec_fn=os.setpgrp,
        )
        return 0 == await proc.wait()


def connect():
    os.makedirs(comm_dir, exist_ok=True)
    for existing in comm_dir.iterdir():
        existing.unlink()
    os.makedirs(base_dir / 'control', exist_ok=True)
    loop.run_until_complete(asyncio.gather(*(
        run_ssh('-fNM', host) for host in collect_ssh_hosts()
    )))


def disconnect():
    loop.run_until_complete(asyncio.gather(*(
        run_ssh('-O', 'exit', host) for host in collect_ssh_hosts()
    )))
    from shutil import rmtree
    rmtree(comm_dir, ignore_errors=True)
    rmtree(base_dir / 'control', ignore_errors=True)


def apply_passes(passes: List[Tuple[int, str]]):
    from nattka.bugzilla import NattkaBugzilla, BugCategory, arches_from_cc
    from nattka.package import find_repository, match_package_list, add_keywords
    from nattka.git import GitWorkTree, git_commit

    if api_key := os.getenv('ARCHTESTER_BUGZILLA_APIKEY'):
        nattka_bugzilla = NattkaBugzilla(api_key=api_key)
    else:
        raise ArgumentError(None, "To apply and resolve, set environment variable ARCHTESTER_BUGZILLA_APIKEY")
    _, repo = find_repository(Path(options.fetch_repo))
    git_repo = GitWorkTree(Path(options.fetch_repo))

    divided = {bug_no: frozenset(a for x, a in passes if x == bug_no) for bug_no, _ in passes}

    for bug_no, bug in nattka_bugzilla.find_bugs(bugs=divided.keys()).items():
        bug_cc = list(arches_from_cc(bug.cc, repo.known_arches))
        for arch in divided[bug_no]:
            if arch not in bug_cc:
                continue
            try:
                plist = dict(match_package_list(repo, bug, only_new=True, filter_arch=[arch], permit_allarches=True))
                allarches = 'ALLARCHES' in bug.keywords
                add_keywords(plist.items(), bug.category == BugCategory.STABLEREQ)
                for pkg, keywords in plist.items():
                    if arch not in keywords:
                        continue

                    ebuild_path = Path(pkg.path).relative_to(repo.location)
                    pfx = f'{pkg.category}/{pkg.package}'
                    act = ('Stabilize' if bug.category == BugCategory.STABLEREQ else 'Keyword')
                    kws = 'ALLARCHES' if allarches else arch
                    msg = f'{pfx}: {act} {pkg.fullver} {kws}, #{bug_no}'
                    print(git_commit(git_repo.path, msg, [str(ebuild_path)]))
                if options.fetch_resolve:
                    to_remove = bug_cc if allarches else [arch]
                    all_done = len(bug_cc) == 1 or allarches
                    to_close = (not bug.security) and all_done
                    if allarches:
                        comment = " ".join(f'[{a}]' if a == arch else a for a in to_remove)
                        comment += " (ALLARCHES) done"
                    else:
                        comment = f'{arch} done'
                    if all_done:
                        comment += '\n\nall arches done'
                    nattka_bugzilla.resolve_bug(
                        bugno=bug_no,
                        uncc=(f'{arch}@gentoo.org' for arch in to_remove),
                        comment=comment,
                        resolve=to_close
                    )
                    for arch in to_remove:
                        bug_cc.remove(arch)
            except Exception as e:
                print(f'failed to apply for {bug_no},{arch} , err:', e)


async def handler(socket_file: Path):
    if not socket_file.exists():
        print(f"No such socket {socket_file}")
        return
    try:
        reader, writer = await asyncio.open_unix_connection(path=socket_file)
    except:
        print("Failed Connect to", socket_file.name)
        return

    writer.write(messages.dump(messages.Worker(name='', arch='')))
    if options.bugs:
        writer.write(messages.dump(messages.GlobalJob(bugs=options.bugs)))
    if options.scan:
        writer.write(messages.dump(messages.DoScan()))
    await writer.drain()

    if options.action == 'fetch':
        now = datetime.utcnow()
        writer.write(messages.dump(messages.CompletedJobsRequest(since=fetch_datetimes.get(socket_file.name, datetime.fromtimestamp(0)))))
        await writer.drain()
        data = messages.load(await reader.readuntil(b'\n'))
        if isinstance(data, messages.CompletedJobsResponse):
            for bug_no, arch in data.passes:
                print(f'{bug_no},{arch}')
            fetch_bugs_passed.extend(data.passes)
            fetch_datetimes[socket_file.name] = now
    elif options.action == 'follower':
        writer.write(messages.dump(messages.Follower()))
        await writer.drain()
        with contextlib.suppress(Exception):
            while True:
                if data := await reader.readuntil(b'\n'):
                    data = messages.load(data)
                    if isinstance(data, messages.LogMessage):
                        print(f'[{data.worker.name}]: {data.msg}')
                else:
                    break

            writer.close()
            await writer.wait_closed()

    writer.close()
    await writer.wait_closed()


def argv_parser() -> ArgumentParser:
    parser = ArgumentParser()
    parser.add_argument("-c", "--connect", dest="connect", action="store_true",
                        help="Connect to all remote managers at start using ssh_config file")
    parser.add_argument("-d", "--disconnect", dest="disconnect", action="store_true",
                        help="Disconnect from all remove managers at end")
    parser.add_argument("-s", "--scan", dest="scan", action="store_true",
                        help="Run scan for bugs on remote managers")
    parser.add_argument("-b", "--bugs", dest="bugs", nargs='*', type=int,
                        help="Bugs to test")

    subparsers = parser.add_subparsers(title='actions', dest='action')

    follower_parser = subparsers.add_parser('follower')

    fetch_parser = subparsers.add_parser('fetch')
    fetch_parser.add_argument("-d", "--repo", dest="fetch_repo", action="store",
                              help="Repository to work on")
    fetch_parser.add_argument("-n", "--dry-run", dest="fetch_dryrun", action="store_true",
                              help="Apply and commit all passing bugs on repo")
    fetch_parser.add_argument("-a", "--apply", dest="fetch_apply", action="store_true",
                              help="Apply and commit all passing bugs on repo")
    fetch_parser.add_argument("-r", "--resolve", dest="fetch_resolve", action="store_true",
                              help="Resolve all passing bugs on repo")
    return parser

options = argv_parser().parse_args()
asyncio.set_event_loop(loop := asyncio.new_event_loop())

if options.connect:
    connect()

if options.action == 'fetch':
    fetch_datetimes = read_fetch_datetimes()
    fetch_bugs_passed: List[Tuple[int, str]] = []

loop.run_until_complete(asyncio.gather(*map(handler, comm_dir.iterdir())))

if options.action == 'fetch' and not options.fetch_dryrun:
    if fetch_bugs_passed and options.fetch_apply and options.fetch_repo:
        apply_passes(fetch_bugs_passed)
    with fetch_datetime_file.open('w') as f:
        f.writelines((f'{host}={date.isoformat()}\n' for host, date in fetch_datetimes.items()))

if options.disconnect:
    disconnect()
