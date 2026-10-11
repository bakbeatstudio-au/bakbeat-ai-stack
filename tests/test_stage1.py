import json
import os
from pathlib import Path
import re
import subprocess
import sys
import uuid
import httpx
import pytest
from fastapi.testclient import TestClient
from ai_stack.engine import Engine, Conflict, Limit
from ai_stack.providers import generate, cost
from ai_stack.server import create_app


def good(*args):
    return dict(status='completed', text='Hello', usage={'input_tokens':10,'output_tokens':5}, error=None)


@pytest.fixture
def engine(tmp_path):
    return Engine(tmp_path/'test.db', keys={'openai':'fake','anthropic':'fake'}, generator=good, initialize_db=False)


def test_duplicate_no_second_spend(engine):
    calls=[]
    engine.generator=lambda *a: calls.append(a) or good()
    key=str(uuid.uuid4())
    first=engine.run(key,'both','hello')
    assert engine.run(key,'both','hello')==first
    assert len(calls)==2
    with pytest.raises(Conflict): engine.run(key,'both','changed')


def test_both_partial_failure(engine):
    def mixed(provider,*_):
        if provider=='anthropic': raise RuntimeError('fake-secret')
        return good()
    engine.generator=mixed
    result=engine.run(str(uuid.uuid4()),'both','hello')
    assert {c['status'] for c in result['calls']}=={'completed','unknown'}
    assert 'fake-secret' not in json.dumps(result)
    assert engine.history()['allowance_used_usd']>0


def test_budget_before_network(engine):
    with engine.connect() as c:
        c.execute("INSERT INTO tasks VALUES ('old','both','x',64,'finished','2026')")
        c.execute("INSERT INTO calls (id,task_id,provider,role,model,status,reserved_usd,pricing_json,created_at) VALUES ('old','old','openai','primary','m','unknown',2,'{}','2026')")
    engine.generator=lambda *_: pytest.fail('Network must not run')
    with pytest.raises(Limit): engine.run(str(uuid.uuid4()),'openai','hello')


def test_provider_io_outside_transaction(engine):
    def independent_write(*_):
        with engine.connect() as c:
            c.execute("UPDATE tasks SET status='running'")
        return good()
    engine.generator=independent_write
    assert engine.run(str(uuid.uuid4()),'both','hello')['status']=='finished'


def test_input_limit(engine):
    with pytest.raises(Limit): engine.run(str(uuid.uuid4()),'openai','💛'*3000)


def test_costs_cached_and_missing():
    rate={'input':4,'cached_input':.4,'cache_write':5,'output':20}
    assert cost('openai',{'input_tokens':100,'output_tokens':10,'input_tokens_details':{'cached_tokens':50}},rate)==pytest.approx(.00042)
    assert cost('openai',{},rate) is None
    assert cost('openai',{'input_tokens':-1,'output_tokens':10},rate) is None


@pytest.mark.parametrize('provider', ['openai','anthropic'])
def test_adapter_limits_and_no_tools(provider):
    def handler(request):
        body=json.loads(request.content)
        assert 'tools' not in body
        assert body.get('max_tokens',body.get('max_output_tokens'))==64
        if provider=='openai':
            assert body['store'] is False
            data={'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':'OK'}]}]}
        else:
            assert body['thinking'] == {'type': 'adaptive'}
            assert body['output_config'] == {'effort': 'low'}
            data={'stop_reason':'end_turn','content':[{'type':'text','text':'OK'}]}
        data['usage']={'input_tokens':3,'output_tokens':1}
        return httpx.Response(200,json=data)
    result=generate(provider,'model','hi','secret',64,httpx.MockTransport(handler))
    assert result['text']=='OK'


def test_failure_body_hidden():
    result=generate('openai','model','hi','secret',64,httpx.MockTransport(lambda _:httpx.Response(401,text='secret')))
    assert result['error']=='authentication_failed'
    assert 'secret' not in str(result)


@pytest.fixture
def browser(engine):
    with TestClient(create_app(engine),base_url='http://127.0.0.1:8765') as c:
        yield c


def auth(browser):
    token=re.search('name="bakbeat-token" content="([^"]+)"',browser.get('/').text).group(1)
    return {'X-BakBeat-Token':token,'Origin':'http://127.0.0.1:8765','Content-Type':'application/json'}


def test_web_boundaries(browser):
    assert browser.get('/',headers={'Host':'evil.example'}).status_code==403
    assert browser.get('/api/history').status_code==403
    headers=auth(browser)
    assert browser.get('/api/history',headers=headers).status_code==200
    headers['Origin']='https://evil.example'
    assert browser.post('/api/chat',headers=headers,json={}).status_code==403


def test_web_chat_and_size(browser):
    headers=auth(browser)
    result=browser.post('/api/chat',headers=headers,json={'id':str(uuid.uuid4()),'selection':'both','prompt':'hello'})
    assert result.status_code==200
    assert len(result.json()['calls'])==2
    assert browser.post('/api/chat',headers=headers,content='x'*16001).status_code==413


def test_killed_call_remains_visible_and_not_retried(tmp_path):
    db=tmp_path/'interrupted.db'
    key=str(uuid.uuid4())
    script='''
import os,sys
from ai_stack.engine import Engine
def die(*args): os._exit(23)
e=Engine(sys.argv[1],keys={'openai':'fake'},generator=die,initialize_db=False)
e.run(sys.argv[2],'openai','kill test')
'''
    env=dict(os.environ,PYTHONPATH=str(Path(__file__).resolve().parents[1]/'src'))
    result=subprocess.run([sys.executable,'-c',script,str(db),key],env=env)
    assert result.returncode==23
    e=Engine(db,keys={'openai':'fake'},generator=lambda *_:pytest.fail('Do not retry'),initialize_db=False)
    task=e.run(key,'openai','kill test')
    assert task['status']=='interrupted'
    assert task['calls'][0]['status']=='unknown'
    assert task['calls'][0]['cost_usd'] is None
    assert e.history()['allowance_used_usd']>0


def test_billing_error_is_categorized_without_echo():
    result=generate('openai','model','hi','secret',64,httpx.MockTransport(lambda _:httpx.Response(429,json={'error':{'code':'insufficient_quota','message':'secret'}})))
    assert result['error']=='billing_or_credits_required'
    assert 'secret' not in str(result)
