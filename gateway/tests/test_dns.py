from __future__ import annotations

from datetime import datetime as real_datetime

from dnslib import A, AAAA, DNSRecord, QTYPE, RR

from gateway.cache import DecisionCache
from gateway.dns.filtering import (
    PolicyDecision,
    evaluate_policy_decision,
    get_user_blacklist,
    is_blocked,
    is_filtering_active,
    normalize_blacklist,
    parse_time_to_minutes,
)
from gateway.store import MongoGatewayStore
from gateway.dns import dns


class FrozenDateTime(real_datetime):
    fixed_now = real_datetime(2026, 4, 27, 10, 15)

    @classmethod
    def now(cls, tz=None):
        if tz is None:
            return cls.fixed_now

        return cls.fixed_now.replace(tzinfo=tz)


class FakeUsersCollection:
    def __init__(self, result=None, error: Exception | None = None):
        self.result = result
        self.error = error
        self.calls = []

    def find_one(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self.error is not None:
            raise self.error
        return self.result


class FakeLogsCollection:
    def __init__(self, error: Exception | None = None):
        self.error = error
        self.inserted = []

    def insert_one(self, document):
        if self.error is not None:
            raise self.error
        self.inserted.append(document)


def build_request(name: str, qtype: str = "A") -> DNSRecord:
    return DNSRecord.question(name, qtype)


def test_extract_query_name_normalizes_domain_and_type():
    request = build_request("Example.COM", "AAAA")

    parsed_request, query_name, qtype = dns.extract_query_name(request.pack())

    assert query_name == "example.com"
    assert qtype == "AAAA"
    assert parsed_request.q is not None


def test_normalize_blacklist_handles_invalid_and_lowercases():
    assert normalize_blacklist(None) == []
    assert normalize_blacklist(["Reddit", "TikTok", 123]) == ["reddit", "tiktok", "123"]


def test_parse_time_to_minutes_parses_and_falls_back():
    assert parse_time_to_minutes("09:30") == 570
    assert parse_time_to_minutes("bad-input") == 0


def test_is_blocked_uses_substring_matching():
    assert is_blocked("api.reddit.com", ["reddit"])
    assert not is_blocked("docs.python.org", ["reddit"])


def test_get_source_cache_returns_same_cache_for_same_ip():
    cache = DecisionCache(ttl_seconds=300)

    first = cache.get_source_cache("10.0.0.1")
    second = cache.get_source_cache("10.0.0.1")

    assert first is second


def test_cache_decision_and_get_cached_decision(monkeypatch):
    cache = DecisionCache(ttl_seconds=300)
    fake_clock = {"value": 100.0}
    monkeypatch.setattr("gateway.cache.monotonic", lambda: fake_clock["value"])

    cache.cache_decision("10.0.0.1", "reddit.com", True)

    assert cache.get_cached_decision("10.0.0.1", "reddit.com") is True


def test_get_cached_decision_expires_and_cleans_up(monkeypatch):
    cache = DecisionCache(ttl_seconds=300)
    fake_clock = {"value": 100.0}
    monkeypatch.setattr("gateway.cache.monotonic", lambda: fake_clock["value"])

    cache.cache_decision("10.0.0.1", "reddit.com", False)
    fake_clock["value"] = 1000.0

    assert cache.get_cached_decision("10.0.0.1", "reddit.com") is None
    assert "10.0.0.1" not in cache.entries


def test_get_blacklist_for_source_ip_returns_normalized_blacklist(monkeypatch):
    store = MongoGatewayStore(
        users_collection=FakeUsersCollection({"focusConfig": {"blacklist": ["Reddit", "TikTok"]}})
    )

    result = store.get_blacklist_for_source_ip("10.0.0.2")

    assert result == ["reddit", "tiktok"]


def test_get_blacklist_for_source_ip_handles_collection_errors():
    store = MongoGatewayStore(users_collection=FakeUsersCollection(error=RuntimeError("db down")))

    assert store.get_blacklist_for_source_ip("10.0.0.2") == []


def test_get_user_context_handles_collection_errors():
    store = MongoGatewayStore(users_collection=FakeUsersCollection(error=RuntimeError("db down")))

    assert store.get_user_context("10.0.0.2") is None


def test_get_user_context_returns_matching_projection_result():
    expected = {"username": "alice", "focusConfig": {"blacklist": ["reddit"]}}
    fake_users = FakeUsersCollection(expected)
    store = MongoGatewayStore(users_collection=fake_users)

    result = store.get_user_context("10.0.0.2")

    assert result == expected
    assert fake_users.calls


def test_get_user_blacklist_returns_empty_for_invalid_focus_config():
    assert get_user_blacklist(None) == []
    assert get_user_blacklist({"focusConfig": "invalid"}) == []


def test_is_filtering_active_returns_true_when_study_mode_enabled():
    user = {"focusConfig": {"studyModeEnabled": True, "schedules": []}}

    assert is_filtering_active(user) is True


def test_is_filtering_active_uses_schedule_and_timezone(monkeypatch):
    user = {
        "focusConfig": {
            "studyModeEnabled": False,
            "timezone": "America/Los_Angeles",
            "schedules": [{"days": [1], "start": "09:00", "end": "11:00"}],
        }
    }

    assert (
        is_filtering_active(
            user,
            now_provider=lambda timezone_name: FrozenDateTime.now(),
        )
        is True
    )


def test_is_filtering_active_returns_false_outside_schedule():
    user = {
        "focusConfig": {
            "studyModeEnabled": False,
            "timezone": "America/Los_Angeles",
            "schedules": [{"days": [1], "start": "11:00", "end": "12:00"}],
        }
    }

    assert (
        is_filtering_active(
            user,
            now_provider=lambda timezone_name: FrozenDateTime.now(),
        )
        is False
    )


def test_is_filtering_active_falls_back_when_timezone_invalid():
    user = {
        "focusConfig": {
            "studyModeEnabled": False,
            "timezone": "Not/AZone",
            "schedules": [{"days": [1], "start": "09:00", "end": "11:00"}],
        }
    }

    assert (
        is_filtering_active(
            user,
            now_provider=lambda timezone_name: FrozenDateTime.now(),
        )
        is True
    )


def test_evaluate_policy_decision_bypasses_when_focus_inactive(monkeypatch):
    result = evaluate_policy_decision(
        source_ip="10.0.0.3",
        query_name="reddit.com",
        user={"focusConfig": {"blacklist": ["reddit"]}},
        cached_blocked=None,
        source_blacklist_loader=lambda source_ip: [],
        cache_decision=lambda source_ip, query_name, blocked: None,
        now_provider=lambda timezone_name: real_datetime(2026, 4, 27, 1, 0),
    )

    assert result == PolicyDecision(
        blocked=False,
        cache_hit=False,
        decision_reason="focus_inactive",
        blacklist_size=1,
    )


def test_evaluate_policy_decision_uses_cached_block():
    result = evaluate_policy_decision(
        source_ip="10.0.0.3",
        query_name="reddit.com",
        user={"focusConfig": {"studyModeEnabled": True, "blacklist": ["reddit"]}},
        cached_blocked=True,
        source_blacklist_loader=lambda source_ip: [],
        cache_decision=lambda source_ip, query_name, blocked: None,
    )

    assert result == PolicyDecision(
        blocked=True,
        cache_hit=True,
        decision_reason="cache_blocked",
        blacklist_size=1,
    )


def test_evaluate_policy_decision_uses_cached_allow():
    result = evaluate_policy_decision(
        source_ip="10.0.0.3",
        query_name="docs.python.org",
        user={"focusConfig": {"studyModeEnabled": True, "blacklist": ["reddit"]}},
        cached_blocked=False,
        source_blacklist_loader=lambda source_ip: [],
        cache_decision=lambda source_ip, query_name, blocked: None,
    )

    assert result == PolicyDecision(
        blocked=False,
        cache_hit=True,
        decision_reason="cache_allowed",
        blacklist_size=1,
    )


def test_evaluate_policy_decision_blocks_and_caches(monkeypatch):
    cached_calls = []

    result = evaluate_policy_decision(
        source_ip="10.0.0.4",
        query_name="api.reddit.com",
        user={"focusConfig": {"studyModeEnabled": True, "blacklist": ["reddit"]}},
        cached_blocked=None,
        source_blacklist_loader=lambda source_ip: [],
        cache_decision=lambda ip, query, blocked: cached_calls.append((ip, query, blocked)),
    )

    assert result == PolicyDecision(
        blocked=True,
        cache_hit=False,
        decision_reason="blacklist_match",
        blacklist_size=1,
    )
    assert cached_calls == [("10.0.0.4", "api.reddit.com", True)]


def test_evaluate_policy_decision_allows_and_caches(monkeypatch):
    cached_calls = []

    result = evaluate_policy_decision(
        source_ip="10.0.0.5",
        query_name="docs.python.org",
        user={"focusConfig": {"studyModeEnabled": True, "blacklist": ["reddit"]}},
        cached_blocked=None,
        source_blacklist_loader=lambda source_ip: [],
        cache_decision=lambda ip, query, blocked: cached_calls.append((ip, query, blocked)),
    )

    assert result == PolicyDecision(
        blocked=False,
        cache_hit=False,
        decision_reason="allowed_no_match",
        blacklist_size=1,
    )
    assert cached_calls == [("10.0.0.5", "docs.python.org", False)]


def test_evaluate_policy_decision_without_user_uses_source_ip_blacklist(monkeypatch):
    cached_calls = []

    result = evaluate_policy_decision(
        source_ip="10.0.0.6",
        query_name="game.roblox.com",
        user=None,
        cached_blocked=None,
        source_blacklist_loader=lambda source_ip: ["roblox"],
        cache_decision=lambda ip, query, blocked: cached_calls.append((ip, query, blocked)),
        now_provider=lambda timezone_name: FrozenDateTime.now(),
    )

    assert result == PolicyDecision(
        blocked=True,
        cache_hit=False,
        decision_reason="blacklist_match",
        blacklist_size=1,
    )
    assert cached_calls == [("10.0.0.6", "game.roblox.com", True)]


def test_build_blackhole_response_for_a_request():
    request = build_request("blocked.example", "A")

    response = DNSRecord.parse(dns.build_blackhole_response(request, "blocked.example", "A"))

    assert response.header.rcode == 0
    assert len(response.rr) == 1
    assert str(response.rr[0].rdata) == "0.0.0.0"


def test_build_blackhole_response_for_aaaa_request():
    request = build_request("blocked.example", "AAAA")

    response = DNSRecord.parse(dns.build_blackhole_response(request, "blocked.example", "AAAA"))

    assert response.header.rcode == 0
    assert len(response.rr) == 1
    assert str(response.rr[0].rdata) == "::"


def test_build_servfail_response_sets_servfail_code():
    request = build_request("example.com", "A")

    response = DNSRecord.parse(dns.build_servfail_response(request))

    assert response.header.rcode == 2


def test_summarize_response_extracts_answers():
    response = build_request("example.com", "A").reply()
    response.add_answer(RR("example.com", QTYPE.A, rdata=A("1.2.3.4"), ttl=60))
    response.add_answer(RR("example.com", QTYPE.AAAA, rdata=AAAA("::1"), ttl=60))

    response_code, answer_count, answers = dns.summarize_response(response.pack())

    assert response_code == "0"
    assert answer_count == 2
    assert answers == ["1.2.3.4", "::1"]


def test_summarize_response_handles_invalid_payload():
    assert dns.summarize_response(b"not-dns") == (None, 0, [])


def test_log_dns_event_inserts_document(monkeypatch):
    fake_logs = FakeLogsCollection()
    store = MongoGatewayStore(dns_logs_collection=fake_logs)
    monkeypatch.setattr("gateway.store.datetime", FrozenDateTime)

    store.log_dns_event(
        source_ip="10.0.0.7",
        username="alice",
        user_matched=True,
        query_name="example.com",
        qtype="A",
        blocked=False,
        cache_hit=True,
        decision_reason="cache_allowed",
        blacklist_size=2,
        response_code="0",
        answer_count=1,
        answers=["1.2.3.4"],
        upstream_latency_ms=4.2,
        error=None,
    )

    assert len(fake_logs.inserted) == 1
    inserted = fake_logs.inserted[0]
    assert inserted["sourceIp"] == "10.0.0.7"
    assert inserted["username"] == "alice"
    assert inserted["decisionReason"] == "cache_allowed"
    assert inserted["answers"] == ["1.2.3.4"]


def test_log_dns_event_swallows_write_errors(monkeypatch):
    store = MongoGatewayStore(dns_logs_collection=FakeLogsCollection(error=RuntimeError("write failed")))

    store.log_dns_event(
        source_ip="10.0.0.7",
        username="alice",
        user_matched=True,
        query_name="example.com",
        qtype="A",
        blocked=False,
        cache_hit=True,
        decision_reason="cache_allowed",
        blacklist_size=2,
        response_code="0",
        answer_count=1,
        answers=["1.2.3.4"],
        upstream_latency_ms=4.2,
        error=None,
    )
