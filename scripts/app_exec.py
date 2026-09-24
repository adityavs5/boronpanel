"""Open tenant logs only after systemd User=/Group= have taken effect."""
import os
import stat
import sys


def main(argv):
    if os.geteuid() == 0 or len(argv) < 4 or argv[0] != '--log' or argv[2] != '--':
        raise RuntimeError('tenant identity and log/command arguments required')
    fd = os.open(argv[1], os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o640)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid():
            raise RuntimeError('log must be a regular file owned by the service user')
        os.dup2(fd, 1)
        os.dup2(fd, 2)
    finally:
        if fd > 2:
            os.close(fd)
    os.execv(argv[3], argv[3:])


if __name__ == '__main__':
    main(sys.argv[1:])
