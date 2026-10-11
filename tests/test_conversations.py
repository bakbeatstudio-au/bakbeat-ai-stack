import json
import sqlite3
import uuid
import pytest
from ai_stack.engine import Engine, Conflict, Limit


@pytest.fixture
def engine(tmp_path):
    def answer(provider,model,prompt,key,max_output):
        return {'status':'completed','text':provider+' answer','usage':{'input_tokens':10,'output_tokens':5}}
    return Engine(tmp_path/'test.db',keys={'openai':'fake','anthropic':'fake'},generator=answer,initialize_db=False)


def conversation(engine, name='Studio'):
    p=engine.save_project(name,'private notes for '+name)
    return engine.create_conversation(p['id'],'Session')['id'],p


def send(engine,cid,prompt='Hello',ids=None,notes=False,provider='both',key=None):
    ids=ids or []
    preview=engine.preview(cid,provider,prompt,ids,notes)
    return engine.run(key or str(uuid.uuid4()),provider,prompt,conversation_id=cid,
                      selected_ids=ids,include_notes=notes,context_hash=preview['context_hash'])


def test_selected_context_separates_providers_and_projects(engine):
    cid,p=conversation(engine)
    other,_=conversation(engine,'Second project')
    previous=send(engine,cid)
    send(engine,other,'Second project confidential')
    preview=engine.preview(cid,'both','Continue',[previous['id']],True)
    assert 'openai answer' in preview['prompts']['openai']
    assert 'anthropic answer' not in preview['prompts']['openai']
    assert 'anthropic answer' in preview['prompts']['anthropic']
    assert 'Second project' not in json.dumps(preview)
    assert 'private notes for Studio' in preview['prompts']['openai']
    with pytest.raises(ValueError):engine.preview(other,'both','Continue',[previous['id']],False)


def test_notes_and_history_are_opt_in(engine):
    cid,_=conversation(engine)
    send(engine,cid,'A secret earlier prompt')
    preview=engine.preview(cid,'openai','Fresh',[],False)
    assert preview['prompts']=={'openai':'Fresh'}


def test_changed_notes_require_new_review(engine):
    cid,p=conversation(engine)
    preview=engine.preview(cid,'openai','Go',[],True)
    engine.save_project(p['name'],'changed',p['id'])
    engine.generator=lambda *_:pytest.fail('No provider call before renewed review')
    with pytest.raises(Conflict):engine.run(str(uuid.uuid4()),'openai','Go',conversation_id=cid,include_notes=True,context_hash=preview['context_hash'])


def test_duplicate_after_notes_edit_returns_original_snapshot(engine):
    cid,p=conversation(engine)
    preview=engine.preview(cid,'openai','Go',[],True)
    key=str(uuid.uuid4())
    result=engine.run(key,'openai','Go',conversation_id=cid,include_notes=True,context_hash=preview['context_hash'])
    engine.save_project(p['name'],'changed',p['id'])
    engine.generator=lambda *_:pytest.fail('No duplicate provider call')
    assert engine.run(key,'openai','Go',conversation_id=cid,include_notes=True,context_hash=preview['context_hash'])==result
    with engine.connect() as c:
        saved=c.execute('SELECT * FROM task_context WHERE task_id=?',(key,)).fetchone()
        assert saved['notes_snapshot']==p['notes']
        assert json.loads(saved['prompts_json'])==preview['prompts']
    with pytest.raises(Conflict):engine.run(key,'openai','Go',conversation_id=cid,include_notes=False,context_hash=preview['context_hash'])


def test_partial_failure_not_silently_used_for_both(engine):
    cid,_=conversation(engine)
    previous=send(engine,cid,provider='openai')
    with pytest.raises(ValueError):engine.preview(cid,'both','Continue',[previous['id']],False)
    assert 'openai answer' in engine.preview(cid,'openai','Continue',[previous['id']],False)['prompts']['openai']


def test_reservation_counts_accumulated_context(engine):
    cid,p=conversation(engine)
    first=send(engine,cid,provider='openai')
    engine.save_project(p['name'],'x'*7000,p['id'])
    second=send(engine,cid,ids=[first['id']],notes=True,provider='openai')
    assert second['calls'][0]['reserved_usd']>first['calls'][0]['reserved_usd']
    with engine.connect() as c:
        c.execute('UPDATE calls SET cost_usd=1 WHERE task_id=?',(second['id'],))
        c.execute('UPDATE calls SET cost_usd=.999 WHERE task_id=?',(first['id'],))
    engine.generator=lambda *_:pytest.fail('Budget must reject before provider call')
    with pytest.raises(Limit):send(engine,cid,notes=True,provider='openai')


def test_context_size_and_duplicate_selection_fail_closed(engine):
    cid,_=conversation(engine)
    previous=send(engine,cid)
    with pytest.raises(ValueError):engine.preview(cid,'openai','Go',[previous['id']]*2,False)
    with engine.connect() as c:c.execute("UPDATE calls SET text=?",('x'*33000,))
    with pytest.raises(ValueError):engine.preview(cid,'openai','Go',[previous['id']],False)


def test_reopen_preserves_legacy_and_conversations(engine):
    legacy=engine.run(str(uuid.uuid4()),'openai','Legacy')
    cid,_=conversation(engine)
    new=send(engine,cid)
    reopened=Engine(engine.db,keys={},initialize_db=False)
    assert reopened.get(legacy['id'])==legacy
    assert reopened.conversation_tasks(cid)==[new]
    with reopened.connect() as c:assert c.execute('PRAGMA foreign_key_check').fetchall()==[]
