#!/usr/bin/env python

import asyncio
import contextlib
import json
import logging
import os
import re
import signal
import subprocess
from argparse import ArgumentParser
from pathlib import Path
from random import shuffle
from time import sleep
from typing import Any, Callable, Iterator

import bugs_fetcher
import messages
from bugs_queue import BugsQueue, BugsQueueItem
from sdnotify import sdnotify, set_logging_format

testing_dir = Path('/tmp/run')
failure_collection_dir = Path.home() / 'logs/failures'


class IrkerSender(asyncio.DatagramProtocol):
    IRC_CHANNEL = "#gentoo-tattoo"

    def __init__(self, identifier: str, bugno: int, msg: str):
        irker_spigot = f"ircs://irc.libera.chat:6697/{IrkerSender.IRC_CHANNEL}"
        message = f"\x0314[{identifier}]: \x0305bug #{bugno}\x0F - {msg}"
        self.message = json.dumps({"to": irker_spigot, "privmsg": message}).encode("utf8")

    def connection_made(self, transport):
        transport.sendto(self.message)
        transport.close()

    def error_received(self, exc):
        logging.error("send to irker failed", exc_info=exc)

    @staticmethod
    async def send_message(identifier: str, bugno: int, msg: str):
        await asyncio.get_event_loop().create_datagram_endpoint(
            lambda: IrkerSender(identifier, bugno, msg),
            remote_addr=("127.0.0.1", 6659)
        )


def parse_report_file(report_file: Path) -> Iterator[dict[str, str]]:
    try:
        lines = report_file.read_text().splitlines(keepends=False)
    except FileNotFoundError:
        return

    run: dict[str, str] = {}
    for line in map(str.strip, lines):
        if not line or line.startswith('#'):
            continue
        elif line == '---':
            if len(run) != 0:
                yield run
                run = {}
        elif ':' in line:
            key, value = line.split(':', maxsplit=1)
            run[key.strip()] = value.strip()
    if len(run) != 0:
        yield run
        run = {}


def collect_failures(report_file: Path) -> Iterator[str]:
    for run in parse_report_file(report_file):
        if run.get('result', '').lower() != 'true':
            atom = run['atom']
            if failure_str := run.get('failure_str', None):
                yield f'   {atom} special fail: {failure_str}'
            elif 'test' in run['features']:
                yield f'   {atom} test run failed'
            elif useflags := run['useflags']:
                yield f'   {atom} USE flag run failed: [{useflags}]'
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

    try:
        logging.info('testing %d - test run', bug_no)
        proc = await asyncio.create_subprocess_exec(
            testing_dir / f'{bug_no}-useflags.sh',
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            preexec_fn=preexec,
            cwd=testing_dir,
        )
        if 0 != await proc.wait():
            await writer(messages.BugJobDone(bug_number=bug_no, success=False))
            return 'fail\n' + '\n'.join(collect_failures(testing_dir / f'{bug_no}.report'))
        await writer(messages.BugJobDone(bug_number=bug_no, success=True))
        return ''
    finally:
        logging.info('testing %d - cleanup', bug_no)
        proc = await asyncio.create_subprocess_exec(
            f'/tmp/run/{bug_no}-cleanup.sh',
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            preexec_fn=preexec,
            cwd=testing_dir,
        )
        await proc.wait()


async def worker_func(worker: messages.Worker, queue: BugsQueue, writer: Callable[[Any], Any]):
    with contextlib.suppress(asyncio.CancelledError):
        while True:
            bug_no: int = await queue.get()
            try:
                result = await test_run(writer, bug_no)
                await IrkerSender.send_message(worker.name, bug_no, result or 'success')
            except asyncio.CancelledError:
                return
            except Exception as exc:
                logging.error('fail', exc_info=exc)
            queue.bug_done(bug_no)


