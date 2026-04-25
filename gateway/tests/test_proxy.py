from __future__ import annotations

from datetime import datetime as real_datetime

from gateway.proxy.filtering import (
    ProxyPolicyDecision,
    build_proxy_target_url,
    evaluate_proxy_decision,
    is_proxy_filtering_active,
    normalize_http_target,
    should_block_url_path,
)


class FrozenDateTime(real_datetime):
    fixed_now = real_datetime(2026, 4, 27, 10, 15)

    @classmethod
    def now(cls, tz=None):
        if tz is None:
            return cls.fixed_now

        return cls.fixed_now.replace(tzinfo=tz)


def test_build_proxy_target_url_combines_components():
    assert (
        build_proxy_target_url("http", "example.com", "/docs", "page=1")
        == "http://example.com/docs?page=1"
    )


def test_normalize_http_target_from_absolute_url():
    scheme, host, path, query, target_url = normalize_http_target(
        path="http://example.com/r/all?sort=hot",
        host_header="ignored.example",
    )

    assert (scheme, host, path, query) == ("http", "example.com", "/r/all", "sort=hot")
    assert target_url == "http://example.com/r/all?sort=hot"


def test_normalize_http_target_from_origin_form():
    scheme, host, path, query, target_url = normalize_http_target(
        path="/docs/reference?tab=api",
        host_header="example.com",
    )

    assert (scheme, host, path, query) == ("http", "example.com", "/docs/reference", "tab=api")
    assert target_url == "http://example.com/docs/reference?tab=api"


def test_should_block_url_path_matches_hardcoded_blacklist():
    assert should_block_url_path("/r/all", "")
    assert should_block_url_path("/watch", "sort=hot")
    assert not should_block_url_path("/docs/reference", "tab=api")


def test_is_proxy_filtering_active_uses_study_mode():
    user = {"focusConfig": {"studyModeEnabled": True, "schedules": []}}

    assert is_proxy_filtering_active(user) is True


def test_is_proxy_filtering_active_uses_schedule():
    user = {
        "focusConfig": {
            "studyModeEnabled": False,
            "timezone": "America/Los_Angeles",
            "schedules": [{"days": [1], "start": "09:00", "end": "11:00"}],
        }
    }

    assert (
        is_proxy_filtering_active(
            user,
            now_provider=lambda timezone_name: FrozenDateTime.now(),
        )
        is True
    )


def test_evaluate_proxy_decision_bypasses_when_focus_inactive():
    result = evaluate_proxy_decision(
        source_ip="10.0.0.9",
        target_url="http://example.com/r/all",
        path="/r/all",
        query="",
        user={"focusConfig": {"studyModeEnabled": False, "schedules": []}},
        cached_blocked=None,
        cache_decision=lambda source_ip, target_url, blocked: None,
        now_provider=lambda timezone_name: real_datetime(2026, 4, 27, 1, 0),
    )

    assert result == ProxyPolicyDecision(
        blocked=False,
        cache_hit=False,
        decision_reason="focus_inactive",
        blacklist_size=8,
    )


def test_evaluate_proxy_decision_uses_cached_result():
    result = evaluate_proxy_decision(
        source_ip="10.0.0.9",
        target_url="http://example.com/r/all",
        path="/r/all",
        query="",
        user={"focusConfig": {"studyModeEnabled": True, "schedules": []}},
        cached_blocked=True,
        cache_decision=lambda source_ip, target_url, blocked: None,
    )

    assert result == ProxyPolicyDecision(
        blocked=True,
        cache_hit=True,
        decision_reason="cache_blocked",
        blacklist_size=8,
    )


def test_evaluate_proxy_decision_blocks_blacklisted_path():
    cached_calls = []

    result = evaluate_proxy_decision(
        source_ip="10.0.0.9",
        target_url="http://example.com/r/all",
        path="/r/all",
        query="",
        user={"focusConfig": {"studyModeEnabled": True, "schedules": []}},
        cached_blocked=None,
        cache_decision=lambda source_ip, target_url, blocked: cached_calls.append(
            (source_ip, target_url, blocked)
        ),
    )

    assert result == ProxyPolicyDecision(
        blocked=True,
        cache_hit=False,
        decision_reason="path_blacklist_match",
        blacklist_size=8,
    )
    assert cached_calls == [("10.0.0.9", "http://example.com/r/all", True)]


def test_evaluate_proxy_decision_allows_clean_path():
    cached_calls = []

    result = evaluate_proxy_decision(
        source_ip="10.0.0.9",
        target_url="http://example.com/docs/reference?tab=api",
        path="/docs/reference",
        query="tab=api",
        user={"focusConfig": {"studyModeEnabled": True, "schedules": []}},
        cached_blocked=None,
        cache_decision=lambda source_ip, target_url, blocked: cached_calls.append(
            (source_ip, target_url, blocked)
        ),
    )

    assert result == ProxyPolicyDecision(
        blocked=False,
        cache_hit=False,
        decision_reason="allowed_no_match",
        blacklist_size=8,
    )
    assert cached_calls == [("10.0.0.9", "http://example.com/docs/reference?tab=api", False)]
