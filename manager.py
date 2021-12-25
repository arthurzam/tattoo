import asyncio
import os
import typing

from db import DB
import messages


loop = asyncio.get_event_loop()
workers: typing.Dict[messages.Worker, asyncio.StreamWriter] = {}

db = DB()


async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    worker = messages.Worker(name='', arch='')
    try:
        while True:
            if data := await reader.readuntil(b'\n'):
                data = messages.load(data)
                if isinstance(data, messages.Worker):
                    if data.arch:
                        worker = data
                        workers[data] = writer
                        print(worker.name, 'of type', worker.arch, 'connected')
                elif isinstance(data, messages.GlobalJob):
                    print(data.bugs)
                    for writer in workers.values():
                        writer.write(messages.dump(data))
                        await writer.drain()
                elif isinstance(data, messages.BugJobDone):
                    db.report_job(worker, data)
                    if data.success:
                        print(f'{data.bug_number},{worker.canonical_arch()}')
                elif isinstance(data, messages.CompletedJobsRequest):
                    writer.write(messages.dump(db.get_reportes(data.since)))
                    await writer.drain()
                elif isinstance(data, messages.DoScan):
                    pass # TODO: implement this
            else:
                break

        writer.close()
        await writer.wait_closed()
    except asyncio.exceptions.IncompleteReadError:
        pass
    except ConnectionResetError:
        print('Closed')
    
    workers.pop(worker, None)

def main():
    if os.path.exists(messages.socket_filename):
        os.remove(messages.socket_filename)
    loop.run_until_complete(asyncio.start_unix_server(handler, path=messages.socket_filename))
    os.chmod(messages.socket_filename, 0o666)
    loop.run_forever()

if __name__ == '__main__':
    main()
