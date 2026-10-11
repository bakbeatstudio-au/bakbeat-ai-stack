import importlib.util
import json
from pathlib import Path
import re
import sqlite3
import uuid

import pytest
from fastapi.testclient import TestClient

from ai_stack.engine import Engine
from ai_stack.server import create_app


@pytest.fixture
def api(tmp_path):
    calls = []
    def answer(provider, model, prompt, key, limit):
        calls.append((provider, prompt))
        return {'status':'completed','text':provider+' reply', 'usage':{'input_tokens':5,'output_tokens':3}}
    engine = Engine(tmp_path/'test.db', keys={'openai':'fake','anthropic':'fake'}, generator=answer, initialize_db=False)
    with TestClient(create_app(engine), base_url='http://127.0.0.1:8765') as client:
        token = re.search('name="bakbeat-token" content="([^"]+)"',client.get('/').text).group(1)
        client.headers.update({'X-BakBeat-Token':token,'Origin':'http://127.0.0.1:8765','Content-Type':'application/json'})
        yield client,engine,calls


def create(client):
    project=client.post('/api/projects',json={'name':'Studio','notes':'Selected notes'}).json()
    conversation=client.post('/api/conversations',json={'project_id':project['id'],'title':'Planning'}).json()
    return project,conversation


def test_full_api_flow_and_exact_reviewed_payload(api):
    client,engine,calls=api
    project,conversation=create(client)
    cid=conversation['id']
    body={'conversation_id':cid,'selection':'both','prompt':'Plan a session','selected_ids':[],'include_notes':False}
    preview=client.post('/api/context-preview',json=body).json()
    first=client.post('/api/chat',json={**body,'id':str(uuid.uuid4()),'context_hash':preview['context_hash']})
    assert first.status_code==200
    assert len(calls)==2
    body.update(prompt='Continue',selected_ids=[first.json()['id']],include_notes=True)
    preview=client.post('/api/context-preview',json=body).json()
    request={**body,'id':str(uuid.uuid4()),'context_hash':preview['context_hash']}
    result=client.post('/api/chat',json=request)
    assert result.status_code==200
    assert dict(calls[-2:])==preview['prompts']
    for call in result.json()['calls']:
        assert call['reserved_usd']==preview['reserved_usd'][call['provider']]
    assert client.post('/api/chat',json=request).json()==result.json()
    assert len(calls)==4
    assert client.get('/api/task/'+request['id']).json()==result.json()
    assert len(client.get('/api/conversation',params={'conversation_id':cid}).json()['tasks'])==2
    assert client.get('/api/projects').json()['projects']==[project]
    assert client.get('/api/conversations',params={'project_id':project['id']}).json()['conversations']==[conversation]
    assert engine.conversation_tasks(cid)[1]['context']['notes_included'] is True


@pytest.mark.parametrize('path,body',[
    ('/api/projects',{'name':'valid','id':{}}),
    ('/api/projects',{'name':'valid','notes':8000}),
    ('/api/projects',{'name':'','notes':''}),
    ('/api/projects',{'name':'valid','notes':'x'*8001}),
    ('/api/conversations',{'project_id':[],'title':'x'}),
    ('/api/conversations',{'project_id':'missing','title':'x'}),
    ('/api/context-preview',{'conversation_id':None,'selection':'openai','prompt':'x','selected_ids':{},'include_notes':False}),
    ('/api/context-preview',{'conversation_id':None,'selection':'openai','prompt':'x','selected_ids':[],'include_notes':'false'}),
])
def test_invalid_inputs_rejected_before_any_call(api,path,body):
    client,engine,calls=api
    assert client.post(path,json=body).status_code==400
    assert not calls


def test_context_endpoints_keep_local_boundary(api):
    client,engine,calls=api
    for path in ['/api/projects','/api/conversations','/api/context-preview']:
        assert client.post(path,json={},headers={'Origin':'https://evil.example'}).status_code==403
        assert client.post(path,json={},headers={'X-BakBeat-Token':''}).status_code==403
    assert not calls


def test_cross_conversation_stale_notes_and_budget_rejected(api):
    client,engine,calls=api
    project,conversation=create(client)
    body={'conversation_id':conversation['id'],'selection':'openai','prompt':'Go','selected_ids':[],'include_notes':True}
    preview=client.post('/api/context-preview',json=body).json()
    client.post('/api/projects',json={**project,'notes':'changed'})
    assert client.post('/api/chat',json={**body,'id':str(uuid.uuid4()),'context_hash':preview['context_hash']}).status_code==409
    assert not calls
    old=engine.run(str(uuid.uuid4()),'openai','independent')
    assert client.post('/api/context-preview',json={**body,'selected_ids':[old['id']]}).status_code==400
    with engine.connect() as c: c.execute('UPDATE calls SET cost_usd=2')
    preview=client.post('/api/context-preview',json=body).json()
    assert preview['within_allowance'] is False
    assert client.post('/api/chat',json={**body,'id':str(uuid.uuid4()),'context_hash':preview['context_hash']}).status_code==400
    assert len(calls)==1


def test_migration_from_original_schema_preserves_rows(api):
    client,engine,calls=api
    original=engine.run(str(uuid.uuid4()),'openai','Legacy')
    with engine.connect() as c:
        c.executescript('DROP TABLE task_context; DROP TABLE conversations; DROP TABLE projects; DELETE FROM schema_metadata WHERE version=2;')
    restored=Engine(engine.db,keys={},initialize_db=False)
    assert restored.get(original['id'])==original
    with restored.connect() as c:
        assert c.execute('SELECT MAX(version) FROM schema_metadata').fetchone()[0]==2
        assert c.execute('PRAGMA integrity_check').fetchone()[0]=='ok'


def test_backup_snapshot_counts_include_conversation_data(api,tmp_path):
    client,engine,calls=api
    _,conversation=create(client)
    body={'conversation_id':conversation['id'],'selection':'openai','prompt':'Go','selected_ids':[],'include_notes':True}
    preview=client.post('/api/context-preview',json=body).json()
    client.post('/api/chat',json={**body,'id':str(uuid.uuid4()),'context_hash':preview['context_hash']})
    spec=importlib.util.spec_from_file_location('backup_under_test',Path(__file__).parents[1]/'scripts/backup.py')
    backup=importlib.util.module_from_spec(spec);spec.loader.exec_module(backup)
    restored=tmp_path/'restored.db'
    with sqlite3.connect(engine.db) as source,sqlite3.connect(restored) as target: source.backup(target)
    counts=backup.read_database(restored)
    assert counts==backup.read_database(engine.db)
    assert counts['projects']==counts['conversations']==counts['task_context']==1
