"""Systemd supervision for the short offline portion of mail restoration.

Prepare/decrypt mailboxes before invoking this module. The supervised command
must only validate its private journal and exchange already prepared directories.
No customer command or service name may reach this internal entry point.
"""
import re
from pathlib import Path

from daemon.procutil import run
from shared.validation import ValidationError


def _service_name(value):
    if value != 'dovecot.service' and not re.fullmatch(r'boron-mail-test-[a-f0-9]{32}\.service', value):
        raise ValidationError('Invalid mail service identifier')
    return value


def service_status(service='dovecot.service'):
    service = _service_name(service)
    result = run(['/usr/bin/systemctl', 'show', service, '--property=LoadState',
                  '--property=ActiveState', '--property=SubState', '--property=KillMode',
                  '--property=MainPID', '--property=ControlPID'], timeout=10)
    if not result.ok:
        raise ValidationError('Could not inspect the mail service')
    return dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)


def require_stopped(service='dovecot.service'):
    state = service_status(service)
    if (state.get('LoadState') != 'loaded' or state.get('ActiveState') != 'inactive'
            or state.get('SubState') != 'dead' or state.get('KillMode') != 'control-group'
            or state.get('MainPID') != '0' or state.get('ControlPID') != '0'):
        raise ValidationError('Mail service has not stopped completely; no mailbox may be exchanged')


def switch_unit(service='dovecot.service'):
    service = _service_name(service)
    if service == 'dovecot.service':
        return 'boron-mail-switch.service'
    return 'boron-mail-switch-test-' + service[len('boron-mail-test-'):]


def supervised_command(command, operation_id, *, service='dovecot.service'):
    """Run a trusted internal switch command independently of the panel process.

    Persist operation_id in the restore journal before calling. On observation
    timeout, inspect that same unit/journal; never blindly launch a new switch.
    ExecStopPost restarts mail even when the command is killed or fails. Mailbox
    guards must remain until the coordinator validates each resulting Maildir.
    """
    service = _service_name(service)
    if not isinstance(operation_id, str) or not re.fullmatch(r'[a-f0-9]{32}', operation_id):
        raise ValidationError('Invalid mail switch operation identifier')
    if not isinstance(command, list) or not command or not all(isinstance(arg, str) for arg in command):
        raise ValidationError('Mail switch requires a trusted argument list')
    state = service_status(service)
    if (state.get('LoadState') != 'loaded' or state.get('ActiveState') != 'active'
            or state.get('SubState') != 'running' or state.get('KillMode') != 'control-group'):
        raise ValidationError('Mail service must be running with complete process-group shutdown')
    # A stable unit per mail service prevents two callers from independently
    # stopping/resuming it around overlapping exchanges, even if a caller dies.
    unit = switch_unit(service)
    result = run([
        '/usr/bin/systemd-run', '--quiet', '--wait', '--unit=' + unit,
        '--description=Boron mailbox switch ' + operation_id,
        '--property=WorkingDirectory=' + str(Path(__file__).resolve().parents[1]),
        '--property=Type=oneshot', '--property=RemainAfterExit=no',
        '--property=TimeoutStartSec=60', '--property=TimeoutStopSec=60',
        '--property=KillMode=control-group',
        '--property=ExecStartPre=/usr/bin/systemctl stop ' + service,
        '--property=ExecStopPost=/usr/bin/systemctl start ' + service,
        *command,
    ], timeout=150)
    resumed = service_status(service)
    if resumed.get('ActiveState') != 'active' or resumed.get('SubState') != 'running':
        raise ValidationError('Mail service did not resume; retain restore guards and inspect recovery')
    if not result.ok:
        raise ValidationError('Mail switch failed; inspect its journal before retrying')
    return {'unit': unit, 'operation_id': operation_id, 'mail_service': 'running'}
