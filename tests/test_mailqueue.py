import datetime as dt

import pytest

from daemon import mailqueue
from daemon.procutil import ProcResult
from shared.validation import ValidationError

# Captured live from this project's own real Postfix mailq output (a
# genuine deferred message sent to an unroutable IP-literal recipient,
# `docs/CHECKPOINT-phase5-3-mail-queue.md`) -- parsed against the real
# format, not a guessed-at one.
REAL_DEFERRED_SAMPLE = """-Queue ID-  --Size-- ----Arrival Time---- -Sender/Recipient-------
4C87949727      380 Fri Jul  3 17:32:11  root@cpanel-test.local
                (connect to 203.0.113.1[203.0.113.1]:25: Connection timed out)
                                         nobody@[203.0.113.1]

-- 0 Kbytes in 1 Request.
"""

ACTIVE_SAMPLE = """-Queue ID-  --Size-- ----Arrival Time---- -Sender/Recipient-------
4C87949727*     380 Fri Jul  3 17:32:11  root@cpanel-test.local
                                         nobody@example.invalid

-- 0 Kbytes in 1 Request.
"""

EMPTY_SAMPLE = "Mail queue is empty\n"

MULTI_RECIPIENT_SAMPLE = """-Queue ID-  --Size-- ----Arrival Time---- -Sender/Recipient-------
AAAA11111      100 Fri Jul  3 10:00:00  sender@example.com
                (deferred reason one)
                                         alice@example.com
                                         bob@example.com

BBBB22222      200 Fri Jul  3 11:00:00  other@example.com
                                         charlie@example.com

-- 0 Kbytes in 2 Requests.
"""


def test_parse_mailq_extracts_deferred_entry_with_reason():
    entries = mailqueue._parse_mailq(REAL_DEFERRED_SAMPLE)
    assert len(entries) == 1
    e = entries[0]
    assert e["queue_id"] == "4C87949727"
    assert e["flag"] == ""
    assert e["size"] == 380
    assert e["sender"] == "root@cpanel-test.local"
    assert e["recipients"] == ["nobody@[203.0.113.1]"]
    assert e["reason"] == "connect to 203.0.113.1[203.0.113.1]:25: Connection timed out"


def test_parse_mailq_extracts_active_flag():
    entries = mailqueue._parse_mailq(ACTIVE_SAMPLE)
    assert entries[0]["flag"] == "*"
    assert entries[0]["reason"] is None


def test_parse_mailq_handles_empty_queue():
    assert mailqueue._parse_mailq(EMPTY_SAMPLE) == []


def test_parse_mailq_handles_multiple_entries_and_recipients():
    entries = mailqueue._parse_mailq(MULTI_RECIPIENT_SAMPLE)
    assert len(entries) == 2
    assert entries[0]["recipients"] == ["alice@example.com", "bob@example.com"]
    assert entries[0]["reason"] == "deferred reason one"
    assert entries[1]["recipients"] == ["charlie@example.com"]
    assert entries[1]["reason"] is None


def test_status_for_flag():
    assert mailqueue._status_for("*") == "active"
    assert mailqueue._status_for("!") == "held"
    assert mailqueue._status_for("") == "deferred"


def test_list_queue_flattens_one_row_per_recipient(monkeypatch):
    monkeypatch.setattr(mailqueue, "run", lambda args, timeout=15: ProcResult(args=args, returncode=0, stdout=MULTI_RECIPIENT_SAMPLE, stderr=""))
    result = mailqueue.list_queue({})
    assert result["count"] == 3
    recipients = {row["recipient"] for row in result["entries"]}
    assert recipients == {"alice@example.com", "bob@example.com", "charlie@example.com"}


def test_list_queue_search_filters_by_sender_or_recipient(monkeypatch):
    monkeypatch.setattr(mailqueue, "run", lambda args, timeout=15: ProcResult(args=args, returncode=0, stdout=MULTI_RECIPIENT_SAMPLE, stderr=""))
    result = mailqueue.list_queue({"search": "bob"})
    assert result["count"] == 1
    assert result["entries"][0]["recipient"] == "bob@example.com"


def test_list_queue_computes_age_seconds(monkeypatch):
    monkeypatch.setattr(mailqueue, "run", lambda args, timeout=15: ProcResult(args=args, returncode=0, stdout=REAL_DEFERRED_SAMPLE, stderr=""))
    result = mailqueue.list_queue({})
    assert result["entries"][0]["age_seconds"] >= 0


def test_validate_queue_id_rejects_shell_metacharacters():
    for bad in ["abc; rm -rf /", "abc`whoami`", "abc$(id)", "", "abc def"]:
        with pytest.raises(ValidationError):
            mailqueue._validate_queue_id(bad)


def test_validate_queue_id_accepts_real_shape():
    assert mailqueue._validate_queue_id("4C87949727") == "4C87949727"


def test_flush_message_calls_postqueue_dash_i(monkeypatch):
    calls = []

    def fake_run(args, timeout=30):
        calls.append(args)
        return ProcResult(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(mailqueue, "run", fake_run)
    result = mailqueue.flush_message({"queue_id": "4C87949727"})
    assert calls == [["postqueue", "-i", "4C87949727"]]
    assert result["status"] == "flush_requested"


def test_flush_message_rejects_bad_queue_id(monkeypatch):
    monkeypatch.setattr(mailqueue, "run", lambda args, timeout=30: ProcResult(args=args, returncode=0, stdout="", stderr=""))
    with pytest.raises(ValidationError):
        mailqueue.flush_message({"queue_id": "not; valid"})


def test_flush_all_calls_postqueue_dash_f(monkeypatch):
    calls = []

    def fake_run(args, timeout=60):
        calls.append(args)
        return ProcResult(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(mailqueue, "run", fake_run)
    mailqueue.flush_all({})
    assert calls == [["postqueue", "-f"]]


def test_delete_message_calls_postsuper_dash_d(monkeypatch):
    calls = []

    def fake_run(args, timeout=30):
        calls.append(args)
        return ProcResult(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(mailqueue, "run", fake_run)
    result = mailqueue.delete_message({"queue_id": "4C87949727"})
    assert calls == [["postsuper", "-d", "4C87949727"]]
    assert result["status"] == "deleted"


def test_delete_all_calls_postsuper_dash_d_all(monkeypatch):
    calls = []

    def fake_run(args, timeout=60):
        calls.append(args)
        return ProcResult(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(mailqueue, "run", fake_run)
    mailqueue.delete_all({})
    assert calls == [["postsuper", "-d", "ALL"]]


def test_flush_message_raises_on_failure(monkeypatch):
    monkeypatch.setattr(mailqueue, "run", lambda args, timeout=30: ProcResult(args=args, returncode=1, stdout="", stderr="not found"))
    with pytest.raises(RuntimeError):
        mailqueue.flush_message({"queue_id": "4C87949727"})
