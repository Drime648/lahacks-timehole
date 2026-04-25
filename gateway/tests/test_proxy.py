from __future__ import annotations

from datetime import datetime as real_datetime

from gateway.proxy.filtering import (
    ProxyPolicyDecision,
    build_proxy_target_url,
    evaluate_proxy_decision,
    is_likely_main_document_request,
    is_proxy_filtering_active,
    normalize_http_target,
)
from gateway.proxy.server import SlidingWindowRateLimiter, build_block_page, build_llm_cache_key


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


def test_is_likely_main_document_request_accepts_html_navigation():
    assert is_likely_main_document_request(
        method="GET",
        path="https://example.com/docs",
        headers={
            "Accept": "text/html,application/xhtml+xml",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Dest": "document",
        },
    )


def test_is_likely_main_document_request_rejects_static_asset_extensions():
    assert not is_likely_main_document_request(
        method="GET",
        path="https://example.com/assets/app.js",
        headers={"Accept": "*/*", "Sec-Fetch-Dest": "script"},
    )


def test_is_likely_main_document_request_rejects_fetch_api_calls():
    assert not is_likely_main_document_request(
        method="GET",
        path="https://example.com/api/feed",
        headers={
            "Accept": "application/json",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Dest": "empty",
        },
    )


def test_is_likely_main_document_request_rejects_post_requests():
    assert not is_likely_main_document_request(
        method="POST",
        path="https://example.com/form-submit",
        headers={"Accept": "text/html"},
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
        blacklist_size=0,
    )


def test_evaluate_proxy_decision_classifier_waits_for_active_focus_window():
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
            }
        },
        cached_blocked=None,
        cache_decision=lambda source_ip, target_url, blocked: cached_calls.append(
            (source_ip, target_url, blocked)
        ),
        now_provider=lambda timezone_name: real_datetime(2026, 4, 27, 1, 0),
        semantic_classifier=lambda payload: "block",
    )

    assert result == ProxyPolicyDecision(
        blocked=False,
        cache_hit=False,
        decision_reason="focus_inactive",
        blacklist_size=0,
    )
    assert cached_calls == []


def test_evaluate_proxy_decision_uses_cached_result():
    result = evaluate_proxy_decision(
        source_ip="10.0.0.9",
        target_url="http://reddit.com/r/all",
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
        blacklist_size=0,
    )


def test_evaluate_proxy_decision_allows_from_gemma_url_classification():
    cached_calls = []

    result = evaluate_proxy_decision(
        source_ip="10.0.0.9",
        target_url="https://docs.example.com/reference",
        path="/reference",
        query="",
        user={"focusConfig": {"studyModeEnabled": True, "schedules": []}},
        cached_blocked=None,
        cache_decision=lambda source_ip, target_url, blocked: cached_calls.append(
            (source_ip, target_url, blocked)
        ),
        semantic_classifier=lambda payload: "allow",
    )

    assert result == ProxyPolicyDecision(
        blocked=False,
        cache_hit=False,
        decision_reason="gemma_url_allowed",
        blacklist_size=0,
    )
    assert cached_calls == [("10.0.0.9", "https://docs.example.com/reference", False)]


def test_evaluate_proxy_decision_blocks_gemma_url_classification():
    cached_calls = []

    result = evaluate_proxy_decision(
        source_ip="10.0.0.9",
        target_url="https://games.example.com/play",
        path="/play",
        query="",
        user={
            "focusConfig": {
                "studyModeEnabled": True,
                "blockedCategories": ["video-games"],
                "focusSummary": "I am studying calculus.",
            }
        },
        cached_blocked=None,
        cache_decision=lambda source_ip, target_url, blocked: cached_calls.append(
            (source_ip, target_url, blocked)
        ),
        semantic_classifier=lambda payload: "block",
    )

    assert result == ProxyPolicyDecision(
        blocked=True,
        cache_hit=False,
        decision_reason="gemma_url_blocked",
        blacklist_size=0,
    )
    assert cached_calls == [("10.0.0.9", "https://games.example.com/play", True)]


