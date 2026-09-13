"""Run one API process on shared or separate administrator/customer sockets."""
import socket
import uvicorn
from shared.config import settings
from shared.panel_ports import listener_ports


def bind_listeners(host,ports):
    sockets=[]
    try:
        for port in dict.fromkeys(ports):
            listener=socket.socket(socket.AF_INET6 if ':' in host else socket.AF_INET,socket.SOCK_STREAM)
            sockets.append(listener)
            listener.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
            if listener.family==socket.AF_INET6:listener.setsockopt(socket.IPPROTO_IPV6,socket.IPV6_V6ONLY,1)
            listener.bind((host,port))
            listener.set_inheritable(True)
        return sockets
    except Exception:
        for listener in sockets:listener.close()
        raise


def main():
    ports=listener_ports()
    config=uvicorn.Config('api.main:app',host=settings.api_bind_host,port=ports[0],
        ssl_keyfile='/etc/boron/ssl/api/panel.key',ssl_certfile='/etc/boron/ssl/api/panel.crt',
        timeout_graceful_shutdown=15,proxy_headers=False)
    sockets=bind_listeners(settings.api_bind_host,ports)
    try:uvicorn.Server(config).run(sockets=sockets)
    finally:
        for listener in sockets:listener.close()


if __name__=='__main__':main()
