"""Internal-only SQLite foundation; no provider calls or conversation schema yet."""
import os
from pathlib import Path
import plistlib
import sqlite3
import stat
import subprocess


def verify_internal_directory(directory):
    directory = Path(directory)
    if directory.resolve() != directory or not directory.is_dir():
        raise ValueError('Database directory must exist without symlinks.')
    info = directory.stat()
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
        raise ValueError('Database directory must be private to the current user.')
    # Fail closed if the OS cannot confirm the backing device is internal.
    device = subprocess.run(['/bin/df', '-P', str(directory)], check=True,
                            capture_output=True, text=True).stdout.splitlines()[-1].split()[0]
    result = subprocess.run(['/usr/sbin/diskutil', 'info', '-plist', device],
                            check=True, capture_output=True)
    disk = plistlib.loads(result.stdout)
    if disk.get('Internal') is not True:
        raise ValueError('Database storage is not confirmed as internal.')


def initialize(path):
    path = Path(path)
    verify_internal_directory(path.parent)
    if path.is_symlink():
        raise ValueError('Database symlinks are forbidden.')
    old_mask = os.umask(0o077)
    try:
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        info = os.fstat(fd)
        os.close(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise ValueError('Database must be a private, single-link regular file.')
        connection = sqlite3.connect(path, timeout=5)
        try:
            connection.execute('PRAGMA busy_timeout=5000')
            if connection.execute('PRAGMA journal_mode=WAL').fetchone()[0] != 'wal':
                raise RuntimeError('SQLite WAL mode is unavailable.')
            with connection:
                connection.execute('CREATE TABLE IF NOT EXISTS schema_metadata (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)')
                connection.execute("INSERT OR IGNORE INTO schema_metadata VALUES (1, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))")
            if connection.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise RuntimeError('Database integrity check failed.')
        finally:
            connection.close()
    finally:
        os.umask(old_mask)
