#!/usr/bin/python3
"""Postfix submission content filter with mailbox-bound DKIM authority."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

# The filter deliberately has no access to Boron's root-only configuration or
# secrets. Its only authority is the OpenDKIM-owned KeyTable/key files.
os.environ["BORON_CONFIG"] = "/dev/null"
os.environ["BORON_SECRETS"] = "/dev/null"
os.environ["BORON_API_SECRETS"] = "/dev/null"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from daemon import mail_submission  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sender", required=True)
    parser.add_argument("--recipient", action="append", required=True)
    args = parser.parse_args()
    raw = sys.stdin.buffer.read(mail_submission.MAX_MESSAGE_BYTES + 1)
    if len(raw) > mail_submission.MAX_MESSAGE_BYTES:
        print("message exceeds 25 MB", file=sys.stderr)
        return 69
    try:
        prepared, envelope = mail_submission.prepare_submission_filter(raw, args.sender)
        mail_submission.submit_via_postfix(prepared, envelope, args.recipient)
    except mail_submission.MailSubmissionError as exc:
        print(str(exc), file=sys.stderr)
        return 67
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
