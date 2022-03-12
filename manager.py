#!/usr/bin/env python

from typing import Dict, Optional
import asyncio
import os

from db import DB
import messages
import bugs_fetcher

import logging
logging.basicConfig(format='{asctime} | [{levelname}] {message}', style='{', level=logging.INFO)

loop = asyncio.get_event_loop()
workers: Dict[messages.Worker, asyncio.StreamWriter] = {}
follower: Optional[asyncio.StreamWriter] = None

db = DB()

async def do_scan():
    logging.info('started scan for new bugs')
    for worker, bugs in bugs_fetcher.collect_bugs([], *workers.keys()):
        if bugs := list(db.filter_not_tested(worker.canonical_arch(), bugs)):
            logging.info(f'sent to {worker.name} bugs {bugs}')
            workers[worker].write(messages.dump(messages.GlobalJob(bugs)))
            await workers[worker].drain()
    logging.info('finished scan for new bugs')

async def auto_scan(interval: int):
    while True:
        await asyncio.sleep(interval)
        await do_scan()

async def periodic_keepalive(writer: asyncio.StreamWriter):
    try:
        while not writer.is_closing():
            writer.write(messages.dump(None))
            await writer.drain()
            await asyncio.sleep(600)
    except asyncio.CancelledError:
        pass

async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    worker = messages.Worker(name='', arch='')
    is_follower = False
    keepaliver = None
    try:
        while True:
            try:
                data = await reader.readline()
                if not data:
                    break
            except asyncio.exceptions.IncompleteReadError as e:
                logging.info('pos')
                data = e.partial
            if data:
                data = messages.load(data)
                if isinstance(data, messages.Worker):
                    if data.arch:
                        worker = data
                        workers[data] = writer
                        logging.info('%s of %s connected', worker.name, worker.arch)
                        keepaliver = asyncio.ensure_future(periodic_keepalive(writer))
                elif isinstance(data, messages.Follower):
                    global follower
                    if is_follower := follower is None:
                        follower = writer
                elif isinstance(data, messages.GlobalJob):
                    logging.debug(f'got bugs {data.bugs}')
                    for worker, bugs in bugs_fetcher.collect_bugs(data.bugs, *workers.keys()):
                        logging.info(f'sent to {worker.name} bugs {bugs}')
                        workers[worker].write(messages.dump(messages.GlobalJob(bugs)))
                        await workers[worker].drain()
                elif isinstance(data, messages.BugJobDone):
                    logging.debug('done %d,%s', data.bug_number, worker.canonical_arch())
                    db.report_job(worker, data)
                elif isinstance(data, messages.CompletedJobsRequest):
                    writer.write(messages.dump(db.get_reportes(data.since)))
                    await writer.drain()
                elif isinstance(data, messages.DoScan):
                    asyncio.ensure_future(do_scan())
                elif isinstance(data, messages.GetLoad):
                    writer.write(messages.dump(messages.LoadResponse(*os.getloadavg())))
                    await writer.drain()
                elif isinstance(data, messages.LogMessage):
                    if follower:
                        follower.write(messages.dump(data))
                        await follower.drain()

        logging.warning('[%s] simple close', worker.name)
    except asyncio.exceptions.IncompleteReadError as e:
        logging.warning('[%s] IncompleteReadError', worker.name, exc_info=e)
    except ConnectionResetError:
        logging.warning('[%s] ConnectionResetError', worker.name)
    finally:
        writer.close()
        await writer.wait_closed()

    if keepaliver:
        keepaliver.cancel()
    if is_follower:
        follower = None
    if worker.name:
        logging.warning('[%s] we lost', worker.name)
    workers.pop(worker, None)


def main():
    if os.path.exists(messages.socket_filename):
        os.remove(messages.socket_filename)
    asyncio.set_event_loop(loop := asyncio.new_event_loop())
    loop.run_until_complete(asyncio.start_unix_server(handler, path=messages.socket_filename))
    os.chmod(messages.socket_filename, 0o666)
    # asyncio.ensure_future(auto_scan(3600))
    loop.run_forever()

if __name__ == '__main__':
    main()
