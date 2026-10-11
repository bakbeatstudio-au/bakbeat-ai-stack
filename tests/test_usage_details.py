"""Malformed optional usage details must not lose a paid answer or crash cost estimation."""
import json
import uuid
import pytest
from ai_stack.engine import Engine
from ai_stack.providers import cost

RATE = {'input': 1.0, 'cached_input': 0.5, 'cache_write': 2.0, 'output': 4.0}
NULL_DETAILS = {'input_tokens': 10, 'output_tokens': 5, 'input_tokens_details': None}


@pytest.fixture
def engine(tmp_path):
    calls = []
    def answer(provider, model, prompt, key, max_output):
        calls.append(provider)
        return {'status': 'completed', 'text': 'Kept answer', 'usage': dict(NULL_DETAILS)}
    engine = Engine(tmp_path/'test.db', keys={'openai': 'fake', 'anthropic': 'fake'},
                    generator=answer, initialize_db=False)
    return engine, calls


@pytest.mark.parametrize('details', [None, [], 'cached', 3])
def test_uninterpretable_openai_details_give_unknown_cost(details):
    assert cost('openai', {'input_tokens': 10, 'output_tokens': 5, 'input_tokens_details': details}, RATE) is None


@pytest.mark.parametrize('usage', [
    {'input_tokens': 10, 'output_tokens': 5, 'input_tokens_details': {'cached_tokens': None}},
    {'input_tokens': '10', 'output_tokens': 5},
    {'input_tokens': True, 'output_tokens': 5},
    {'input_tokens': 10, 'output_tokens': 5.0},
])
def test_uninterpretable_token_counts_give_unknown_cost(usage):
    assert cost('openai', usage, RATE) is None


def test_valid_calculations_unchanged():
    assert cost('openai', {'input_tokens': 10, 'output_tokens': 5}, RATE) == pytest.approx(30 / 1_000_000)
    assert cost('openai', {'input_tokens': 10, 'output_tokens': 5, 'input_tokens_details': {}}, RATE) == pytest.approx(30 / 1_000_000)
    assert cost('openai', {'input_tokens': 10, 'output_tokens': 5,
                           'input_tokens_details': {'cached_tokens': 4}}, RATE) == pytest.approx(28 / 1_000_000)
    assert cost('anthropic', {'input_tokens': 10, 'output_tokens': 5, 'cache_read_input_tokens': 4,
                              'cache_creation_input_tokens': 2}, RATE) == pytest.approx(36 / 1_000_000)


def test_null_details_keep_answer_finish_task_and_reserve_allowance(engine):
    engine, calls = engine
    key = str(uuid.uuid4())
    result = engine.run(key, 'openai', 'Hello')
    assert result['status'] == 'finished'
    [call] = result['calls']
    assert call['status'] == 'completed'
    assert call['text'] == 'Kept answer'
    assert call['usage'] == NULL_DETAILS
    assert call['cost_usd'] is None
    # Unknown cost falls back to the conservative reservation.
    assert call['reserved_usd'] > 0
    assert engine.history()['allowance_used_usd'] == pytest.approx(call['reserved_usd'])
    with engine.connect() as c:
        row = c.execute('SELECT usage_json,cost_usd FROM calls WHERE task_id=?', (key,)).fetchone()
    assert json.loads(row['usage_json']) == NULL_DETAILS and row['cost_usd'] is None
    assert calls == ['openai']


def test_retry_with_same_id_makes_no_second_call(engine):
    engine, calls = engine
    key = str(uuid.uuid4())
    first = engine.run(key, 'openai', 'Hello')
    engine.generator = lambda *_: pytest.fail('No duplicate provider call')
    assert engine.run(key, 'openai', 'Hello') == first
    assert calls == ['openai']
