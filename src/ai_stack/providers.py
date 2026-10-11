"""Single bounded HTTPS call. No retries, redirects, tools or raw error logs."""
import time
import httpx


def _generate_cloud(provider, model, prompt, key, max_output, transport=None):
    start = time.monotonic()
    result = dict(status='error', text='', error='provider_error', usage=None,
                  resolved_model=None, finish_reason=None)
    try:
        if provider == 'openai':
            url = 'https://api.openai.com/v1/responses'
            headers = {'Authorization': f'Bearer {key}'}
            body = {'model': model, 'input': prompt, 'max_output_tokens': max_output,
                    'reasoning': {'effort': 'none'}, 'store': False}
        elif provider == 'anthropic':
            url = 'https://api.anthropic.com/v1/messages'
            headers = {'x-api-key': key, 'anthropic-version': '2023-06-01'}
            body = {'model': model, 'messages': [{'role': 'user', 'content': prompt}],
                    'max_tokens': max_output, 'thinking': {'type': 'adaptive'},
                    'output_config': {'effort': 'low'}}
        else:
            raise ValueError('Unknown provider')
        with httpx.Client(timeout=httpx.Timeout(90, connect=15), trust_env=False,
                          follow_redirects=False, transport=transport) as client:
            response = client.post(url, headers=headers, json=body)
        if response.status_code != 200:
            # Error bodies can echo user input/credentials. Keep only category.
            result['error'] = {401: 'authentication_failed', 403: 'access_denied',
                               402: 'billing_required', 429: 'quota_or_rate_limit'}.get(
                                   response.status_code, f'http_{response.status_code}')
            try:
                error = response.json().get('error', {})
                code = str(error.get('code', error.get('type', '')))
                message = str(error.get('message', '')).lower()
                if code == 'insufficient_quota' or 'credit balance' in message or 'billing' in message:
                    result['error'] = 'billing_or_credits_required'
                elif 'thinking' in message:
                    result['error'] = 'thinking_setting_rejected'
                elif 'max_tokens' in message or 'max_output_tokens' in message:
                    result['error'] = 'output_limit_rejected'
                elif 'model' in message and ('not found' in message or 'access' in message):
                    result['error'] = 'model_access_unavailable'
            except Exception:
                pass
        else:
            data = response.json()
            result['usage'] = data.get('usage')
            result['resolved_model'] = data.get('model', model)
            if provider == 'openai':
                text = '\n'.join(part.get('text', part.get('refusal', ''))
                                 for item in data.get('output', []) if item.get('type') == 'message'
                                 for part in item.get('content', [])
                                 if part.get('type') in ('output_text', 'refusal'))
                finish = data.get('status', 'unknown')
            else:
                text = '\n'.join(p.get('text', '') for p in data.get('content', []) if p.get('type') == 'text')
                finish = data.get('stop_reason', 'unknown')
            result.update(text=text, finish_reason=finish, error=None,
                          status='completed' if text else 'incomplete')
            if finish in ('incomplete', 'max_tokens'):
                result['status'] = 'incomplete'
    except httpx.TimeoutException:
        result.update(error='timeout_usage_unknown', status='unknown')
    except httpx.RequestError:
        result.update(error='connection_usage_unknown', status='unknown')
    except Exception:
        result.update(error='response_unreadable_usage_unknown', status='unknown')
    result['latency_ms'] = round((time.monotonic() - start) * 1000)
    return result


def cost(provider, usage, rate):
    """USD estimate from returned tokens; retain raw usage separately."""
    if provider not in ('openai', 'anthropic') or not isinstance(usage, dict):
        return None
    try:
        incoming, outgoing = usage['input_tokens'], usage['output_tokens']
        if provider == 'openai':
            details = usage.get('input_tokens_details', {})
            # Present but uninterpretable details make the split unreliable.
            if not isinstance(details, dict):
                return None
            cached = details.get('cached_tokens', 0)
            written = details.get('cache_creation_tokens', 0)
        else:
            cached = usage.get('cache_read_input_tokens', 0)
            written = usage.get('cache_creation_input_tokens', 0)
        if any(type(v) is not int or v < 0 for v in (incoming, cached, written, outgoing)):
            return None
        plain = incoming - cached - written if provider == 'openai' else incoming
        if plain < 0:
            return None
        return (plain * rate['input'] + cached * rate['cached_input'] +
                written * rate['cache_write'] + outgoing * rate['output']) / 1_000_000
    except (KeyError, TypeError):
        return None


# Stage 1 keeps its existing call signature while routing through a generic
# contract. Future adapters do not need to modify _generate_cloud.
from .provider_contract import (ModelRequest, ProviderSpec, ProviderRegistry,
                                ProviderPolicyError)


class CloudAdapter:
    def __init__(self, provider):
        self.spec = ProviderSpec(provider, 'cloud', True)

    def generate(self, request, *, key, transport=None):
        if request.context:
            raise ProviderPolicyError('Context serialization is not enabled in Stage 1.')
        return _generate_cloud(self.spec.id, request.model, request.prompt, key,
                               request.max_output, transport)


REGISTRY = ProviderRegistry()
for _provider in ('openai', 'anthropic'):
    _adapter = CloudAdapter(_provider)
    REGISTRY.declare(_adapter.spec, _adapter)
REGISTRY.declare(ProviderSpec('local', 'local', False))


def generate(provider, model, prompt, key, max_output, transport=None, *,
             role='primary', context=(), tools_allowed=(), privacy_mode='online',
             offline_required=False, capabilities=frozenset({'text'})):
    request = ModelRequest(provider, model, prompt, max_output, role,
                           frozenset(capabilities), tuple(context),
                           tuple(tools_allowed), privacy_mode, offline_required)
    return REGISTRY.dispatch(request, key=key, transport=transport)
