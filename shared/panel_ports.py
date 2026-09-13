"""Shared listener configuration for startup, authorization and firewall rules."""
from shared.config import settings


def listener_ports():
    admin=settings.api_bind_port
    customer=settings.api_customer_port if settings.api_customer_port is not None else admin
    for port in (admin,customer):
        if type(port) is not int or not 1024<=port<=65535:
            raise ValueError('Panel ports must be integers between 1024 and 65535')
    return admin,customer
