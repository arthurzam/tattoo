#!/usr/bin/env python

import asyncio
import logging
import os

import bugs_fetcher
import messages
from db import DB
from sdnotify import sdnotify, set_logging_format, socket_activated_server

workers: dict[messages.Worker, asyncio.StreamWriter] = {}
workers_status: dict[messages.Worker, asyncio.Future] = {}

db = DB()

async def process_bugs(job: messages.GlobalJob):
    logging.info('processing bugs %s', job.bugs)
    for worker, bugs in bugs_fetcher.collect_bugs(job.bugs, *workers.keys()):
        logging.info('sent to %s bugs %s', worker.name, bugs)
        workers[worker].write(messages.dump(messages.GlobalJob(priority=job.priority, bugs=bugs)))
        await workers[worker].drain()
    logging.info('finished processing bugs')

async def do_scan(trigger: str):
    logging.info('started %s scan for new bugs', trigger)
    for worker, bugs in bugs_fetcher.collect_bugs((), *workers.keys()):
        if bugs := list(db.filter_not_tested(worker.canonical_arch(), frozenset(bugs))):
            logging.info('sent to %s bugs %s', worker.name, bugs)
            workers[worker].write(messages.dump(messages.GlobalJob(priority=100, bugs=bugs)))
            await workers[worker].drain()
    logging.info('finished %s scan for new bugs', trigger)

async def periodic_keepalive(writer: asyncio.StreamWriter):
    try:
        while not writer.is_closing():
            writer.write(messages.dump(None))
            await writer.drain()
            await asyncio.sleep(600)
    except asyncio.CancelledError:
        pass

async def get_status():
    if workers:
        for worker in workers:
            workers_status[worker] = asyncio.get_running_loop().create_future()
        for worker, writer in workers.items():
            writer.write(messages.dump(messages.GetStatus()))
            await writer.drain()
        worker_names, statuses = tuple(zip(*workers_status.items()))
        statuses = dict(zip(worker_names, await asyncio.gather(*statuses)))
    else:
        statuses = {}
    return messages.ManagerStatus(
        load=os.getloadavg(),
        cpu_count=os.cpu_count(),
        testers=statuses,
    )

async def auto_scan():
    while True:
        await asyncio.sleep(14400) # 4h = 4 * 60 * 60s

        status = await get_status()
        if not status.testers:
            logging.warning("Self scan skipped because no testers are connected")
            continue
        if any(t.bugs_queue for t in status.testers.values()):
            logging.warning("Self scan skipped because tester's queues aren't empty")
            continue
        while (load := 100 * os.getloadavg()[0] / os.cpu_count()) > 50:
            logging.warning("Self scan postponed because of high load (%.2f%%)", load)
            await asyncio.sleep(1200) # 20m = 20 * 60s

        await do_scan("self")

async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    worker = messages.Worker(name='', arch='')
    keepaliver = None
    try:
        while True:
            try:
                data = await reader.readline()
                if not data:
                    break
            except asyncio.IncompleteReadError as exc:
                logging.info('pos')
                data = exc.partial
            if data:
                data = messages.load(data)
                if isinstance(data, messages.Worker):
                    if data.arch:
                        worker = data
                        workers[data] = writer
                        logging.info('%s of arch %s connected', worker.name, worker.arch)
                        keepaliver = asyncio.ensure_future(periodic_keepalive(writer))
                elif isinstance(data, messages.GlobalJob):
                    asyncio.ensure_future(process_bugs(data))
                elif isinstance(data, messages.BugJobDone):
                    logging.debug('done %d,%s', data.bug_number, worker.canonical_arch())
                    db.report_job(worker, data)
                elif isinstance(data, messages.CompletedJobsRequest):
                    writer.write(messages.dump(db.get_reportes(data.since)))
                    await writer.drain()
                elif isinstance(data, messages.DoScan):
                    asyncio.ensure_future(do_scan("manuel"))
                elif isinstance(data, messages.TesterStatus):
                    workers_status.pop(worker).set_result(data)
                elif isinstance(data, messages.GetStatus):
                    writer.write(messages.dump(await get_status()))
                    await writer.drain()

        if worker.name:
            logging.info('[%s] normal connection closed', worker.name)
    except asyncio.IncompleteReadError as exc:
        logging.warning('[%s] IncompleteReadError', worker.name, exc_info=exc)
    except ConnectionResetError:
        logging.warning('[%s] ConnectionResetError', worker.name)
    finally:
        writer.close()
        await writer.wait_closed()

    if keepaliver:
        keepaliver.cancel()
    if worker.name:
        logging.warning('Tester [%s] was disconnected', worker.name)
    workers.pop(worker, None)


async def main():
    try:
        server = await socket_activated_server(handler, messages.SOCKET_FILENAME)
        sdnotify('READY=1')
        asyncio.ensure_future(auto_scan())
        await server.serve_forever()
    except KeyboardInterrupt:
        logging.info('Caught a CTRL + C, good bye')
        sdnotify('STOPPING=1')

if __name__ == '__main__':
    set_logging_format()
    asyncio.run(main())
