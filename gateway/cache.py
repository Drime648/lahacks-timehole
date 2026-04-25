from __future__ import annotations

from dataclasses import dataclass, field
from time import monotonic


@dataclass
class CachedDecision:
    blocked: bool
    expires_at: float


@dataclass
class SourceIpCache:
    decisions: dict[str, CachedDecision] = field(default_factory=dict)


class DecisionCache:
    def __init__(self, ttl_seconds: float):
        self.ttl_seconds = ttl_seconds
        self._cache: dict[str, SourceIpCache] = {}

    @property
    def entries(self) -> dict[str, SourceIpCache]:
        return self._cache

    def clear(self) -> None:
        self._cache.clear()

    def get_source_cache(self, source_ip: str) -> SourceIpCache:
        cache = self._cache.get(source_ip)
        if cache is None:
            cache = SourceIpCache()
            self._cache[source_ip] = cache
        return cache

    def get_cached_decision(self, source_ip: str, query_name: str) -> bool | None:
        cache = self._cache.get(source_ip)
        if cache is None:
            return None

        decision = cache.decisions.get(query_name)
        if decision is None:
            return None

        if decision.expires_at <= monotonic():
            del cache.decisions[query_name]
            if not cache.decisions:
                del self._cache[source_ip]
            return None

        return decision.blocked

    def cache_decision(self, source_ip: str, query_name: str, blocked: bool) -> None:
        cache = self.get_source_cache(source_ip)
        cache.decisions[query_name] = CachedDecision(
            blocked=blocked,
            expires_at=monotonic() + self.ttl_seconds,
        )
