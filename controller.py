#!/usr/bin/env python

import asyncio
import contextlib
import logging
import os
import subprocess
from argparse import ArgumentError, ArgumentParser
from datetime import datetime
from pathlib import Path
from typing import Any

import messages
from sdnotify import set_logging_format

set_logging_format()

try:
    from nattka.bugzilla import BugCategory, NattkaBugzilla, arches_from_cc
    from nattka.git import GitWorkTree, git_commit
    from nattka.package import (PackageListDoneAlready, add_keywords,
                                find_repository, match_package_list)
    HAVE_NATTKA = True
except ImportError:
    HAVE_NATTKA = False

base_dir = Path('/tmp/tattoo')
comm_dir = base_dir / 'comm'
fetch_datetime_file = Path.cwd() / 'controller.datetime.txt'


def collect_ssh_hosts() -> tuple[str, ...]:
    with open(Path.cwd() / 'ssh_config', encoding='utf8') as file:
        return tuple(
            row.removeprefix('Host ').strip()
            for row in file
            if row.startswith('Host ')
        )


def read_fetch_datetimes() -> dict[str, datetime]:
    res = {}
    with contextlib.suppress(Exception):
        with fetch_datetime_file.open() as file:
            for line in file:
                with contextlib.suppress(Exception):
                    system, datetime_s = line.rstrip('\n').split('=', maxsplit=1)
                    res[system] = datetime.fromisoformat(datetime_s)
    return res


async def run_ssh(*extra_args) -> bool:
    cmd_args = ('ssh', '-F', 'ssh_config', '-T', *extra_args)
    try:
        logging.info("running %r", ' '.join(cmd_args))
        proc = await asyncio.create_subprocess_exec(
            *cmd_args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            preexec_fn=os.setpgrp,
        )
        stdout, _ = await proc.communicate()
        if 0 != proc.returncode:
            logging.error("running %r failed with:\n%s", ' '.join(cmd_args), stdout.decode('utf8'))
        return 0 == proc.returncode
    except Exception as exc:
        logging.error("running %r failed", ' '.join(cmd_args), exc_info=exc)
        return False


async def connect():
    hosts = collect_ssh_hosts()
    comm_dir.mkdir(parents=True, exist_ok=True)
    (base_dir / 'control').mkdir(parents=True, exist_ok=True)
    for existing in comm_dir.iterdir():
        if existing.name in hosts:
            existing.unlink()
    result = await asyncio.gather(*(
        run_ssh('-fNM', host) for host in hosts
    ))
    if not all(result):
        logging.error("connect() failed")


async def disconnect():
    logging.info("disconnecting")
    hosts = collect_ssh_hosts()
    await asyncio.gather(*(
        run_ssh('-O', 'exit', host) for host in hosts
    ))
    for host in hosts:
        (comm_dir / host).unlink(missing_ok=True)
        (base_dir / 'control' / host).unlink(missing_ok=True)


def apply_passes(passes: list[tuple[int, str]]):
    if api_key := os.getenv('ARCHTESTER_BUGZILLA_APIKEY'):
        nattka_bugzilla = NattkaBugzilla(api_key=api_key)
    else:
        raise ArgumentError(None, "To apply and resolve, set environment variable ARCHTESTER_BUGZILLA_APIKEY")
    _, repo = find_repository(OPTIONS.fetch_repo)
    git_repo = GitWorkTree(OPTIONS.fetch_repo)

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
                if OPTIONS.fetch_resolve:
                    to_remove = bug_cc if allarches else [arch]
                    all_done = len(bug_cc) == 1 or allarches
                    to_close = all_done and not bug.security
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
                    logging.info("processed %d,%s", bug_no, arch)
                    for a in to_remove:
                        bug_cc.remove(a)
            except PackageListDoneAlready as exc:
                logging.warning("skipping %d,%s as it was already done", bug_no, arch, exc_info=exc)
            except Exception as exc:
                logging.error("failed to apply for %d,%s", bug_no, arch, exc_info=exc)


