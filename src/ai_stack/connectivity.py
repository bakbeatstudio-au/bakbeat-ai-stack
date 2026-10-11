"""Read-only provider authentication checks; never return headers or raw errors."""
import httpx
from .credentials import SERVICE, backend

ENDPOINTS = {
    'openai': 'https://api.openai.com/v1/models',
    'anthropic': 'https://api.anthropic.com/v1/models',
}


def check_provider(provider, store=None, transport=None):
    if provider not in ENDPOINTS:
        raise ValueError('Unknown provider.')
    try:
        store = backend() if store is None else store
        secret = store.get_password(SERVICE, provider)
        if not secret:
            return {'provider': provider, 'status': 'missing_key'}
        headers = ({'Authorization': f'Bearer {secret}'} if provider == 'openai'
                   else {'x-api-key': secret, 'anthropic-version': '2023-06-01'})
        models = []
        params = {'limit': 100} if provider == 'anthropic' else {}
        # Disable redirects/proxies so credentials only reach the fixed provider.
        with httpx.Client(timeout=20, follow_redirects=False, trust_env=False,
                          transport=transport) as client:
            for _ in range(10):
                response = client.get(ENDPOINTS[provider], headers=headers, params=params)
                if response.status_code != 200:
                    return {'provider': provider, 'status': 'http_error', 'http_status': response.status_code}
                data = response.json()
                for item in data['data']:
                    model_id = item.get('id')
                    if isinstance(model_id, str) and len(model_id) < 200:
                        models.append(model_id)
                if provider == 'openai' or not data.get('has_more'):
                    return {'provider': provider, 'status': 'authenticated', 'models': sorted(set(models))}
                cursor = data.get('last_id')
                if not isinstance(cursor, str) or not cursor:
                    break
                params['after_id'] = cursor
        return {'provider': provider, 'status': 'incomplete_model_list'}
    except httpx.TimeoutException:
        return {'provider': provider, 'status': 'timeout'}
    except httpx.RequestError:
        return {'provider': provider, 'status': 'network_error'}
    except Exception:
        return {'provider': provider, 'status': 'check_failed'}
