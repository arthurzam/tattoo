#!/usr/bin/env python

from typing import Any, Callable
from argparse import ArgumentParser
from time import sleep
import contextlib
import subprocess
import asyncio
import socket
import json
import os

import bugs_fetcher
import messages

import logging
logging.basicConfig(format='%(asctime)s - %(message)s', level=logging.NOTSET)

testing_dir = '/tmp/run'

class IrkerSender(asyncio.DatagramProtocol):
    IRC_CHANNEL = "#gentoo-arthurzam"

    def __init__(self, bugno: int, msg: str):
        irker_spigot = f"ircs://irc.libera.chat:6697/{IrkerSender.IRC_CHANNEL}"
        message = f"\x0314[{options.name}]: \x0305bug #{bugno}\x0F - {msg}"
        self.message = json.dumps({"to": irker_spigot, "privmsg": message}).encode("utf8")

    def connection_made(self, transport):
        transport.sendto(self.message)
        transport.close()

    def error_received(self, exc):
        logging.error('send to irker failed', exc_info=exc)

    @staticmethod
    async def send_message(bugno: int, msg: str):
        await asyncio.get_event_loop().create_datagram_endpoint(
            lambda: IrkerSender(bugno, msg),
            remote_addr=("127.0.0.1", 6659)
        )


def collect_zombies():
    with contextlib.suppress(Exception):
        while True:
            cpid, _ = os.waitpid(-1, os.WNOHANG)
            if cpid == 0:
                break


async def test_run(writer: Callable[[Any], Any], bug_no: int) -> str:
    async with jobs_semaphore:
        proc = await asyncio.create_subprocess_exec(
            'tatt', '-b', str(bug_no), '-j', str(bug_no),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            preexec_fn=os.setpgrp,
            cwd=testing_dir,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode != 0:
            logging.error('failed with tatt -b %d\n%s', bug_no, stdout)
            return 'tatt failed'

        await writer(messages.LogMessage(worker, f'Started testing of bug #{bug_no}'))
        proc = await asyncio.create_subprocess_exec(
            f'/tmp/run/{bug_no}-useflags.sh',
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            preexec_fn=os.setpgrp,
            cwd=testing_dir,
        )
        if 0 != await proc.wait():
            await writer(messages.BugJobDone(bug_number=bug_no, success=False))
            return 'fail'
        await writer(messages.BugJobDone(bug_number=bug_no, success=True))

    proc = await asyncio.create_subprocess_exec(
        f'/tmp/run/{bug_no}-cleanup.sh',
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        preexec_fn=os.setpgrp,
        cwd=testing_dir,
    )
    await proc.wait()
    return ''


async def handle_bug_job(writer: Callable[[Any], Any], bug_no: int) -> None:
    try:
        result = await test_run(writer, bug_no)
        collect_zombies()
        await IrkerSender.send_message(bug_no, result or 'success')
    except Exception as e:
        result = f'error: {e}'
    with contextlib.suppress(Exception):
        await writer(messages.LogMessage(worker, f'Finished testing of bug #{bug_no} {result}'))


async def handler():
    logging.info('connecting')
    reader, writer = await asyncio.open_unix_connection(path=messages.socket_filename)
    def writer_func(obj: Any):
        writer.write(messages.dump(obj))
        return writer.drain()

    writer.write(messages.dump(worker))
    await writer.drain()
    try:
        while True:
            if data := await reader.readuntil(b'\n'):
                data = messages.load(data)
                if isinstance(data, messages.GlobalJob):
                    await writer_func(messages.LogMessage(worker, f'Got job requests {data.bugs}'))
                    try:
                        for _, bugs in bugs_fetcher.collect_bugs(data.bugs, worker):
                            await writer_func(messages.LogMessage(worker, f'Will test {bugs}'))
                            for bug_no in bugs:
                                logging.debug(bug_no)
                                asyncio.ensure_future(handle_bug_job(writer_func, bug_no))
                    except Exception as e:
                        logging.error('GlobalJob', exc_info=e)
                        await writer_func(messages.LogMessage(worker, f'Failed with: {e}'))
            else:
                logging.debug('closing')
    except asyncio.exceptions.IncompleteReadError:
        logging.warning('IncompleteReadError')
    except ConnectionResetError:
        logging.warning('ConnectionResetError')
    except Exception as e:
        logging.error('Unknown', exc_info=e)
    finally:
        with contextlib.suppress(Exception):
            writer.close()
            await writer.wait_closed()
        logging.debug('closing')

parser = ArgumentParser()
parser.add_argument("-n", "--name", dest="name", action="store", required=True,
                    help="name for the tester, easy to identify")
parser.add_argument("-a", "--arch", dest="arch", action="store", required=True,
                    help="Gentoo's arch name. Prepend with ~ for keywording")
parser.add_argument("-j", "--jobs", dest="jobs", type=int, action="store", default=2,
                    help="Amount of simultaneous testing jobs")
options = parser.parse_args()

jobs_semaphore = asyncio.Semaphore(options.jobs)
worker = messages.Worker(name=options.name, arch=options.arch)

if not os.path.exists(testing_dir):
    os.mkdir(testing_dir)

asyncio.set_event_loop(loop := asyncio.new_event_loop())
if os.path.exists(messages.socket_filename):
    for _ in range(5):
        try:
            loop.run_until_complete(handler())
        except:
            sleep(0.5)
