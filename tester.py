from argparse import ArgumentParser
from json import JSONEncoder
import subprocess
import asyncio
import socket
import os

from nattka.bugzilla import *

import messages


nattka_bugzilla = NattkaBugzilla(api_key=None)


def is_ready(bug: BugInfo) -> bool:
    if bug.resolved:
        return False
    if len(bug.depends) > 0:
        results = nattka_bugzilla.find_bugs(bugs=bug.depends, unresolved=True)
        if results.items():
            return False
    return True


def send_irker(bugno: int, msg: str):
    irker_listener = ("127.0.0.1", 6659)
    irker_spigot = "ircs://irc.libera.chat:6697/#gentoo-arthurzam"
    message = f"\x0314[{options.name}]: \x0305bug #{bugno}\x0F - {msg}"
    json_msg = JSONEncoder().encode({"to": irker_spigot, "privmsg": message})

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.sendto(json_msg.encode("utf8"), irker_listener)
    sock.close()


async def test_run(bugnum: int) -> bool:
    proc = await asyncio.create_subprocess_exec(
        'tatt', '-b', str(bugnum), '-j', str(bugnum),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        preexec_fn=os.setpgrp,
        cwd="/tmp/run"
    )
    if 0 != await proc.wait():
        send_irker(bugnum, 'tatt -b failed')
        return False

    async with sema:
        proc = await asyncio.create_subprocess_exec(
            f'/tmp/run/{bugnum}-useflags.sh',
            stdout=subprocess.DEVNULL,
            preexec_fn=os.setpgrp,
            cwd="/tmp/run"
        )
        if 0 != await proc.wait():
            send_irker(bugnum, 'fail')
            raise Exception('Unable to useflag')
        else:
            send_irker(bugnum, 'success')

    proc = await asyncio.create_subprocess_exec(
        f'/tmp/run/{bugnum}-cleanup.sh',
        stdout=subprocess.DEVNULL,
        preexec_fn=os.setpgrp,
        cwd="/tmp/run"
    )
    await proc.wait()
    return True


async def handle_bug_job(writer: asyncio.StreamWriter, bug_no: int) -> None:
    print('started', bug_no)
    writer.write(messages.dump(messages.BugJobDone(bug_number=bug_no, success=await test_run(bug_no))))
    await writer.drain()


async def handler():
    reader, writer = await asyncio.open_unix_connection(path=messages.socket_filename)

    writer.write(messages.dump(messages.Worker(name=options.name, arch=options.arch)))
    await writer.drain()
    try:
        while True:
            if data := await reader.readuntil(b'\n'):
                data = messages.load(data)
                if isinstance(data, messages.GlobalJob):
                    print(data.bugs)
                    bugs = nattka_bugzilla.find_bugs(
                        bugs=data.bugs,
                        unresolved=True,
                        sanity_check=[True],
                        cc=[f'{options.arch.removeprefix("~")}@gentoo.org']
                    )
                    for bug_no, bug in bugs.items():
                        if is_ready(bug) and (bug.category == BugCategory.KEYWORDREQ) == options.arch.startswith('~'):
                            asyncio.ensure_future(handle_bug_job(writer, bug_no))
            else:
                break
    except EOFError:
        pass
    except ConnectionResetError:
        pass

parser = ArgumentParser()
parser.add_argument("-n", "--name", dest="name", action="store",
                    help="name for the tester, easy to identify")
parser.add_argument("-a", "--arch", dest="arch", action="store",
                    help="Gentoo's arch name. Prepend with ~ for keywording")
parser.add_argument("-j", "--jobs", dest="jobs", type=int, action="store", default=2,
                    help="Amount of simultaneous testing jobs")
options = parser.parse_args()

if not options.name:
    parser.error('name not given')
if not options.arch:
    parser.error('arch not given')

sema = asyncio.BoundedSemaphore(options.jobs)

if not os.path.exists('/tmp/run'):
    os.mkdir('/tmp/run')

loop = asyncio.get_event_loop()
if os.path.exists(messages.socket_filename):
    loop.run_until_complete(handler())
