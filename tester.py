#!/usr/bin/env python

from random import shuffle
from typing import Any, Callable, Iterator
from argparse import ArgumentParser
from time import sleep
from pathlib import Path
import contextlib
import subprocess
import logging
import asyncio
import signal
import json
import os

import bugs_fetcher
import messages

logging.basicConfig(format='{asctime} | [{levelname}] {message}', style='{', level=logging.INFO)

testing_dir = Path('/tmp/run')
failure_collection_dir = Path.home() / 'logs/failures'
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


def parse_report_file(report_file: Path) -> Iterator[str]:
    try:
        report = json.loads(report_file.read_text(encoding='utf8').strip()[:-1] + ']}')
    except json.JSONDecodeError:
        yield 'JSON decoding failed'
        return
    for run in report['runs']:
        if not run['result']:
            atom = run['atom']
            if failure_str := run.get('failure_str', None):
                yield f'   {atom} special fail: {failure_str}'
            elif 'test' in run['features']:
                yield f'   {atom} test run failed'
            elif useflags := run['useflags']:
                yield f'   {atom} USE flag run failed: [{useflags.strip()}]'
            else:
                yield f'   {atom} default USE failed'


def preexec():
    signal.signal(signal.SIGCHLD, signal.SIG_DFL)
    os.setpgrp()


async def test_run(writer: Callable[[Any], Any], bug_no: int) -> str:
    logging.info('testing %d - tatt', bug_no)
    proc = await asyncio.create_subprocess_exec(
        'tatt', '-b', str(bug_no), '-j', str(bug_no),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        preexec_fn=preexec,
        cwd=testing_dir,
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=60)
    except asyncio.TimeoutError:
        logging.error('`tatt -b %d` timed out', bug_no)
        return 'tatt timed out'
    if proc.returncode != 0:
        try:
            if b'request due to maintenance downtime or capacity' in stdout:
                logging.error('failed with `tatt -b %d` - bugzilla rate limit', bug_no)
                return 'tatt failed with bugzilla rate'
            (dst_failure := failure_collection_dir / f'{bug_no}.tatt-failure.log').write_bytes(stdout)
            logging.error('failed with `tatt -b %d` - log saved at %s', bug_no, dst_failure)
        except Exception as exc:
            logging.error('failed with `tatt -b %d`, but saving log to file failed', exc_info=exc)
        return 'tatt failed'

    logging.info('testing %d - test run', bug_no)
    await writer(messages.LogMessage(worker, f'Started testing of bug #{bug_no}'))
    proc = await asyncio.create_subprocess_exec(
        testing_dir / f'{bug_no}-useflags.sh',
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        preexec_fn=preexec,
        cwd=testing_dir,
    )
    if 0 != await proc.wait():
        await writer(messages.BugJobDone(bug_number=bug_no, success=False))
        return 'fail\n' + '\n'.join(parse_report_file(testing_dir / f'{bug_no}.report'))
    await writer(messages.BugJobDone(bug_number=bug_no, success=True))

    logging.info('testing %d - cleanup', bug_no)
    proc = await asyncio.create_subprocess_exec(
        f'/tmp/run/{bug_no}-cleanup.sh',
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        preexec_fn=preexec,
        cwd=testing_dir,
    )
    await proc.wait()
    return ''


async def worker_func(queue: asyncio.Queue, writer: Callable[[Any], Any]):
    with contextlib.suppress(asyncio.CancelledError):
        while True:
            bug_no: int = await queue.get()
            try:
                result = await test_run(writer, bug_no)
                await IrkerSender.send_message(bug_no, result or 'success')
            except asyncio.CancelledError:
                return
            except Exception as exc:
                result = f'error: {exc}'
            with contextlib.suppress(Exception):
                await writer(messages.LogMessage(worker, f'Finished testing of bug #{bug_no} {result}'))
            queue.task_done()


async def handler():
    logging.info('connecting')
    reader, writer = await asyncio.open_unix_connection(path=messages.socket_filename)
    def writer_func(obj: Any):
        writer.write(messages.dump(obj))
        return writer.drain()

    await writer_func(worker)

    queue = asyncio.Queue()
    tasks = [asyncio.create_task(worker_func(queue, writer_func), name=f'Tester {i + 1}') for i in range(options.jobs)]

    try:
        while data := await reader.readuntil(b'\n'):
            data = messages.load(data)
            if isinstance(data, messages.GlobalJob):
                await writer_func(messages.LogMessage(worker, f'Got job requests {data.bugs}'))
                try:
                    for _, bugs in bugs_fetcher.collect_bugs(data.bugs, worker):
                        await writer_func(messages.LogMessage(worker, f'Will test {bugs}'))
                        shuffle(bugs)
                        for bug_no in bugs:
                            logging.info('Queuing %d', bug_no)
                            await queue.put(bug_no)
                except Exception as exc:
                    logging.error('Running GlobalJob failed', exc_info=exc)
                    await writer_func(messages.LogMessage(worker, f'Failed with: {exc}'))
    except asyncio.exceptions.IncompleteReadError:
        logging.warning('IncompleteReadError')
    except ConnectionResetError:
        logging.warning('ConnectionResetError')
    except Exception as exc:
        logging.error('Unknown', exc_info=exc)
    finally:
        with contextlib.suppress(Exception):
            writer.close()
            await writer.wait_closed()
        for task in tasks:
            task.cancel()
        logging.info('closing')


parser = ArgumentParser()
parser.add_argument("-n", "--name", dest="name", action="store", required=True,
                    help="name for the tester, easy to identify")
parser.add_argument("-a", "--arch", dest="arch", action="store", required=True,
                    help="Gentoo's arch name. Prepend with ~ for keywording")
parser.add_argument("-j", "--jobs", dest="jobs", type=int, action="store", default=2,
                    help="Amount of simultaneous testing jobs")
options = parser.parse_args()

# signal.signal(signal.SIGCHLD, signal.SIG_IGN)

worker = messages.Worker(name=options.name, arch=options.arch)

os.makedirs(testing_dir, exist_ok=True)
os.makedirs(failure_collection_dir, exist_ok=True)

asyncio.set_event_loop(loop := asyncio.new_event_loop())
if os.path.exists(messages.socket_filename):
    for _ in range(5):
        try:
            loop.run_until_complete(handler())
        except KeyboardInterrupt:
            logging.info('Caught a CTRL + C, good bye')
            break
        except Exception:
            sleep(0.5)
