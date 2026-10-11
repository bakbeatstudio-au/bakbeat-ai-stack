from unittest.mock import Mock
import httpx
import pytest
from ai_stack.connectivity import check_provider


@pytest.mark.parametrize('provider', ['openai', 'anthropic'])
def test_authenticated_without_secret_in_result(provider):
    store = Mock()
    store.get_password.return_value = 'test-secret'
    def handler(request):
        assert request.method == 'GET'
        assert request.url.path == '/v1/models'
        assert request.url.host in ('api.openai.com', 'api.anthropic.com')
        return httpx.Response(200, json={'data': [{'id': 'model-v1'}], 'has_more': False})
    result = check_provider(provider, store, httpx.MockTransport(handler))
    assert result['status'] == 'authenticated'
    assert result['models'] == ['model-v1']
    assert 'test-secret' not in str(result)


@pytest.mark.parametrize('code', [301, 401, 403, 429, 500])
def test_errors_do_not_expose_response_body(code):
    store = Mock()
    store.get_password.return_value = 'test-secret'
    result = check_provider('openai', store, httpx.MockTransport(
        lambda _: httpx.Response(code, text='test-secret', headers={'Location': 'https://example.com'})))
    assert result == {'provider': 'openai', 'status': 'http_error', 'http_status': code}


def test_keychain_exception_suppressed():
    store = Mock()
    store.get_password.side_effect = RuntimeError('test-secret')
    assert check_provider('openai', store) == {'provider': 'openai', 'status': 'check_failed'}


def test_anthropic_pagination():
    store = Mock()
    store.get_password.return_value = 'test-secret'
    def handler(request):
        if 'after_id' not in request.url.params:
            return httpx.Response(200, json={'data': [{'id': 'first'}], 'has_more': True, 'last_id': 'first'})
        assert request.url.params['after_id'] == 'first'
        return httpx.Response(200, json={'data': [{'id': 'second'}], 'has_more': False})
    result = check_provider('anthropic', store, httpx.MockTransport(handler))
    assert result['models'] == ['first', 'second']
