from __future__ import annotations

from datetime import datetime as real_datetime

from gateway.proxy.filtering import (
    ProxyPolicyDecision,
    build_proxy_target_url,
    evaluate_proxy_decision,
    get_user_manual_blacklist,
    is_proxy_filtering_active,
    normalize_http_target,
    should_block_url,
)
from gateway.proxy.server import build_block_page


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


def test_should_block_url_matches_mongodb_blacklist_entries():
    blacklist = ["reddit", "/shorts", "sort=hot"]

    assert should_block_url("https://www.reddit.com/r/all", blacklist)
    assert should_block_url("https://youtube.com/shorts/123", blacklist)
    assert should_block_url("https://example.com/watch?sort=hot", blacklist)
    assert not should_block_url("https://example.com/docs/reference?tab=api", blacklist)


def test_get_user_manual_blacklist_returns_normalized_manual_entries():
    assert get_user_manual_blacklist(None) == []
    assert get_user_manual_blacklist({"focusConfig": "invalid"}) == []
    assert (
        get_user_manual_blacklist({"focusConfig": {"manualBlacklist": ["JetPunk", "Reddit"]}})
        == ["jetpunk", "reddit"]
    )


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
        target_url="http://reddit.com/r/all",
        path="/r/all",
        query="",
        user={
            "focusConfig": {
                "studyModeEnabled": False,
                "schedules": [],
                "blacklist": ["reddit"],
            }
        },
        cached_blocked=None,
        cache_decision=lambda source_ip, target_url, blocked: None,
        now_provider=lambda timezone_name: real_datetime(2026, 4, 27, 1, 0),
    )

    assert result == ProxyPolicyDecision(
        blocked=False,
        cache_hit=False,
        decision_reason="focus_inactive",
        blacklist_size=1,
    )


def test_evaluate_proxy_decision_blacklist_waits_for_active_focus_window():
    cached_calls = []

    result = evaluate_proxy_decision(
        source_ip="10.0.0.9",
        target_url="https://www.jetpunk.com/quizzes",
        path="/quizzes",
        query="",
        user={
            "focusConfig": {
                "studyModeEnabled": False,
                "schedules": [],
                "blacklist": ["jetpunk"],
                "manualBlacklist": ["jetpunk"],
            }
        },
        cached_blocked=None,
        cache_decision=lambda source_ip, target_url, blocked: cached_calls.append(
            (source_ip, target_url, blocked)
        ),
        now_provider=lambda timezone_name: real_datetime(2026, 4, 27, 1, 0),
    )

    assert result == ProxyPolicyDecision(
        blocked=False,
        cache_hit=False,
        decision_reason="focus_inactive",
        blacklist_size=1,
    )
    assert cached_calls == []


def test_evaluate_proxy_decision_uses_cached_result():
    result = evaluate_proxy_decision(
        source_ip="10.0.0.9",
        target_url="http://reddit.com/r/all",
        path="/r/all",
        query="",
        user={"focusConfig": {"studyModeEnabled": True, "schedules": [], "blacklist": ["reddit"]}},
        cached_blocked=True,
        cache_decision=lambda source_ip, target_url, blocked: None,
    )

    assert result == ProxyPolicyDecision(
        blocked=True,
        cache_hit=True,
        decision_reason="cache_blocked",
        blacklist_size=1,
    )


def test_evaluate_proxy_decision_blocks_mongodb_blacklisted_url():
    cached_calls = []

    result = evaluate_proxy_decision(
        source_ip="10.0.0.9",
        target_url="http://reddit.com/r/all",
        path="/r/all",
        query="",
        user={"focusConfig": {"studyModeEnabled": True, "schedules": [], "blacklist": ["reddit"]}},
        cached_blocked=None,
        cache_decision=lambda source_ip, target_url, blocked: cached_calls.append(
            (source_ip, target_url, blocked)
        ),
    )

    assert result == ProxyPolicyDecision(
        blocked=True,
        cache_hit=False,
        decision_reason="blacklist_match",
        blacklist_size=1,
    )
    assert cached_calls == [("10.0.0.9", "http://reddit.com/r/all", True)]


def test_evaluate_proxy_decision_blocks_during_scheduled_focus_window():
    cached_calls = []

    result = evaluate_proxy_decision(
        source_ip="10.0.0.9",
        target_url="https://www.jetpunk.com/quizzes",
        path="/quizzes",
        query="",
        user={
            "focusConfig": {
                "studyModeEnabled": False,
                "timezone": "America/Los_Angeles",
                "schedules": [{"days": [1], "start": "09:00", "end": "11:00"}],
                "blacklist": ["jetpunk"],
            }
        },
        cached_blocked=None,
        cache_decision=lambda source_ip, target_url, blocked: cached_calls.append(
            (source_ip, target_url, blocked)
        ),
        now_provider=lambda timezone_name: FrozenDateTime.now(),
    )

    assert result == ProxyPolicyDecision(
        blocked=True,
        cache_hit=False,
        decision_reason="blacklist_match",
        blacklist_size=1,
    )
    assert cached_calls == [("10.0.0.9", "https://www.jetpunk.com/quizzes", True)]


def test_evaluate_proxy_decision_allows_clean_path():
    cached_calls = []

    result = evaluate_proxy_decision(
        source_ip="10.0.0.9",
        target_url="http://example.com/docs/reference?tab=api",
        path="/docs/reference",
        query="tab=api",
        user={"focusConfig": {"studyModeEnabled": True, "schedules": [], "blacklist": ["reddit"]}},
        cached_blocked=None,
        cache_decision=lambda source_ip, target_url, blocked: cached_calls.append(
            (source_ip, target_url, blocked)
        ),
    )

    assert result == ProxyPolicyDecision(
        blocked=False,
        cache_hit=False,
        decision_reason="allowed_no_response_to_analyze",
        blacklist_size=1,
    )
    assert cached_calls == [("10.0.0.9", "http://example.com/docs/reference?tab=api", False)]


def test_build_block_page_contains_get_back_on_task_message():
    body = build_block_page("https://reddit.com/r/all", "path_blacklist_match").decode("utf-8")

    assert "Get back on task" in body
    assert "https://reddit.com/r/all" in body
    assert "path_blacklist_match" in body
