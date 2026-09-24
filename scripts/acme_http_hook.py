#!/usr/bin/env python3
"""Certbot manual HTTP hook; invoked for initial issuance and renewal."""
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from daemon.acme_http import perform

if __name__ == '__main__':
    perform(sys.argv[1], os.environ['CERTBOT_DOMAIN'], os.environ['CERTBOT_TOKEN'],
            os.environ['CERTBOT_VALIDATION'])
