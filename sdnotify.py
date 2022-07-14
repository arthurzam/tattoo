import asyncio
import socket
import os
import logging

SYSTEMD_FIRST_SOCKET_FD = 3

def set_logging_format():
    is_systemd = os.getenv('NOTIFY_SOCKET') is not None
    if is_systemd:
        logging.basicConfig(format='[{levelname}] {message}', style='{', level=logging.INFO)
    else:
        logging.basicConfig(format='{asctime} | [{levelname}] {message}', style='{', level=logging.INFO, datefmt="%Y-%m-%d %H:%M:%S")


def sdnotify(state: str):
    if addr := os.getenv('NOTIFY_SOCKET'):
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
                if addr[0] == '@':
                    addr = '\0' + addr[1:]
                sock.connect(addr)

                sock.sendall(bytes(state, 'utf-8'))
        except Exception as exc:
            logging.error('failed to send notification to systemd', exc_info=exc)


async def socket_activated_server(handler, path: str) -> asyncio.Server:
    if os.getenv("LISTEN_PID") == str(os.getpid()) and os.getenv("LISTEN_FDS") == "1":
        logging.info('Using systemd socket activated socket')
        sock = socket.fromfd(SYSTEMD_FIRST_SOCKET_FD, socket.AF_UNIX, socket.SOCK_STREAM)
        return await asyncio.start_unix_server(handler, sock=sock)
    else:
        if os.path.exists(path):
            os.remove(path)
        server = await asyncio.start_unix_server(handler, path=path)
        os.chmod(path, 0o666)
        return server
