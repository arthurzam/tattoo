import socket
import os
import logging

def sdnotify(state: str):
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            addr = os.getenv('NOTIFY_SOCKET')
            if addr is None:
                logging.info('NOTIFY_SOCKET is not set')
                return
            if addr[0] == '@':
                addr = '\0' + addr[1:]
            sock.connect(addr)

            sock.sendall(bytes(state, 'utf-8'))
    except Exception as exc:
        logging.error('failed to send notification to systemd', exc_info=exc)
