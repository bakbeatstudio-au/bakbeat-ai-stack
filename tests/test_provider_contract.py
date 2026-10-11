from unittest.mock import Mock
import pytest
from ai_stack.provider_contract import (ContextItem, ModelRequest, ProviderSpec,
    ProviderRegistry, ProviderPolicyError, ProviderUnavailable)
from ai_stack.providers import generate, REGISTRY, cost


def request(provider='openai', **kwargs):
    return ModelRequest(provider, 'explicit-model', 'hello', 64, **kwargs)


@pytest.mark.parametrize('provider', ['openai', 'anthropic'])
@pytest.mark.parametrize('policy', [{'offline_required': True}, {'privacy_mode':'offline_only'}, {'privacy_mode':'local_preferred'}])
def test_cloud_policy_blocks_before_adapter(provider, policy):
    registry=ProviderRegistry()
    spec=ProviderSpec(provider,'cloud',True)
    adapter=Mock()
    adapter.spec=spec
    registry.declare(spec,adapter)
    with pytest.raises(ProviderPolicyError): registry.dispatch(request(provider,**policy),key='fake')
    adapter.generate.assert_not_called()


def test_planned_local_fails_without_fallback():
    with pytest.raises(ProviderUnavailable): REGISTRY.dispatch(request('local',offline_required=True))
    with pytest.raises(ProviderUnavailable): generate('local','model','hello',None,64)


def test_future_local_adapter_needs_no_cloud_credentials():
    registry=ProviderRegistry()
    spec=ProviderSpec('local','local',False)
    class FakeLocal:
        def generate(self, request, *, key, transport=None):
            assert key is None
            assert request.context[0].source_id=='manual:page4'
            return {'status':'completed','text':'local answer','usage':None}
    adapter=FakeLocal()
    adapter.spec=spec
    registry.declare(spec,adapter)
    result=registry.dispatch(request('local',offline_required=True,context=(ContextItem('private passage','manual:page4'),)),key='cloud-secret')
    assert result['text']=='local answer'


@pytest.mark.parametrize('kwargs', [{'tools_allowed':('read_file',)}, {'capabilities':frozenset({'embeddings'})}, {'context':(ContextItem('private','doc:1'),)}])
def test_unsupported_request_fails_closed(kwargs):
    with pytest.raises(ProviderPolicyError): REGISTRY.dispatch(request(**kwargs),key='fake')


def test_stage1_does_not_silently_drop_context():
    with pytest.raises(ProviderPolicyError):
        REGISTRY.dispatch(request(context=(ContextItem('approved','doc:1',True),)),key='fake')


def test_local_cost_not_assumed_free_or_anthropic():
    assert cost('local',{'input_tokens':1,'output_tokens':1},{}) is None
