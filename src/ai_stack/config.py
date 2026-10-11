"""Fail-closed Stage 0 configuration for the local macOS app."""
from pathlib import Path
import tomllib

ROOT = Path(__file__).resolve().parents[2]
DATABASE = Path.home() / 'Library/Application Support/BakBeat AI Stack/data/ai-stack.sqlite3'


def validate(config):
    if config.get('config_version') != 1:
        raise ValueError('Unsupported configuration version.')
    server = config['server']
    if server['host'] != '127.0.0.1' or server['execution_context'] != 'logged_in_user':
        raise ValueError('Only localhost in the logged-in user session is supported.')
    if type(server['port']) is not int or not 1024 <= server['port'] <= 65535:
        raise ValueError('Invalid server port.')
    expected = {'router', 'cross_review', 'tools', 'lan_access'}
    if set(config['features']) != expected or any(v is not False for v in config['features'].values()):
        raise ValueError('Later-stage features must remain disabled.')
    if config['credentials'] != {'backend': 'macos_keychain', 'service': 'com.bakbeat.ai-stack'}:
        raise ValueError('Only the project macOS Keychain service is supported.')
    for provider in ('openai', 'anthropic'):
        if config['providers'][provider]['keychain_account'] != provider:
            raise ValueError('Unexpected Keychain account.')
    path = Path(config['storage']['database_path']).expanduser()
    if path != DATABASE or path.resolve() != DATABASE:
        raise ValueError('Database must use the fixed internal path, without symlinks.')
    return config


def load():
    with (ROOT / 'config/settings.example.toml').open('rb') as stream:
        return validate(tomllib.load(stream))
