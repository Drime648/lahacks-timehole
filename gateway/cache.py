from __future__ import annotations

import threading
from dataclasses import dataclass, field
from time import monotonic


@dataclass
class CachedDecision:
    blocked: bool
    expires_at: float
    config_version: str | None = None


@dataclass
class CachedLlmDecision:
    decision: str
    expires_at: float
    config_version: str | None = None


@dataclass
class SourceIpCache:
    decisions: dict[str, CachedDecision] = field(default_factory=dict)
    llm_decisions: dict[str, CachedLlmDecision] = field(default_factory=dict)


class DecisionCache:
    def __init__(self, ttl_seconds: float):
        self.ttl_seconds = ttl_seconds
        self._cache: dict[str, SourceIpCache] = {}
        self._lock = threading.Lock()

    @property
    def entries(self) -> dict[str, SourceIpCache]:
        with self._lock:
            return dict(self._cache)

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()

    def _get_source_cache_locked(self, source_ip: str) -> SourceIpCache:
        cache = self._cache.get(source_ip)
        if cache is None:
            cache = SourceIpCache()
            self._cache[source_ip] = cache
        return cache

    def get_cached_decision(
        self,
        source_ip: str,
        query_name: str,
        config_version: str | None = None,
    ) -> bool | None:
        with self._lock:
            cache = self._cache.get(source_ip)
            if cache is None:
                return None

            decision = cache.decisions.get(query_name)
            if decision is None:
                return None

            if decision.expires_at <= monotonic() or decision.config_version != config_version:
                del cache.decisions[query_name]
                if not cache.decisions and not cache.llm_decisions:
                    del self._cache[source_ip]
                return None

            return decision.blocked

    def cache_decision(
        self,
        source_ip: str,
        query_name: str,
        blocked: bool,
        config_version: str | None = None,
    ) -> None:
        with self._lock:
            cache = self._get_source_cache_locked(source_ip)
            cache.decisions[query_name] = CachedDecision(
                blocked=blocked,
                expires_at=monotonic() + self.ttl_seconds,
                config_version=config_version,
            )

    def get_cached_llm_decision(
        self,
        source_ip: str,
        cache_key: str,
        config_version: str | None = None,
    ) -> str | None:
        with self._lock:
            cache = self._cache.get(source_ip)
            if cache is None:
                return None

            decision = cache.llm_decisions.get(cache_key)
            if decision is None:
                return None

            if decision.expires_at <= monotonic() or decision.config_version != config_version:
                del cache.llm_decisions[cache_key]
                if not cache.decisions and not cache.llm_decisions:
                    del self._cache[source_ip]
                return None

            return decision.decision

    def cache_llm_decision(
        self,
        source_ip: str,
        cache_key: str,
        decision: str,
        config_version: str | None = None,
    ) -> None:
        with self._lock:
            cache = self._get_source_cache_locked(source_ip)
            cache.llm_decisions[cache_key] = CachedLlmDecision(
                decision=decision,
                expires_at=monotonic() + self.ttl_seconds,
                config_version=config_version,
            )
