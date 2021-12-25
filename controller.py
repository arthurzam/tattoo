from argparse import ArgumentParser
from typing import Dict, Iterator
from datetime import datetime
from shutil import rmtree
import asyncio
import os

import messages

base_dir = '/tmp/arch-tester/comm'


def collect_ssh_hosts() -> Iterator[str]:
    with open('ssh_config') as f:
        for row in f:
            if row.startswith('Host '):
                yield row.removeprefix('Host ').strip()


def fetch_datetimes() -> Dict[str, datetime]:
    fetch_datetime_file = 'controller.datetime.txt'
    res = {}
    with open(fetch_datetime_file) as f:
        for system, datetime_s in map(str.split('=', maxsplit=1), f):
            try:
                res[system] = datetime.fromisoformat(datetime_s)
            except:
                pass
    return res


async def run_ssh(*extra_args) -> bool:
    proc = await asyncio.create_subprocess_exec(
        'ssh', '-F', 'ssh_config', '-T', *extra_args,
        preexec_fn=os.setpgrp,
    )
    return 0 == await proc.wait()


async def handler(name: str):
    socket_file = base_dir + os.path.sep + name
    if not os.path.exists(socket_file):
        print(f"Error {socket_file}")
        return
    try:
        reader, writer = await asyncio.open_unix_connection(path=socket_file)
    except:
        print("Failed Connect to", name)
        return

    writer.write(messages.dump(messages.Worker(name='', arch='')))
    if options.bugs:
        writer.write(messages.dump(messages.GlobalJob(bugs=options.bugs)))
    if options.scan:
        writer.write(messages.dump(messages.DoScan()))
    await writer.drain()

    if options.action == 'fetch':
        now = datetime.utcnow()
        writer.write(messages.dump(messages.CompletedJobsRequest(since=fetch_datetimes.get(name, datetime.fromtimestamp(0)))))
        data = messages.load(await reader.readuntil(b'\n'))
        if isinstance(data, messages.CompletedJobsResponse):
            for bug_no, arch in data.passes:
                print(f'{bug_no},{arch}')
            if options.fetch_apply and not options.fetch_dryrun:
                pass # TODO: implement
                if options.fetch_resolve:
                    pass # TODO: implement
            fetch_datetimes[name] = now

    writer.close()
    await writer.wait_closed()

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
fetch_parser = subparsers.add_parser('fetch')
fetch_parser.add_argument("-d", "--repo", dest="fetch_repo", action="store",
                          help="Repository to work on")
fetch_parser.add_argument("-n", "--dry-run", dest="fetch_dryrun", action="store_true",
                          help="Apply and commit all passing bugs on repo")
fetch_parser.add_argument("-a", "--apply", dest="fetch_apply", action="store_true",
                          help="Apply and commit all passing bugs on repo")
fetch_parser.add_argument("-r", "--resolve", dest="fetch_resolve", action="store_true",
                          help="Resolve all passing bugs on repo")

options = parser.parse_args()


loop = asyncio.get_event_loop()
if options.connect:
    os.makedirs('/tmp/arch-tester/comm', exist_ok=True)
    for existing in os.listdir('/tmp/arch-tester/comm'):
        os.remove('/tmp/arch-tester/comm' + os.path.sep + existing)
    os.makedirs('/tmp/arch-tester/control', exist_ok=True)
    loop.run_until_complete(asyncio.gather(*(
        run_ssh('-fNM', host) for host in collect_ssh_hosts()
    )))

if options.action == 'fetch':
    fetch_datetimes: Dict[str, datetime] = {}
    try:
        with open('controller.datetime.txt') as f:
            for line in f:
                system, datetime_s = line.rstrip().split('=', maxsplit=2)
                try:
                    fetch_datetimes[system] = datetime.fromisoformat(datetime_s)
                except:
                    pass
    except:
        pass

loop.run_until_complete(asyncio.gather(*map(handler, os.listdir(base_dir))))

if options.action == 'fetch' and not options.fetch_dryrun:
    with open('controller.datetime.txt', 'w') as f:
        f.writelines((f'{host}={date.isoformat()}\n' for host, date in fetch_datetimes.items()))

if options.disconnect:
    loop.run_until_complete(asyncio.gather(*(
        run_ssh('-O', 'exit', host) for host in collect_ssh_hosts()
    )))
    from shutil import rmtree
    rmtree('/tmp/arch-tester/comm', ignore_errors=True)
    rmtree('/tmp/arch-tester/control', ignore_errors=True)
