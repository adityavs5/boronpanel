"""Standalone file restore helper. Opens roots, then permanently drops privilege.

JSON stdin is supplied only by the root snapshot service. Files are replaced
atomically through directory descriptors; target symlinks are never traversed.
"""
import json
import os
from pathlib import Path, PurePosixPath
import secrets
import shutil
import stat
import sys

FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
PROTECTED = {'.php'}


def components(value):
    path = PurePosixPath(value)
    if path.is_absolute() or '..' in path.parts or '\0' in value:
        raise ValueError('Restore paths must remain inside the account home')
    parts=tuple(p for p in path.parts if p != '.')
    if parts and parts[0] in PROTECTED:raise ValueError('PHP runtime files are managed by the panel')
    return parts


def valid_link(value, relative, home):
    target = value if value.startswith('/') else str(home / relative.parent / value)
    normalized = os.path.normpath(target)
    if not Path(normalized).is_relative_to(home):
        raise ValueError(f'Symbolic link escapes the account home: {relative}')


def prepare_source(root, uid, gid, home):
    modes = {}
    for base, dirs, files in os.walk(root, followlinks=False):
        if Path(base)==root:
            dirs[:]=[name for name in dirs if name not in PROTECTED]
            files=[name for name in files if name not in PROTECTED]
        for path in [Path(base), *[Path(base)/name for name in dirs+files]]:
            relative = path.relative_to(root)
            info = path.lstat()
            modes[str(relative)] = stat.S_IMODE(info.st_mode) & 0o777
            if stat.S_ISLNK(info.st_mode):
                valid_link(os.readlink(path), relative, home)
                continue
            if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                raise ValueError(f'Unsupported special file in restore: {relative}')
    # The private parent remains root-owned and inaccessible throughout this
    # preparation. No account process can race the root ownership changes.
    for relative, mode in modes.items():
        path = root / relative
        if path.is_symlink(): continue
        directory = path.is_dir()
        os.chown(path, uid, gid, follow_symlinks=False)
        os.chmod(path, mode | (0o500 if directory else 0o400), follow_symlinks=False)
    return modes


def child_directory(parent, name, create=False):
    if create:
        try: os.mkdir(name, 0o700, dir_fd=parent)
        except FileExistsError: pass
    return os.open(name, FLAGS, dir_fd=parent)


def walk_directory(root_fd, parts, create=False):
    current = os.dup(root_fd)
    try:
        for name in parts:
            child = child_directory(current, name, create)
            os.close(current); current = child
        return current
    except Exception:
        os.close(current)
        raise


def copy_entry(source_fd, target_fd, name, relative, modes, counts, home):
    info = os.stat(name, dir_fd=source_fd, follow_symlinks=False)
    if stat.S_ISDIR(info.st_mode):
        source_child = child_directory(source_fd, name)
        try:
            target_child = child_directory(target_fd, name, True)
            try:
                for item in os.listdir(source_child):
                    copy_entry(source_child,target_child,item,relative/item,modes,counts,home)
                os.fchmod(target_child,modes[str(relative)])
            finally: os.close(target_child)
        finally: os.close(source_child)
        counts['directories'] += 1
        return
    temporary = '.boron-restore-' + secrets.token_hex(12)
    try:
        if stat.S_ISLNK(info.st_mode):
            link=os.readlink(name,dir_fd=source_fd)
            valid_link(link,relative,home)
            os.symlink(link,temporary,dir_fd=target_fd)
            counts['links'] += 1
        elif stat.S_ISREG(info.st_mode):
            source = os.open(name,os.O_RDONLY|os.O_NOFOLLOW,dir_fd=source_fd)
            try:
                if not stat.S_ISREG(os.fstat(source).st_mode):raise ValueError('Restore source changed type')
                target = os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=target_fd)
                with os.fdopen(target,'wb') as output, os.fdopen(os.dup(source),'rb') as input_file:
                    shutil.copyfileobj(input_file,output,1024*1024)
                    output.flush();os.fsync(output.fileno())
                    os.fchmod(output.fileno(),modes[str(relative)])
                counts['files'] += 1
            finally: os.close(source)
        else:raise ValueError('Unsupported special file in restore')
        os.replace(temporary,name,src_dir_fd=target_fd,dst_dir_fd=target_fd)
        os.fsync(target_fd)
    finally:
        try:os.unlink(temporary,dir_fd=target_fd)
        except FileNotFoundError:pass


def main(params):
    uid=params['uid'];gid=params['gid']
    if isinstance(uid,bool) or isinstance(gid,bool) or not isinstance(uid,int) or not isinstance(gid,int) or uid<=0 or gid<=0:
        raise ValueError('Restore must use a non-root account identity')
    source=Path(params['source']);home=Path(params['home'])
    if not source.is_absolute() or not home.is_absolute() or source.resolve()!=source or home.resolve()!=home:
        raise ValueError('Restore roots must be absolute directories without symbolic links')
    selected=[components(value) for value in params['paths']]
    if not selected:raise ValueError('No restore paths selected')
    source_fd=os.open(source,FLAGS);target_fd=os.open(home,FLAGS)
    try:
        if os.fstat(target_fd).st_uid!=uid:raise ValueError('Account home has an unexpected owner')
        modes=prepare_source(source,uid,gid,home)
        # No root filesystem mutations after this point. Retained directory
        # descriptors allow access to the verified private source tree.
        os.setgroups([]);os.setgid(gid);os.setuid(uid)
        if os.getuid()!=uid or os.geteuid()!=uid:raise ValueError('Could not drop restore privileges')
        counts={'files':0,'directories':0,'links':0}
        for parts in selected:
            if not parts:
                for name in os.listdir(source_fd):
                    if name not in PROTECTED:copy_entry(source_fd,target_fd,name,Path(name),modes,counts,home)
                continue
            src_parent=walk_directory(source_fd,parts[:-1])
            try:
                dst_parent=walk_directory(target_fd,parts[:-1],True)
                try:copy_entry(src_parent,dst_parent,parts[-1],Path(*parts),modes,counts,home)
                finally:os.close(dst_parent)
            finally:os.close(src_parent)
        return {**counts,'effective_uid':os.geteuid(),'nice_level':os.getpriority(os.PRIO_PROCESS,0)}
    finally:
        os.close(source_fd);os.close(target_fd)


if __name__=='__main__':
    try:print(json.dumps(main(json.load(sys.stdin))))
    except Exception as exc:
        print(str(exc),file=sys.stderr)
        sys.exit(1)
