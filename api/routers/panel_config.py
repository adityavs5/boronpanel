"""Administrator configuration for shared or separate HTTPS panel listeners."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from api.rpc import call_daemon
from api.security import Identity, get_identity, require_admin

api_router = APIRouter(prefix='/api/v1/admin/panel-config', tags=['panel configuration'])


class PortsBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    admin_port: int = Field(strict=True, ge=1024, le=65535)
    customer_port: int = Field(strict=True, ge=1024, le=65535)
    confirm: bool = False


@api_router.get('')
def status(identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon('panel.config.status', identity)


@api_router.post('/ports', status_code=202)
def change_ports(body: PortsBody, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon('panel.config.start', identity, **body.model_dump(), initiated_by=identity.username)