async def running_emerge_jobs() -> tuple[str, ...]:
    try:
        proc = await asyncio.create_subprocess_exec(
            'qlop', '-r', '-F', '$%{CATEGORY}/%{PF}$',
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
    except FileNotFoundError:
        logging.error('qlop not found - install "app-portage/portage-utils"')
        return ('???', )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=20)
    except asyncio.TimeoutError:
        logging.error('`qlop -r` timed out')
        return ()
    if proc.returncode != 0:
        logging.error('`qlop -r` failed with:\n%s', stderr)
        return ()
    atom_re = re.compile(r'^.* >>> \$(?P<atom>.*)\$... .*$')
    return tuple(
        mo.group('atom')
        for line in stdout.splitlines()
        if (mo := atom_re.match(line.decode()))
    )


def queue_append_bugs(queue: BugsQueue, worker: messages.Worker, job: messages.GlobalJob):
    for _, bugs in bugs_fetcher.collect_bugs(job.bugs, worker):
        bugs = list(frozenset(bugs).difference(queue.bugs, queue.running))
        shuffle(bugs)
        for bug_no in bugs:
            logging.info('Queuing %d', bug_no)
            queue.put_nowait(BugsQueueItem(bug=bug_no, priority=job.priority))


async def handler(worker: messages.Worker, jobs_count: int):
    reader, writer = await asyncio.open_unix_connection(path=messages.socket_filename)
    def writer_func(obj: Any):
        writer.write(messages.dump(obj))
        return writer.drain()

    await writer_func(worker)

    queue = BugsQueue()
    tasks = [asyncio.create_task(worker_func(worker, queue, writer_func), name=f'Tester {i + 1}') for i in range(jobs_count)]

    sdnotify('READY=1')

    try:
        while data := await reader.readuntil(b'\n'):
            data = messages.load(data)
            if isinstance(data, messages.GlobalJob):
                try:
                    queue_append_bugs(queue, worker, data)
                except Exception as exc:
                    logging.error('Running GlobalJob failed', exc_info=exc)
            elif isinstance(data, messages.GetStatus):
                await writer_func(messages.TesterStatus(
                    bugs_queue=tuple(queue.running) + queue.bugs,
                    merging_atoms=await running_emerge_jobs(),
                ))
    except asyncio.IncompleteReadError:
        logging.warning('Abrupt connection closed')
    except ConnectionResetError:
        logging.warning('Abrupt connection reset')
    except Exception as exc:
        logging.error('General exception', exc_info=exc)
    finally:
        with contextlib.suppress(Exception):
            writer.close()
            await writer.wait_closed()
        for task in tasks:
            task.cancel()
        logging.info('closing')


def main():
    parser = ArgumentParser()
    parser.add_argument("-n", "--name", action="store", required=True,
                        help="name for the tester, easy to identify")
    parser.add_argument("-a", "--arch", action="store", required=True,
                        help="Gentoo's arch name. Prepend with ~ for keywording")
    parser.add_argument("-j", "--jobs", type=int, action="store", default=2,
                        help="Amount of simultaneous testing jobs")
    options = parser.parse_args()

    if not os.path.exists(messages.socket_filename):
        logging.error("%s socket doesn't exist", messages.socket_filename)
        return

    os.makedirs(testing_dir, exist_ok=True)
    os.makedirs(failure_collection_dir, exist_ok=True)

    worker = messages.Worker(name=options.name, arch=options.arch)

    asyncio.set_event_loop(loop := asyncio.new_event_loop())
    retry_counter = 0
    while retry_counter < 5:
        try:
            logging.info('connecting to manager')
            loop.run_until_complete(handler(worker, options.jobs))
            retry_counter = 0
        except KeyboardInterrupt:
            logging.info('Caught a CTRL + C, good bye')
            break
        except Exception:
            sleep(0.5)
            retry_counter += 1
        sdnotify('RELOADING=1')
    sdnotify('STOPPING=1')


if __name__ == '__main__':
    set_logging_format()
    main()