async def manager_communicate(socket_file: Path):
    if not socket_file.exists():
        logging.error("No such socket %s", socket_file)
        return
    try:
        reader, writer = await asyncio.open_unix_connection(path=socket_file)
    except Exception as exc:
        logging.error("Failed Connect to [%s]", socket_file.name, exc_info=exc)
        return

    try:
        writer.write(messages.dump(messages.Worker(name='', arch='')))
        if OPTIONS.bugs:
            writer.write(messages.dump(messages.GlobalJob(priority=OPTIONS.priority, bugs=OPTIONS.bugs)))
        if OPTIONS.scan:
            if OPTIONS.scan == '*' or socket_file.name in OPTIONS.scan.split(','):
                writer.write(messages.dump(messages.DoScan()))
                logging.info("Initiated scan for [%s]", socket_file.name)
        await writer.drain()

        if OPTIONS.info:
            writer.write(messages.dump(messages.GetStatus()))
            logging.info("Requesting status for [%s]", socket_file.name)
            await writer.drain()
            data = messages.load(await reader.readuntil(b'\n'))
            if isinstance(data, messages.ManagerStatus):
                statuses[socket_file.name] = data

        if OPTIONS.action == 'fetch':
            now = datetime.utcnow()
            writer.write(messages.dump(messages.CompletedJobsRequest(since=fetch_datetimes.get(socket_file.name, datetime.fromtimestamp(0)))))
            await writer.drain()
            data = messages.load(await reader.readuntil(b'\n'))
            if isinstance(data, messages.CompletedJobsResponse):
                for bug_no, arch in data.passes:
                    logging.info("test pass %d,%s", bug_no, arch)
                fetch_bugs_passed.extend(data.passes)
                fetch_datetimes[socket_file.name] = now
    except Exception as exc:
        logging.error("Failed communicating with socket [%s]", socket_file.name, exc_info=exc)
    finally:
        with contextlib.suppress(Exception):
            writer.close()
            await writer.wait_closed()


def argv_parser() -> ArgumentParser:
    parser = ArgumentParser()
    parser.add_argument("-c", "--connect", action="store_true",
                        help="Connect to all remote managers at start using ssh_config file")
    parser.add_argument("-d", "--disconnect", action="store_true",
                        help="Disconnect from all remove managers at end")
    parser.add_argument("-s", "--scan", action="store", const='*', nargs='?',
                        help="Run scan for bugs on remote managers (optional comma delimited host list)")
    parser.add_argument("-i", "--info", action="store_true",
                        help="Show info about the connected managers and testers")
    parser.add_argument("-b", "--bugs", nargs='*', type=int,
                        help="Bugs to test")
    parser.add_argument("-p", "--priority", type=int, default=0,
                        help="Priority for specified bugs")

    subparsers = parser.add_subparsers(title='actions', dest='action')

    fetch_parser = subparsers.add_parser('fetch')
    fetch_parser.add_argument("-d", "--repo", dest="fetch_repo", action="store", type=Path,
                              help="Repository to work on")
    fetch_parser.add_argument("-n", "--dry-run", dest="fetch_dryrun", action="store_true",
                              help="Apply and commit all passing bugs on repo")
    fetch_parser.add_argument("-a", "--apply", dest="fetch_apply", action="store_true",
                              help="Apply and commit all passing bugs on repo")
    fetch_parser.add_argument("-r", "--resolve", dest="fetch_resolve", action="store_true",
                              help="Resolve all passing bugs on repo")
    return parser


OPTIONS: Any = None
fetch_datetimes = read_fetch_datetimes()
fetch_bugs_passed: list[tuple[int, str]] = []
statuses: dict[str, messages.ManagerStatus] = {}


async def main():
    if OPTIONS.connect:
        await connect()

    await asyncio.gather(*map(manager_communicate, comm_dir.iterdir()))

    if OPTIONS.action == 'fetch' and not OPTIONS.fetch_dryrun and HAVE_NATTKA:
        if fetch_bugs_passed and OPTIONS.fetch_apply and OPTIONS.fetch_repo:
            apply_passes(fetch_bugs_passed)
        with fetch_datetime_file.open('w') as file:
            file.writelines((f'{host}={date.isoformat()}\n' for host, date in fetch_datetimes.items()))

    if OPTIONS.disconnect:
        await disconnect()

    if OPTIONS.info:
        for host, status in statuses.items():
            print(f'{host}:')
            print('|')
            print(f'+-- CPUs: {status.cpu_count}')
            print(f'+-- Load: {status.load[0]:.2f} ({100 * status.load[0] / status.cpu_count:.2f}%)')
            for tester, tester_status in status.testers.items():
                print(f'+-- "{tester.name}" of arch {tester.arch}:')
                print('|   |')
                print(f'|   +-- Queue (size {len(tester_status.bugs_queue)})')
                if tester_status.bugs_queue:
                    print(f'|   |   {", ".join(map(str, tester_status.bugs_queue[:7]))}')
                print('|   +-- Running emerge jobs')
                if tester_status.merging_atoms:
                    print('|       |')
                    for job in tester_status.merging_atoms:
                        print(f'|       +-- {job}')

if __name__ == '__main__':
    OPTIONS = argv_parser().parse_args()
    asyncio.run(main())
