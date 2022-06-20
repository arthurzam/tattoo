import socket
import os
import logging


def set_logging_format():
    is_systemd = os.getenv('NOTIFY_SOCKET') is not None
    if is_systemd:
        logging.basicConfig(format='[{levelname}] {message}', style='{', level=logging.INFO)
    else:
        logging.basicConfig(format='{asctime} | [{levelname}] {message}', style='{', level=logging.INFO, datefmt="%Y-%m-%d %H:%M:%S")
        logging.info('NOTIFY_SOCKET is not set')


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
