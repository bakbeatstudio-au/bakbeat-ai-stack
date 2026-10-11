"""Provider-independent requests and policy checks. No network or discovery here."""
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ContextItem:
    text: str
    source_id: str
    cloud_allowed: bool = False


@dataclass(frozen=True)
class ModelRequest:
    provider: str
    model: str
    prompt: str
    max_output: int
    role: str = 'primary'
    capabilities: frozenset[str] = frozenset({'text'})
    context: tuple[ContextItem, ...] = ()
    tools_allowed: tuple[str, ...] = ()
    privacy_mode: str = 'online'
    offline_required: bool = False


@dataclass(frozen=True)
class ProviderSpec:
    id: str
    execution: str  # cloud or local; local implementations must enforce loopback.
    credential_required: bool
    capabilities: frozenset[str] = frozenset({'text'})


class Adapter(Protocol):
    spec: ProviderSpec

    def generate(self, request: ModelRequest, *, key: str | None, transport=None) -> dict:
        """Return normalized status/text/error/usage/model/latency fields."""
        ...


class ProviderUnavailable(ValueError):
    pass


class ProviderPolicyError(ValueError):
    pass


class ProviderRegistry:
    def __init__(self):
        self._specs = {}
        self._adapters = {}

    def declare(self, spec: ProviderSpec, adapter: Adapter | None = None):
        if spec.id in self._specs or spec.execution not in ('local', 'cloud'):
            raise ValueError('Invalid or duplicate provider declaration.')
        if adapter is not None and adapter.spec != spec:
            raise ValueError('Adapter specification mismatch.')
        self._specs[spec.id] = spec
        if adapter is not None:
            self._adapters[spec.id] = adapter

    def dispatch(self, request: ModelRequest, *, key=None, transport=None):
        spec = self._specs.get(request.provider)
        if spec is None:
            raise ProviderUnavailable('Unknown provider.')
        if request.privacy_mode not in ('online', 'local_preferred', 'offline_only'):
            raise ProviderPolicyError('Unknown privacy mode.')
        if (request.offline_required or request.privacy_mode == 'offline_only') and spec.execution != 'local':
            raise ProviderPolicyError('Offline-only requests cannot use cloud providers.')
        if request.privacy_mode == 'local_preferred' and spec.execution != 'local':
            raise ProviderPolicyError('Select online explicitly to permit cloud use; no silent fallback.')
        if request.tools_allowed:
            raise ProviderPolicyError('Tools remain disabled until the Tool Broker is implemented.')
        if not request.capabilities <= spec.capabilities:
            raise ProviderPolicyError('Provider does not support the requested capabilities.')
        if spec.execution == 'cloud' and any(not item.cloud_allowed for item in request.context):
            raise ProviderPolicyError('Private context cannot leave the machine.')
        adapter = self._adapters.get(spec.id)
        if adapter is None:
            raise ProviderUnavailable('Provider is planned but not enabled.')
        if not request.model or not request.role or type(request.max_output) is not int or request.max_output <= 0:
            raise ProviderPolicyError('Explicit model, role and positive output limit required.')
        if spec.credential_required and not key:
            raise ProviderPolicyError('Provider credential unavailable.')
        # Never forward a cloud credential to a credential-free local adapter.
        return adapter.generate(request, key=key if spec.credential_required else None, transport=transport)
