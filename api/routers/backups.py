from __future__ import annotations

from fastapi import APIRouter, Depends, Form
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from starlette.requests import Request

from api.rpc import call_daemon
from api.security import Identity, get_identity, require_admin
from api.templates import templates

# Admin-level only: destinations/schedules are server-wide infra/policy,
# same trust level as suspend/terminate -- not a per-account self-service
# knob. Per-account trigger/browse/restore lives in account_backups.py
# (same split domains.py/dns.py already use for account-scoped vs.
# admin-scoped concerns under /accounts/{username}/...).
api_router = APIRouter(prefix="/api/v1/backups", tags=["backups"])
ui_router = APIRouter(prefix="/ui/backups", tags=["ui:backups"])


class CreateDestinationBody(BaseModel):
    name: str
    kind: str = "local"
    local_path: str | None = None
    rclone_remote_type: str | None = None
    rclone_config: dict = {}
    rclone_path_prefix: str = ""


@api_router.get("/destinations")
def list_destinations(identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("backup.destination.list", identity)


@api_router.post("/destinations")
def create_destination(body: CreateDestinationBody, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("backup.destination.create", identity, **body.model_dump())


@api_router.delete("/destinations/{destination_id}")
def delete_destination(destination_id: int, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("backup.destination.delete", identity, id=destination_id)


class SetScheduleBody(BaseModel):
    username: str | None = None
    frequency: str = "daily"
    retention_count: int = 7
    on_demand_retention: int = 7
    pre_restore_retention: int = 7
    freshness_hours: int = 36
    minimum_free_mb: int = 2048
    destination_id: int
    enabled: bool = True


@api_router.get("/schedules")
def list_schedules(identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("backup.schedule.list", identity)


@api_router.post("/schedules")
def set_schedule(body: SetScheduleBody, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("backup.schedule.set", identity, **body.model_dump())


@api_router.get("/jobs")
def list_all_jobs(username: str | None = None, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("backup.job.list", identity, username=username)


# --- server-rendered UI ------------------------------------------------------


@ui_router.get("")
def ui_admin_backups(request: Request, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    destinations = call_daemon("backup.destination.list", identity)["destinations"]
    schedules = call_daemon("backup.schedule.list", identity)["schedules"]
    jobs = call_daemon("backup.job.list", identity, username=None)["jobs"]
    return templates.TemplateResponse(
        request,
        "backups_admin.html",
        {"identity": identity, "destinations": destinations, "schedules": schedules, "jobs": jobs},
    )


@ui_router.post("/destinations")
def ui_create_destination(
    name: str = Form(...),
    kind: str = Form("local"),
    local_path: str = Form(""),
    rclone_remote_type: str = Form(""),
    rclone_path_prefix: str = Form(""),
    identity: Identity = Depends(get_identity),
):
    require_admin(identity)
    call_daemon(
        "backup.destination.create",
        identity,
        name=name,
        kind=kind,
        local_path=local_path or None,
        rclone_remote_type=rclone_remote_type or None,
        rclone_path_prefix=rclone_path_prefix,
        rclone_config={},
    )
    return RedirectResponse("/ui/backups", status_code=303)


@ui_router.post("/destinations/{destination_id}/delete")
def ui_delete_destination(destination_id: int, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    call_daemon("backup.destination.delete", identity, id=destination_id)
    return RedirectResponse("/ui/backups", status_code=303)


@ui_router.post("/schedules")
def ui_set_schedule(
    username: str = Form(""),
    frequency: str = Form("daily"),
    retention_count: int = Form(7),
    destination_id: int = Form(...),
    identity: Identity = Depends(get_identity),
):
    require_admin(identity)
    call_daemon(
        "backup.schedule.set",
        identity,
        username=username or None,
        frequency=frequency,
        retention_count=retention_count,
        destination_id=destination_id,
        enabled=True,
    )
    return RedirectResponse("/ui/backups", status_code=303)


# Incremental jobs live alongside the existing archive backup endpoints.
class SnapshotDestinationBody(BaseModel):
    name: str
    kind: str = 'local'
    path: str = ''
    ssh_host: str = ''
    ssh_user: str = ''
    ssh_port: int = 22
    ssh_host_key: str = ''
    ssh_auth: str = 'key'
    ssh_password: str = ''
    ssh_private_key: str = ''
    ssh_key_passphrase: str = ''
    s3_provider: str = 'custom'
    s3_endpoint: str = ''
    s3_bucket: str = ''
    s3_prefix: str = ''
    s3_region: str = ''
    s3_access_key: str = ''
    s3_secret_key: str = ''
    s3_session_token: str = ''
    drive_folder: str = ''
    drive_client_id: str = ''
    drive_client_secret: str = ''
    drive_token: str = ''


class SnapshotDestinationSettingsBody(BaseModel):
    enabled: bool | None = None
    customer_visible: bool | None = None


class DriveFolderBody(BaseModel):
    path: str


class DestinationOperationBody(BaseModel):
    account_mapping: dict[str, str] = {}


class SnapshotPolicyBody(BaseModel):
    name: str
    destination_id: int
    destination_ids: list[int] = []
    frequency: str = 'manual'
    enabled: bool = True
    mode: str = 'incremental'
    accounts: list[str] = []
    excluded_accounts: list[str] = []
    components: list[str] = ['files', 'databases', 'mail', 'config']
    include_paths: list[str] = []
    exclude_patterns: list[str] = []
    notification_channels: list[str] = []
    retention_count: int = 7
    retention_daily: int = 7
    retention_weekly: int = 4
    retention_monthly: int = 6
    timezone: str = 'UTC'


class TelegramPluginBody(BaseModel):
    enabled: bool = False
    chat_id: str = ''
    token: str = ''
    events: list[str] = ['backup.completed','backup.failed']


class BackupConfigurationImportBody(BaseModel):
    payload: str
    recovery_key: str
    apply: bool = False


@api_router.get('/snapshots/destinations')
def snapshot_destinations(identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon('snapshot.destination.list', identity)


@api_router.post('/snapshots/destinations')
def snapshot_create_destination(body: SnapshotDestinationBody, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon('snapshot.destination.create', identity, **body.model_dump())


@api_router.post('/snapshots/destinations/{destination_id}/initialize')
def snapshot_initialize_destination(destination_id: int, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon('snapshot.destination.initialize', identity, id=destination_id)


@api_router.post('/snapshots/destinations/{destination_id}/drive/oauth/start')
def snapshot_drive_oauth_start(destination_id: int, request: Request, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    callback=str(request.base_url).rstrip('/')+f'/api/v1/backups/snapshots/destinations/{destination_id}/drive/oauth/callback'
    return call_daemon('snapshot.drive.oauth.start',identity,id=destination_id,redirect_uri=callback)


@api_router.get('/snapshots/destinations/{destination_id}/drive/oauth/callback')
def snapshot_drive_oauth_callback(destination_id: int, state: str, code: str,
                                  identity: Identity = Depends(get_identity)):
    require_admin(identity)
    call_daemon('snapshot.drive.oauth.callback',identity,id=destination_id,state=state,code=code)
    return RedirectResponse('/app/backup-jobs?drive=connected',status_code=303)


@api_router.get('/snapshots/destinations/{destination_id}/drive/folders')
def snapshot_drive_folders(destination_id: int, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon('snapshot.drive.folders',identity,id=destination_id)


@api_router.put('/snapshots/destinations/{destination_id}/drive/folder')
def snapshot_drive_folder(destination_id: int, body: DriveFolderBody, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon('snapshot.drive.folder.set',identity,id=destination_id,path=body.path)


@api_router.post('/snapshots/destinations/{destination_id}/drive/revoke')
def snapshot_drive_revoke(destination_id: int, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon('snapshot.drive.revoke',identity,id=destination_id)


@api_router.post('/snapshots/destinations/{destination_id}/recovery-key')
def snapshot_recovery_key(destination_id: int, identity: Identity = Depends(get_identity)):
    from fastapi.responses import JSONResponse
    require_admin(identity)
    return JSONResponse(call_daemon('snapshot.destination.recovery_key', identity, id=destination_id),
        headers={'Cache-Control':'no-store','Pragma':'no-cache'})


@api_router.patch('/snapshots/destinations/{destination_id}')
def snapshot_destination_settings(destination_id: int, body: SnapshotDestinationSettingsBody, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon('snapshot.destination.set', identity, id=destination_id, **body.model_dump(exclude_none=True))


@api_router.delete('/snapshots/destinations/{destination_id}')
def snapshot_delete_destination(destination_id: int, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon('snapshot.destination.delete', identity, id=destination_id)


@api_router.post('/snapshots/destinations/{destination_id}/{action}')
def snapshot_destination_action(destination_id: int, action: str, body: DestinationOperationBody | None = None,
                                identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon('snapshot.destination.operation.queue', identity, id=destination_id, action=action,
                       **(body.model_dump() if body else {}))


@api_router.get('/snapshots/destination-operations')
def snapshot_destination_operations(destination_id: int | None = None, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon('snapshot.destination.operation.list', identity, id=destination_id)


@api_router.get('/snapshots/destinations/{destination_id}/browse')
def snapshot_destination_browse(destination_id: int,identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon('snapshot.destination.inventory',identity,id=destination_id)


@api_router.get('/snapshots/policies')
def snapshot_policies(identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon('snapshot.policy.list', identity)


@api_router.post('/snapshots/policies')
def snapshot_create_policy(body: SnapshotPolicyBody, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon('snapshot.policy.save', identity, **body.model_dump())


@api_router.put('/snapshots/policies/{policy_id}')
def snapshot_update_policy(policy_id: int, body: SnapshotPolicyBody, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon('snapshot.policy.save', identity, id=policy_id, **body.model_dump())


@api_router.post('/snapshots/policies/{policy_id}/run')
def snapshot_run_policy(policy_id: int, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon('snapshot.policy.run', identity, id=policy_id)


@api_router.get('/snapshots/runs')
def snapshot_runs(username: str | None = None, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon('snapshot.run.list', identity, username=username)


@api_router.post('/snapshots/runs/{run_id}/cancel')
def snapshot_cancel_run(run_id: int, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon('snapshot.run.cancel', identity, id=run_id)


@api_router.post('/snapshots/runs/{run_id}/retry')
def snapshot_retry_run(run_id: int, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon('snapshot.run.retry', identity, id=run_id)


class SnapshotPinBody(BaseModel):
    pinned: bool


@api_router.patch('/snapshots/runs/{run_id}/pin')
def snapshot_pin_run(run_id: int, body: SnapshotPinBody, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon('snapshot.run.pin', identity, id=run_id, pinned=body.pinned)


@api_router.get('/snapshots/catalog/accounts')
def snapshot_account_catalog(identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon('snapshot.catalog.accounts', identity)


@api_router.get('/snapshots/notifications')
def snapshot_notification_plugins(identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon('snapshot.notifications.status',identity)


@api_router.put('/snapshots/notifications/telegram')
def snapshot_save_telegram(body: TelegramPluginBody,identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon('snapshot.notifications.telegram.save',identity,**body.model_dump())


@api_router.post('/snapshots/notifications/telegram/test')
def snapshot_test_telegram(identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon('snapshot.notifications.telegram.test',identity)


@api_router.get('/snapshots/notification-deliveries')
def snapshot_notification_deliveries(identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon('snapshot.notifications.deliveries',identity)


@api_router.post('/snapshots/configuration/export')
def snapshot_export_configuration(identity: Identity = Depends(get_identity)):
    require_admin(identity)
    from fastapi.responses import JSONResponse
    return JSONResponse(call_daemon('snapshot.config.export',identity),
        headers={'Cache-Control':'no-store','Pragma':'no-cache'})


@api_router.post('/snapshots/configuration/import')
def snapshot_import_configuration(body: BackupConfigurationImportBody,identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon('snapshot.config.import',identity,**body.model_dump())
