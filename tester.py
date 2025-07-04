#!/usr/bin/env python

import asyncio
import contextlib
import json
import logging
import os
import re
import signal
import socket
import subprocess
import warnings
from argparse import ArgumentParser
from pathlib import Path
from random import shuffle
from time import sleep
from typing import Any, Callable, Iterator

import bugs_fetcher
import messages
from bugs_queue import BugsQueue, BugsQueueItem
from sdnotify import sdnotify, set_logging_format

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    warnings.warn('psutil not found - install "dev-python/psutil"')
    HAS_PSUTIL = False

testing_dir = Path('/tmp/run')
logs_dir = Path.home() / 'logs'
failure_collection_dir = logs_dir / 'failures'
pkgdev_template = str(Path(__file__).parent / 'pkgdev.tatt.template.sh')


class IrkerSender(asyncio.DatagramProtocol):
    IRC_CHANNEL = "#gentoo-tattoo"

    def __init__(self, identifier: str, bugno: int, msg: str):
        irker_spigot = f"ircs://irc.libera.chat:6697/{IrkerSender.IRC_CHANNEL}"
        message = f"\x0314[{identifier}]: \x0307bug #{bugno}\x0F - {msg}"
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


def collect_failure_text(report_file: Path) -> str:
    report = tuple(parse_report_file(report_file))
    failures: list[str] = []
    for run in report:
        if run.get('result', '').lower() != 'true':
            atom = run['atom']
            if failure_str := run.get('failure_str', None):
                failures.append(f'   {atom} special fail: {failure_str}')
            elif 'test' in run['features']:
                failures.append(f'   {atom} test run failed')
            elif useflags := run['useflags']:
                failures.append(f'   {atom} USE flag run failed: [{useflags}]')
            else:
                failures.append(f'   {atom} default USE failed')
    return f"fail ({len(failures)} fails / {len(report)} runs):\n" + "\n".join(failures)


def preexec():
    signal.signal(signal.SIGCHLD, signal.SIG_DFL)
    os.setpgrp()


async def monitor_hang_job(pid: int, bug_no: int):
    """Monitor job's children pids and terminate if they don't change for a while.

    The detection logic is based on the assumption that the job is running emerge,
    and for 6 hours the children pids didn't change. You can adjust the timeout
    using the HANG_TIMEOUT_SECS environment variable.
    In worst scenario, we will have ~12h to detect the hang.
    """
    try:
        if not HAS_PSUTIL:
            return True

        duration = int(os.getenv('HANG_TIMEOUT_SECS', str(6 * 3600))) # 6h

        await asyncio.sleep(10 * 60) # 10m - initial wait
        prev_pids = []
        while True:
            try:
                proc = psutil.Process(pid)
            except psutil.NoSuchProcess:
                return True
            children = [p.pid for p in proc.children(recursive=True)]
            if children == prev_pids:
                logging.error('job %d is hung', bug_no)
                proc.terminate()
                return False
            prev_pids = children
            await asyncio.sleep(duration)
    except asyncio.CancelledError:
        return True


async def test_run(writer: Callable[[Any], Any], bug_no: int) -> str:
    logging.info('testing %d - pkgdev tatt', bug_no)
    args = (
        f'--bug={bug_no}',
        '--job-name={BUGNO}',
        f'--template-file={pkgdev_template}',
        f"--logs-dir={str(logs_dir)}",
        "--emerge-opts=--autounmask-keep-keywords=y --autounmask-use=y --autounmask-continue --autounmask-write",
        "--ignore-prefixes=elibc_,video_cards_,linguas_,python_targets_,python_single_target_,kdeenablefinal,test,debug,qemu_user_,qemu_softmmu_,libressl,static-libs,systemd,sdjournal,elogind,doc,ruby_targets_,default-libcxx,headers-only",
    )
    if key := bugs_fetcher.read_api_key():
        args += (f'--api-key={key}', )
    if (conf := Path(__file__).parent / 'pkgdev.tattoo.conf').exists():
        args += (f'--config={str(conf)}', )
    proc = await asyncio.create_subprocess_exec(
        'pkgdev', 'tatt', *args,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        preexec_fn=preexec,
        cwd=testing_dir,
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=60)
    except asyncio.TimeoutError:
        logging.error('`pkgdev tatt -b %d` timed out', bug_no)
        return 'tatt timed out'
    if proc.returncode != 0:
        try:
            if b'request due to maintenance downtime or capacity' in stdout:
                logging.error('failed with `tatt -b %d` - bugzilla rate limit', bug_no)
                return 'tatt failed with bugzilla rate'
            (dst_failure := failure_collection_dir / f'{bug_no}.tatt-failure.log').write_bytes(stdout)
            logging.error('failed with `pkgdev tatt -b %d` - log saved at %s', bug_no, dst_failure)
        except Exception as exc:
            logging.error('failed with `pkgdev tatt -b %d`, but saving log to file failed', exc_info=exc)
        return 'tatt failed'

    try:
        logging.info('testing %d - test run', bug_no)
        proc = await asyncio.create_subprocess_exec(
            testing_dir / f'{bug_no}.sh',
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            preexec_fn=preexec,
            cwd=testing_dir,
        )
        monitor = asyncio.create_task(monitor_hang_job(proc.pid, bug_no))
        exit_code = await proc.wait()
        monitor.cancel()
        if exit_code != 0:
            await writer(messages.BugJobDone(bug_number=bug_no, success=False))
            return collect_failure_text(testing_dir / f'{bug_no}.report')
        await writer(messages.BugJobDone(bug_number=bug_no, success=True))
        return ''
    finally:
        logging.info('testing %d - cleanup', bug_no)
        proc = await asyncio.create_subprocess_exec(
            testing_dir / f'{bug_no}.sh', '--clean',
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
        atom.group('atom')
        for line in stdout.splitlines()
        if (atom := atom_re.match(line.decode()))
    )


def queue_append_bugs(queue: BugsQueue, worker: messages.Worker, job: messages.GlobalJob):
    for _, bugs in bugs_fetcher.collect_bugs(job.bugs, worker):
        bugs = list(frozenset(bugs).difference(queue.bugs, queue.running))
        shuffle(bugs)
        for bug_no in bugs:
            logging.info('Queuing %d', bug_no)
            queue.put_nowait(BugsQueueItem(bug=bug_no, priority=job.priority))


async def handler(worker: messages.Worker, jobs_count: int):
    reader, writer = await asyncio.open_unix_connection(path=messages.SOCKET_FILENAME)
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
    parser.add_argument("-n", "--name", action="store", default=socket.gethostname(),
                        help="name for the tester, easy to identify")
    parser.add_argument("-a", "--arch", action="store", default=os.getenv('ARCH'),
                        help="Gentoo's arch name. Prepend with ~ for keywording")
    parser.add_argument("-j", "--jobs", type=int, action="store", default=1,
                        help="Amount of simultaneous testing jobs")
    options = parser.parse_args()

    if not options.arch:
        parser.error("You must specify --arch or set $ARCH environment variable")

    if not os.path.exists(messages.SOCKET_FILENAME):
        logging.error("%s socket doesn't exist", messages.SOCKET_FILENAME)
        return

    logging.info('Starting tester %r for arch %r', options.name, options.arch)

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
