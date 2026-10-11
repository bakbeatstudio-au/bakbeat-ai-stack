import re
import subprocess
import threading
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import ai_stack
from ai_stack.engine import Busy, Conflict, Engine
from ai_stack.server import create_app

ROOT = Path(__file__).resolve().parent.parent
JSC = '/System/Library/Frameworks/JavaScriptCore.framework/Versions/Current/Helpers/jsc'


def test_imports_target_this_worktree():
    assert Path(ai_stack.__file__).resolve().is_relative_to(ROOT / 'src')


@pytest.fixture
def api(tmp_path):
    calls = []
    def answer(provider, model, prompt, key, limit):
        calls.append((provider, prompt))
        return {'status':'completed','text':provider+' reply','usage':{'input_tokens':5,'output_tokens':3}}
    engine = Engine(tmp_path/'test.db', keys={'openai':'fake','anthropic':'fake'}, generator=answer, initialize_db=False)
    with TestClient(create_app(engine), base_url='http://127.0.0.1:8765') as client:
        token = re.search('name="bakbeat-token" content="([^"]+)"', client.get('/').text).group(1)
        client.headers.update({'X-BakBeat-Token':token,'Origin':'http://127.0.0.1:8765','Content-Type':'application/json'})
        yield client, engine, calls


def pause_before_insert(engine):
    """Block run() after it holds the lock and before it writes the task row."""
    entered, release = threading.Event(), threading.Event()
    original = engine.preview
    def paused(*args, **kwargs):
        entered.set()
        assert release.wait(10), 'test never released the paused request'
        return original(*args, **kwargs)
    engine.preview = paused
    return entered, release


def test_busy_is_a_conflict_subclass():
    assert issubclass(Busy, Conflict)


def test_same_id_retry_during_pre_persistence_window_reports_busy(api):
    client, engine, calls = api
    key = str(uuid.uuid4())
    body = {'id':key,'selection':'openai','prompt':'Hello'}
    entered, release = pause_before_insert(engine)
    outcome = {}
    def original():
        try: outcome['result'] = engine.run(key, 'openai', 'Hello')
        except Exception as exc: outcome['error'] = exc
    worker = threading.Thread(target=original)
    worker.start()
    try:
        assert entered.wait(10)
        retry = client.post('/api/chat', json=body)
        assert retry.status_code == 409
        assert retry.json()['code'] == 'engine_busy'
        assert client.get('/api/task/'+key).status_code == 404
        with pytest.raises(Busy): engine.run(key, 'openai', 'Hello')
    finally:
        release.set()
        worker.join(10)
    assert not worker.is_alive()
    assert 'error' not in outcome
    assert outcome['result']['status'] == 'finished'
    assert len(calls) == 1
    again = client.post('/api/chat', json=body)
    assert again.status_code == 200 and again.json() == outcome['result']
    assert len(calls) == 1


def test_other_conflicts_keep_a_distinct_code(api):
    client, engine, calls = api
    key = str(uuid.uuid4())
    assert client.post('/api/chat', json={'id':key,'selection':'openai','prompt':'Hello'}).status_code == 200
    mismatch = client.post('/api/chat', json={'id':key,'selection':'openai','prompt':'Changed'})
    assert mismatch.status_code == 409 and mismatch.json()['code'] == 'request_conflict'
    assert client.post('/api/chat', json={'id':key,'selection':'openai','prompt':'Hello'}).status_code == 200
    assert len(calls) == 1


def test_stale_context_rejection_is_not_busy_and_leaves_no_row(api):
    client, engine, calls = api
    project = client.post('/api/projects', json={'name':'Studio','notes':'Notes'}).json()
    cid = client.post('/api/conversations', json={'project_id':project['id'],'title':'Plan'}).json()['id']
    body = {'conversation_id':cid,'selection':'openai','prompt':'Go','selected_ids':[],'include_notes':True}
    preview = client.post('/api/context-preview', json=body).json()
    client.post('/api/projects', json={'id':project['id'],'name':'Studio','notes':'Edited'})
    key = str(uuid.uuid4())
    stale = client.post('/api/chat', json={**body,'id':key,'context_hash':preview['context_hash']})
    assert stale.status_code == 409 and stale.json()['code'] == 'request_conflict'
    assert client.get('/api/task/'+key).status_code == 404
    assert calls == []


def frontend(scenario):
    if not Path(JSC).exists():
        pytest.skip('JavaScriptCore jsc is unavailable; frontend behaviour unverified')
    result = subprocess.run([JSC, str(ROOT/'tests/test_retry_race_frontend.js'), '--',
                             str(ROOT/'src/ai_stack/web/app.js'), scenario],
                            capture_output=True, text=True, timeout=30)
    assert f'PASS {scenario}' in result.stdout, result.stdout + result.stderr


@pytest.mark.parametrize('scenario', ['busy_absent_row_keeps_pending', 'api_error_keeps_code',
                                      'reload_with_absent_row_keeps_pending', 'stale_context_allows_fresh_review',
                                      'recorded_conflict_keeps_pending', 'bad_request_clears_pending',
                                      'reload_shows_saved_context_then_retry_sends_exact_payload',
                                      'summary_quick_prompt', 'summary_notes_only', 'summary_exchanges_only',
                                      'summary_notes_and_exchanges', 'summary_survives_busy_and_404_then_stale_clears',
                                      'refresh_completion_clears_summary_and_next_request_is_fresh'])
def test_frontend_retry_handling(scenario):
    frontend(scenario)
