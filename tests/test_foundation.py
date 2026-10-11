import sqlite3
import tomllib
from unittest.mock import Mock

import pytest
from ai_stack import config, credentials, storage


@pytest.fixture
def settings():
    with (config.ROOT / 'config/settings.example.toml').open('rb') as f:
        return tomllib.load(f)


@pytest.mark.parametrize('section,key,value', [
    ('server', 'host', '0.0.0.0'),
    ('server', 'host', '192.168.1.10'),
    ('features', 'tools', True),
    ('features', 'router', True),
    ('features', 'cross_review', True),
    ('features', 'lan_access', True),
    ('credentials', 'backend', 'plaintext'),
    ('storage', 'database_path', '/mnt/external/live.sqlite3'),
])
def test_reject_unsafe_config(settings, section, key, value):
    settings[section][key] = value
    with pytest.raises(ValueError):
        config.validate(settings)


def test_valid_config(settings):
    assert config.validate(settings) is settings


def test_database_restart_preserves_data(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, 'verify_internal_directory', lambda _: None)
    db = tmp_path / 'test.sqlite3'
    storage.initialize(db)
    with sqlite3.connect(db) as conn:
        conn.execute('CREATE TABLE sample (value TEXT)')
        conn.execute("INSERT INTO sample VALUES ('keep')")
    storage.initialize(db)
    with sqlite3.connect(db) as conn:
        assert conn.execute('SELECT value FROM sample').fetchone() == ('keep',)
        assert conn.execute('PRAGMA journal_mode').fetchone() == ('wal',)
        assert conn.execute('SELECT count(*) FROM schema_metadata').fetchone() == (1,)
    assert db.stat().st_mode & 0o077 == 0


def test_database_symlink_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, 'verify_internal_directory', lambda _: None)
    db = tmp_path / 'link.sqlite3'
    db.symlink_to(tmp_path / 'target.sqlite3')
    with pytest.raises(ValueError):
        storage.initialize(db)
    assert not (tmp_path / 'target.sqlite3').exists()


def test_noninteractive_key_entry_never_reads_secret(monkeypatch):
    monkeypatch.setattr(credentials.sys.stdin, 'isatty', lambda: False)
    store = Mock()
    monkeypatch.setattr(credentials, 'backend', store)
    with pytest.raises(RuntimeError):
        credentials.provision('openai')
    store.assert_not_called()


def test_existing_key_not_overwritten(monkeypatch):
    monkeypatch.setattr(credentials.sys.stdin, 'isatty', lambda: True)
    store = Mock()
    store.get_password.return_value = 'existing-test-secret'
    monkeypatch.setattr(credentials, 'backend', lambda: store)
    with pytest.raises(RuntimeError):
        credentials.provision('openai')
    store.set_password.assert_not_called()


def test_key_saved_without_output(monkeypatch, capsys):
    monkeypatch.setattr(credentials.sys.stdin, 'isatty', lambda: True)
    store = Mock()
    store.get_password.side_effect = [None, 'test-secret']
    monkeypatch.setattr(credentials, 'backend', lambda: store)
    monkeypatch.setattr(credentials.getpass, 'getpass', lambda _: 'test-secret')
    credentials.provision('openai')
    store.set_password.assert_called_once_with(credentials.SERVICE, 'openai', 'test-secret')
    assert 'test-secret' not in capsys.readouterr().out


def test_cli_suppresses_secret_in_exception(monkeypatch, capsys):
    from ai_stack import __main__ as cli
    monkeypatch.setattr(cli, 'load', lambda: None)
    monkeypatch.setattr(cli.sys, 'argv', ['ai_stack', 'setup-key', 'openai'])
    monkeypatch.setattr(credentials, 'provision', Mock(side_effect=RuntimeError('test-secret')))
    assert cli.main() == 1
    assert 'test-secret' not in capsys.readouterr().err


@pytest.mark.parametrize('internal', [False, None])
def test_external_or_unknown_device_rejected(tmp_path, monkeypatch, internal):
    import plistlib
    from types import SimpleNamespace
    tmp_path.chmod(0o700)
    outputs = [SimpleNamespace(stdout='Filesystem blocks used available capacity mount\n/dev/disk9 1 1 1 1% /mnt/test\n'),
               SimpleNamespace(stdout=plistlib.dumps({} if internal is None else {'Internal': internal}))]
    monkeypatch.setattr(storage.subprocess, 'run', Mock(side_effect=outputs))
    with pytest.raises(ValueError):
        storage.verify_internal_directory(tmp_path)


def test_shared_permissions_rejected(tmp_path):
    tmp_path.chmod(0o755)
    with pytest.raises(ValueError):
        storage.verify_internal_directory(tmp_path)
