"""Honest capability gates for privileged host-integration tests."""
from __future__ import annotations

import errno
import os
import socket
from os import PathLike

import pytest


def chown_or_skip(path: str | bytes | PathLike[str] | PathLike[bytes], uid: int, gid: int) -> None:
    """Change ownership or skip when the runner cannot map that identity.

    Root inside a user namespace can have effective UID 0 while arbitrary
    host UIDs/GIDs remain unmapped. Treating that environment as a product
    failure makes the unprivileged suite nondeterministic; the nightly real
    Ubuntu runner performs these checks with actual service identities.
    """
    try:
        os.chown(path, uid, gid)
    except OSError as exc:
        if exc.errno in {errno.EPERM, errno.EINVAL}:
            pytest.skip(f"runner cannot map disposable uid/gid {uid}:{gid}")
        raise


def socket_or_skip(*args, **kwargs) -> socket.socket:
    """Create a socket or skip when the test sandbox denies networking."""
    try:
        return socket.socket(*args, **kwargs)
    except OSError as exc:
        if exc.errno in {errno.EPERM, errno.EACCES}:
            pytest.skip("runner does not permit disposable sockets")
        raise
