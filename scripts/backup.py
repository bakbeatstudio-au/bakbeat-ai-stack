#!/usr/bin/env python3
"""Manual recovery checkpoint. Never replace the live DB or existing backups.

Usage: python scripts/backup.py --target DIRECTORY
The project directory is the parent of this script's directory and must be a clean Git work tree.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import uuid

SOURCE = Path(__file__).resolve().parents[1]
DATABASE = Path.home() / 'Library/Application Support/BakBeat AI Stack/data/ai-stack.sqlite3'


def run(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout.strip()


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_database(path):
    connection = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
    try:
        if connection.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
            raise RuntimeError('SQLite integrity check failed.')
        if connection.execute('PRAGMA foreign_key_check').fetchall():
            raise RuntimeError('SQLite foreign-key check failed.')
        # Counts, not prompt/response content, go into the manifest.
        names = ('tasks', 'calls', 'schema_metadata', 'projects', 'conversations', 'task_context')
        existing = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        return {name: connection.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0]
                for name in names if name in existing}
    finally:
        connection.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description='Create a verified recovery checkpoint of the source tree and database.')
    parser.add_argument('--target', required=True, help='existing directory that will receive a verified copy of the checkpoint')
    TARGET = Path(parser.parse_args(argv).target).expanduser().resolve()
    os.umask(0o077)
    if not (SOURCE / '.git').exists():
        raise RuntimeError('Run this script from a Git work tree of the project, not a restored copy.')
    if TARGET == SOURCE or SOURCE in TARGET.parents:
        raise RuntimeError('The target must be outside the project directory.')
    for root in (SOURCE, TARGET):
        if not root.is_dir() or root.resolve() != root:
            raise RuntimeError('Expected project or target directory is missing or redirected.')
        if shutil.disk_usage(root).free < 100 * 1024 * 1024:
            raise RuntimeError('Less than 100 MiB free; stop and review space.')
    if run('git','-C',str(SOURCE),'status','--porcelain'):
        raise RuntimeError('Commit or explicitly preserve uncommitted source before taking a checkpoint.')
    if not DATABASE.is_file() or DATABASE.resolve() != DATABASE:
        raise RuntimeError('Live database missing or redirected.')
    for root in (SOURCE, TARGET):
        folder=root/'backups'
        if folder.is_symlink():
            raise RuntimeError('Backup directory may not be a symlink.')
        folder.mkdir(mode=0o700, exist_ok=True)
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8]
    name='checkpoint-'+stamp
    staging=SOURCE/'backups'/('.partial-'+stamp)
    staging.mkdir(mode=0o700)
    head=run('git','-C',str(SOURCE),'rev-parse','HEAD')
    run('git','-C',str(SOURCE),'bundle','create',str(staging/'source.bundle'),'--all')
    run('git','-C',str(SOURCE),'bundle','verify',str(staging/'source.bundle'))
    src=sqlite3.connect(DATABASE.as_uri()+'?mode=ro',uri=True)
    dst=sqlite3.connect(staging/'database.sqlite3')
    try:
        # SQLite's online backup API includes committed WAL content consistently.
        src.backup(dst, pages=256, sleep=.1)
    finally:
        dst.close()
        src.close()
    snapshot=staging/'database.sqlite3'
    # Self-contained, closed snapshot suitable for ordinary file backup.
    with sqlite3.connect(snapshot) as c:
        c.execute('PRAGMA journal_mode=DELETE')
    counts=read_database(snapshot)
    manifest={'format':1,'created_at':datetime.now(timezone.utc).isoformat(),
              'git_head':head,'database_counts':counts,
              'files':{n:digest(staging/n) for n in ('source.bundle','database.sqlite3')},
              'includes':['committed Git history and tracked source','consistent live database snapshot'],
              'excludes':['macOS Keychain credentials','.venv','ignored workspace/assets','uncommitted source'],
              'automatic_schedule':False}
    (staging/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    package=SOURCE/'backups'/name
    staging.rename(package)
    dest_partial=TARGET/'backups'/('.partial-'+stamp)
    shutil.copytree(package,dest_partial)
    for path in package.iterdir():
        if digest(path)!=digest(dest_partial/path.name):
            raise RuntimeError('Backup copy verification failed.')
    destination=TARGET/'backups'/name
    dest_partial.rename(destination)
    # Restore from the destination drive, not from the working-drive package.
    with tempfile.TemporaryDirectory(prefix='ai-stack-restore-') as temp:
        restored=Path(temp)
        run('git','clone','--quiet',str(destination/'source.bundle'),str(restored/'source'))
        if run('git','-C',str(restored/'source'),'rev-parse','HEAD')!=head:
            raise RuntimeError('Restored Git checkpoint mismatch.')
        run('git','-C',str(restored/'source'),'fsck','--full')
        # Compare every tracked restored file/symlink against the clean source.
        tracked=run('git','-C',str(SOURCE),'ls-files','-z').split('\0')
        for relative in filter(None,tracked):
            original=SOURCE/relative
            copy=restored/'source'/relative
            if original.is_symlink():
                if not copy.is_symlink() or os.readlink(original)!=os.readlink(copy):
                    raise RuntimeError('Restored symlink mismatch.')
            elif digest(original)!=digest(copy):
                raise RuntimeError('Restored tracked file mismatch.')
        shutil.copyfile(destination/'database.sqlite3',restored/'database.sqlite3')
        if read_database(restored/'database.sqlite3')!=counts:
            raise RuntimeError('Restored database count mismatch.')
    report={'restored_from':str(destination),'git_head':head,
            'database_counts':counts,'result':'passed',
            'verified':['SHA-256 copy hashes','Git clone and fsck','tracked file contents','SQLite integrity and foreign keys','table row counts'],
            'not_verified':['reinstalling Python/dependencies','re-provisioning Keychain','live provider calls'],
            'live_database_replaced':False}
    for folder in (package,destination):
        (folder/'restore-check.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))


if __name__=='__main__':
    main()
