#!/usr/bin/env python3
"""Decrypt a Boron portable backup without requiring a running panel."""
import argparse
from getpass import getpass
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from daemon.portable_crypto import PortableEncryptionError, decrypt  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Decrypt a .boron.tar[.gz].enc backup")
    parser.add_argument("archive", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--recovery-key-file", type=Path,
                        help="Read the destination recovery key from this file instead of prompting")
    args = parser.parse_args()
    key = args.recovery_key_file.read_text().strip() if args.recovery_key_file else getpass("Destination recovery key: ")
    if args.output.exists():
        parser.error("output already exists")
    try:
        decrypt(args.archive, args.output, key)
    except (OSError, PortableEncryptionError) as exc:
        print(f"decrypt failed: {exc}", file=sys.stderr)
        return 1
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
