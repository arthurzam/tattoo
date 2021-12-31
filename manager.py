#!/usr/bin/env python

from typing import Dict
import asyncio
import os

from db import DB
import messages
import bugs_fetcher


loop = asyncio.get_event_loop()
workers: Dict[messages.Worker, asyncio.StreamWriter] = {}

db = DB()

async def do_scan():
    for worker, bugs in bugs_fetcher.collect_bugs([], *workers.keys()):
        if bugs := list(db.filter_not_tested(worker.canonical_arch(), bugs)):
            workers[worker].write(messages.dump(messages.GlobalJob(bugs)))
            await workers[worker].drain()

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
    keepaliver = None
    try:
        while True:
            if data := await reader.readuntil(b'\n'):
                data = messages.load(data)
                if isinstance(data, messages.Worker):
                    if data.arch:
                        worker = data
                        workers[data] = writer
                        print(worker.name, 'of type', worker.arch, 'connected')
                        keepaliver = asyncio.ensure_future(periodic_keepalive(writer))
                elif isinstance(data, messages.GlobalJob):
                    print('jobs:', data.bugs)
                    for worker, bugs in bugs_fetcher.collect_bugs(data.bugs, *workers.keys()):
                        workers[worker].write(messages.dump(messages.GlobalJob(bugs)))
                        await workers[worker].drain()
                elif isinstance(data, messages.BugJobDone):
                    print(f'{data.bug_number},{worker.canonical_arch()}')
                    db.report_job(worker, data)
                elif isinstance(data, messages.CompletedJobsRequest):
                    writer.write(messages.dump(db.get_reportes(data.since)))
                    await writer.drain()
                elif isinstance(data, messages.DoScan):
                    asyncio.ensure_future(do_scan())
                elif isinstance(data, messages.GetLoad):
                    writer.write(messages.dump(messages.LoadResponse(*os.getloadavg())))
                    await writer.drain()
            else:
                break

        writer.close()
        await writer.wait_closed()
    except asyncio.exceptions.IncompleteReadError:
        pass
    except ConnectionResetError:
        print('Closed')
    
    if keepaliver:
        keepaliver.cancel()
    workers.pop(worker, None)


def main():
    if os.path.exists(messages.socket_filename):
        os.remove(messages.socket_filename)
    loop.run_until_complete(asyncio.start_unix_server(handler, path=messages.socket_filename))
    os.chmod(messages.socket_filename, 0o666)
    # asyncio.ensure_future(auto_scan(3600))
    loop.run_forever()

if __name__ == '__main__':
    main()