def test_build_llm_cache_key_includes_html_metadata():
    base_payload = {
        "phase": "html",
        "target_url": "https://www.youtube.com/watch?v=abc",
        "title": "Backend tutorial",
        "description": "Learn APIs",
        "text": "Python API tutorial",
        "focus_summary": "I am studying backend systems.",
        "blocked_categories": ["streaming"],
    }

    changed_payload = {
        **base_payload,
        "title": "Funny fail compilation",
    }

    assert build_llm_cache_key(base_payload) == build_llm_cache_key({**base_payload})
    assert build_llm_cache_key(base_payload) != build_llm_cache_key(changed_payload)


def test_sliding_window_rate_limiter_caps_calls_within_window():
    limiter = SlidingWindowRateLimiter(max_calls=2, window_seconds=60)

    assert limiter.allow(now=0) is True
    assert limiter.allow(now=10) is True
    assert limiter.allow(now=20) is False
    assert limiter.allow(now=61) is True


def test_evaluate_proxy_decision_defers_ambiguous_gemma_url_to_html():
    cached_calls = []

    result = evaluate_proxy_decision(
        source_ip="10.0.0.9",
        target_url="https://www.youtube.com/watch?v=abc",
        path="/watch",
        query="v=abc",
        user={
            "focusConfig": {
                "studyModeEnabled": True,
                "blockedCategories": ["streaming"],
                "focusSummary": "I am studying backend systems.",
            }
        },
        cached_blocked=None,
        cache_decision=lambda source_ip, target_url, blocked: cached_calls.append(
            (source_ip, target_url, blocked)
        ),
        semantic_classifier=lambda payload: "needs_html",
    )

    assert result == ProxyPolicyDecision(
        blocked=False,
        cache_hit=False,
        decision_reason="gemma_needs_html",
        blacklist_size=0,
    )
    assert cached_calls == []


def test_evaluate_proxy_decision_blocks_gemma_html_classification():
    cached_calls = []

    result = evaluate_proxy_decision(
        source_ip="10.0.0.9",
        target_url="https://www.youtube.com/watch?v=abc",
        path="/watch",
        query="v=abc",
        user={
            "focusConfig": {
                "studyModeEnabled": True,
                "blockedCategories": ["streaming"],
                "focusSummary": "I am studying backend systems.",
            }
        },
        cached_blocked=None,
        cache_decision=lambda source_ip, target_url, blocked: cached_calls.append(
            (source_ip, target_url, blocked)
        ),
        response_body=b"<html><title>Funny fail compilation</title></html>",
        response_content_type="text/html; charset=utf-8",
        semantic_classifier=lambda payload: "block",
    )

    assert result == ProxyPolicyDecision(
        blocked=True,
        cache_hit=False,
        decision_reason="gemma_html_blocked",
        blacklist_size=0,
    )
    assert cached_calls == [("10.0.0.9", "https://www.youtube.com/watch?v=abc", True)]


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
            }
        },
        cached_blocked=None,
        cache_decision=lambda source_ip, target_url, blocked: cached_calls.append(
            (source_ip, target_url, blocked)
        ),
        now_provider=lambda timezone_name: FrozenDateTime.now(),
        semantic_classifier=lambda payload: "block",
    )

    assert result == ProxyPolicyDecision(
        blocked=True,
        cache_hit=False,
        decision_reason="gemma_url_blocked",
        blacklist_size=0,
    )
    assert cached_calls == [("10.0.0.9", "https://www.jetpunk.com/quizzes", True)]


def test_evaluate_proxy_decision_allows_non_html_response_after_needs_html():
    cached_calls = []

    result = evaluate_proxy_decision(
        source_ip="10.0.0.9",
        target_url="http://example.com/data.json",
        path="/data.json",
        query="",
        user={"focusConfig": {"studyModeEnabled": True, "schedules": []}},
        cached_blocked=None,
        cache_decision=lambda source_ip, target_url, blocked: cached_calls.append(
            (source_ip, target_url, blocked)
        ),
        response_body=b'{"ok": true}',
        response_content_type="application/json",
        semantic_classifier=lambda payload: "block",
    )

    assert result == ProxyPolicyDecision(
        blocked=False,
        cache_hit=False,
        decision_reason="allowed_non_html_response",
        blacklist_size=0,
    )
    assert cached_calls == [("10.0.0.9", "http://example.com/data.json", False)]


def test_build_block_page_contains_get_back_on_task_message():
    body = build_block_page("https://reddit.com/r/all", "path_blacklist_match").decode("utf-8")

    assert "Get back on task" in body
    assert "https://reddit.com/r/all" in body
    assert "path_blacklist_match" in body
