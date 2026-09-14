import socket
import threading
import time
import http.client
import pytest
import uvicorn
from fastapi import FastAPI,Request
from api.serve import bind_listeners
from api.security import Identity,enforce_listener_role
from shared.config import settings
from shared.panel_ports import listener_ports


def test_shared_default_and_invalid_ports(monkeypatch):
    monkeypatch.setattr(settings,'api_bind_port',2222)
    monkeypatch.setattr(settings,'api_customer_port',None)
    assert listener_ports()==(2222,2222)
    for value in (22,0,65536,True,'2222'):
        monkeypatch.setattr(settings,'api_customer_port',value)
        with pytest.raises(ValueError):listener_ports()


def test_shared_listener_is_bound_once():
    listeners=bind_listeners('127.0.0.1',(0,0))
    try:assert len(listeners)==1
    finally:
        for listener in listeners:listener.close()


def test_failed_second_binding_closes_first_socket():
    blocker=socket.socket();blocker.bind(('127.0.0.1',0));blocker.listen()
    first=socket.socket();first.bind(('127.0.0.1',0));port=first.getsockname()[1];first.close()
    try:
        with pytest.raises(OSError):bind_listeners('127.0.0.1',(port,blocker.getsockname()[1]))
        check=socket.socket()
        try:check.bind(('127.0.0.1',port))
        finally:check.close()
    finally:blocker.close()


def test_real_dual_listener_roles_ignore_host_header(monkeypatch):
    sockets=[]
    for _ in range(2):
        s=socket.socket();s.bind(('127.0.0.1',0));sockets.append(s)
    ports=[s.getsockname()[1] for s in sockets]
    monkeypatch.setattr(settings,'api_bind_port',ports[0])
    monkeypatch.setattr(settings,'api_customer_port',ports[1])
    app=FastAPI()
    @app.get('/{role}')
    def probe(role:str,request:Request):
        identity=Identity(panel_user_id=1,username='test',role=role,account_id=1,auth_method='session')
        enforce_listener_role(identity,request)
        return {'role':role,'port':request.scope['server'][1]}
    server=uvicorn.Server(uvicorn.Config(app,log_level='error',lifespan='off',proxy_headers=False))
    thread=threading.Thread(target=server.run,kwargs={'sockets':sockets},daemon=True);thread.start()
    try:
        deadline=time.monotonic()+10
        while not server.started and thread.is_alive() and time.monotonic()<deadline:time.sleep(.02)
        assert server.started
        for port,role in zip(ports,('admin','customer')):
            for requested in ('admin','customer'):
                client=http.client.HTTPConnection('127.0.0.1',port,timeout=5)
                try:
                    client.request('GET','/'+requested,headers={'Host':f'fake.example:{ports[1] if port==ports[0] else ports[0]}'})
                    response=client.getresponse();response.read()
                    assert response.status==(200 if requested==role else 403)
                finally:client.close()
    finally:
        server.should_exit=True;thread.join(timeout=10)
        for s in sockets:s.close()
    assert not thread.is_alive()


@pytest.mark.parametrize('mode',['token','session'])
def test_existing_authentication_enforces_listener_role(monkeypatch,mode):
    from api import security
    from fastapi import HTTPException
    identity=Identity(panel_user_id=1,username='admin',role='admin',account_id=None,auth_method=mode)
    monkeypatch.setattr(settings,'api_bind_port',2222);monkeypatch.setattr(settings,'api_customer_port',3333)
    monkeypatch.setattr(security,'_identity_from_bearer_token',lambda token:identity)
    monkeypatch.setattr(security,'_identity_from_session_cookie',lambda cookie:identity)
    request=Request({'type':'http','server':('127.0.0.1',3333),'client':('127.0.0.1',1000),'headers':[(b'host',b'panel.example:2222')]})
    with pytest.raises(HTTPException) as error:
        security.get_identity(request,authorization='Bearer test' if mode=='token' else None,fh_session='test' if mode=='session' else None)
    assert error.value.status_code==403


def test_reseller_uses_admin_listener(monkeypatch):
    monkeypatch.setattr(settings,'api_bind_port',2222);monkeypatch.setattr(settings,'api_customer_port',3333)
    identity=Identity(panel_user_id=1,username='seller',role='reseller',account_id=None,auth_method='session')
    admin_request=Request({'type':'http','server':('127.0.0.1',2222),'client':('127.0.0.1',1000),'headers':[]})
    enforce_listener_role(identity,admin_request)
    customer_request=Request({'type':'http','server':('127.0.0.1',3333),'client':('127.0.0.1',1000),'headers':[]})
    with pytest.raises(Exception) as error:
        enforce_listener_role(identity,customer_request)
    assert error.value.status_code==403


def test_customer_listener_is_firewall_protected(monkeypatch):
    from daemon.firewall import protected_ports
    monkeypatch.setattr(settings,'api_customer_port',3333)
    assert 3333 in protected_ports()
