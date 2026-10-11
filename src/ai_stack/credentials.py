"""Interactive-only provisioning. Never echo secrets or backend exception text."""
import getpass
from pathlib import Path
import subprocess
import sys
import warnings

SERVICE = 'com.bakbeat.ai-stack'
PROVIDERS = ('openai', 'anthropic')


def backend():
    if sys.platform != 'darwin':
        raise RuntimeError('macOS login Keychain is required.')
    result = subprocess.run(['/usr/bin/security', 'default-keychain', '-d', 'user'],
                            capture_output=True, text=True, check=True)
    selected = Path(result.stdout.strip().strip('"')).expanduser().resolve()
    expected = (Path.home() / 'Library/Keychains/login.keychain-db').resolve()
    if selected != expected:
        raise RuntimeError('The login Keychain must be the default user Keychain.')
    # Explicit backend prevents plaintext alternatives or environment fallback.
    from keyring.backends.macOS import Keyring
    instance = Keyring()
    instance.keychain = None
    return instance


def provision(provider):
    if provider not in PROVIDERS:
        raise ValueError('Unknown provider.')
    if not sys.stdin.isatty():
        raise RuntimeError('Run key setup directly in an interactive Terminal.')
    store = backend()
    if store.get_password(SERVICE, provider) is not None:
        raise RuntimeError('A key already exists; replacement requires a separate explicit step.')
    with warnings.catch_warnings():
        warnings.simplefilter('error', getpass.GetPassWarning)
        secret = getpass.getpass(f'{provider} API key (hidden): ')
    if not secret or secret != secret.strip() or any(c.isspace() for c in secret):
        raise ValueError('Empty keys and whitespace are not accepted.')
    try:
        store.set_password(SERVICE, provider, secret)
        if store.get_password(SERVICE, provider) != secret:
            raise RuntimeError('Keychain verification failed.')
    finally:
        secret = None
    print(f'{provider}: saved and verified in macOS Keychain.')
