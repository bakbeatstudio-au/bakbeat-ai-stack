"""Single owning process for the local application."""
from contextlib import contextmanager
import fcntl
import os
import uuid
from .config import DATABASE, load
from .storage import verify_internal_directory


@contextmanager
def ownership():
    verify_internal_directory(DATABASE.parent)
    fd = os.open(DATABASE.parent / 'server.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def run(command):
    from .engine import Engine
    with ownership():
        os.umask(0o077)
        engine = Engine()
        if command == 'smoke-test':
            import json
            calls = []
            for provider in ('openai', 'anthropic'):
                with engine.connect() as c:
                    previous = c.execute("SELECT task_id FROM calls JOIN tasks ON tasks.id=calls.task_id WHERE provider=? AND model=? AND calls.status='completed' AND prompt=? ORDER BY calls.created_at DESC LIMIT 1",
                                         (provider, engine.models[provider], 'Reply with exactly: Connection confirmed.')).fetchone()
                if previous:
                    result = engine.get(previous[0])
                else:
                    result = engine.run(str(uuid.uuid5(uuid.NAMESPACE_URL, 'bakbeat-smoke-v2-' + provider)),
                                        provider, 'Reply with exactly: Connection confirmed.', max_output=256)
                calls.extend(c for c in result['calls'] if c['provider'] == provider)
            print(json.dumps({'calls': calls}, indent=2))
            return 0 if all(c['status']=='completed' for c in calls) else 1
        import uvicorn
        from .server import create_app
        settings = load()['server']
        uvicorn.run(create_app(engine, settings['port']), host='127.0.0.1',
                    port=settings['port'], access_log=False, log_level='warning',
                    proxy_headers=False, workers=1)
        return 0
