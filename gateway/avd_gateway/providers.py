# avd-swarm gateway: model providers. Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
"""The providers a model call can go to, behind one interface (core-and-adapters.md section 3.4).

Each declares how it signs in (always an Entra-based token, never a key), the model IDs it serves and
the adapter that must be turned on before it is used. `anthropic` is the default and needs no adapter;
`foundry` is the model-host adapter (adapters/foundry), off by default. Every model a provider serves
needs a price row in governance/model-prices.yml, or the call is refused.
"""

from __future__ import annotations

from dataclasses import dataclass

from .prices import PriceBook, SpendRefused


@dataclass(frozen=True)
class Provider:
    name: str
    auth: str              # how the caller signs in; no provider takes a key
    models: tuple[str, ...]
    adapter: str | None    # the adapter that must be on, or None for the core default


CLAUDE = ("claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5")

PROVIDERS = {
    "anthropic": Provider("anthropic", "anthropic-workload-identity-federation", CLAUDE, None),
    "foundry": Provider("foundry", "entra-managed-identity", CLAUDE, "foundry"),
    # No models yet: listed so the interface is fixed before anything uses it.
    "azure-openai": Provider("azure-openai", "entra-managed-identity", (), "foundry"),
}


def missing_prices(provider: Provider, prices: PriceBook) -> list[str]:
    """The models a provider serves that have no price row; those calls would be refused."""
    return [m for m in provider.models if not prices.has(provider.name, m)]


def usable(name: str, model: str, enabled_adapters: set[str] | frozenset[str],
           providers: dict[str, Provider] = PROVIDERS) -> Provider:
    """The provider for a call, or SpendRefused when it is unknown, its adapter is off or it doesn't serve the model."""
    provider = providers.get(name)
    if provider is None:
        raise SpendRefused(f"unknown model provider {name!r}")
    if provider.adapter and provider.adapter not in enabled_adapters:
        raise SpendRefused(f"provider {name} needs the {provider.adapter} adapter, which is off")
    if model not in provider.models:
        raise SpendRefused(f"provider {name} doesn't serve {model}")
    return provider
