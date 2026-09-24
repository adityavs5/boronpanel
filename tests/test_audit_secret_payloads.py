import json
import pytest
from daemon.audit import _sanitize, record
from shared.db import write_session
from shared.models import AuditLog
from sqlalchemy import select


@pytest.mark.parametrize('key,value', [
    ('env_vars', {'DATABASE_URL': 'mysql://root:canary@localhost/db'}),
    ('environment', {'UNUSUAL_NAME': 'canary'}),
    ('content', 'DB_PASSWORD=canary'),
    ('headers', {'X-Custom-Auth': 'canary'}),
    ('url', 'https://example.com/hook?key=canary'),
    ('repository', 'https://user:canary@example.com/repo.git'),
    ('endpoint', 'https://example.com/#canary'),
])
def test_private_payloads_never_reach_audit_storage(isolated_db, key, value):
    record('admin', 'admin', 'test.operation', 'customer', {key: value}, 'ok')
    with write_session() as session:
        row = session.scalars(select(AuditLog)).one()
        assert 'canary' not in json.dumps(row.params)
        assert row.params[key] == '***'
        assert row.target == 'customer'


def test_nested_redaction_retains_nonsecret_metadata():
    assert _sanitize('changes', {'port': 30000, 'nested': [{'env_vars': {'ANY': 'canary'}}]}) == {
        'port': 30000, 'nested': [{'env_vars': '***'}],
    }
