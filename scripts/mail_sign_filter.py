#!/usr/bin/python3
"""Postfix submission content filter with mailbox-bound DKIM authority."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

# The filter deliberately has no access to Boron's root-only configuration or
# secrets. Its only authority is the OpenDKIM-owned KeyTable/key files.
# Postfix copies the filter's stderr into bounce text; loading these empty
# placeholders logs nothing (shared/config.py).
os.environ["BORON_CONFIG"] = "/dev/null"
os.environ["BORON_SECRETS"] = "/dev/null"
os.environ["BORON_API_SECRETS"] = "/dev/null"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from daemon import mail_submission  # noqa: E402

# sysexits(3) codes that Postfix pipe(8) turns into delivery status codes.
EX_UNAVAILABLE = 69  # 5.3.0: message too large to accept
EX_TEMPFAIL = 75     # 4.3.0: retried from the queue (signer/queue outage)
EX_NOPERM = 77       # 5.7.0: policy refusal; never "5.1.1 user unknown"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sender", required=True)
    # Postfix passes ${sasl_username}; absent only on a master.cf written by
    # an older release, which the upgrade reconciles before reloading Postfix.
    parser.add_argument("--login")
    parser.add_argument("--recipient", action="append", required=True)
    args = parser.parse_args()
    raw = sys.stdin.buffer.read(mail_submission.MAX_MESSAGE_BYTES + 1)
    if len(raw) > mail_submission.MAX_MESSAGE_BYTES:
        print("message exceeds 25 MB", file=sys.stderr)
        return EX_UNAVAILABLE
    try:
        prepared, envelope = mail_submission.prepare_submission_filter(raw, args.sender, login=args.login)
        mail_submission.submit_via_postfix(prepared, envelope, args.recipient)
    except mail_submission.MailSubmissionUnavailable as exc:
        print(str(exc), file=sys.stderr)
        return EX_TEMPFAIL
    except mail_submission.MailSubmissionError as exc:
        print(str(exc), file=sys.stderr)
        return EX_NOPERM
    except Exception:  # noqa: BLE001 - a filter crash must defer, not bounce
        print("mail submission filter failed; the message will be retried", file=sys.stderr)
        return EX_TEMPFAIL
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
