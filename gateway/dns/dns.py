from __future__ import annotations

import logging
import os
import socket
from dataclasses import dataclass, field
from datetime import UTC, datetime
from time import monotonic
from typing import Any, Final
from zoneinfo import ZoneInfo

from dnslib import A, AAAA, DNSHeader, DNSQuestion, DNSRecord, QTYPE, RR
from pymongo import MongoClient

LISTEN_HOST: Final[str] = os.environ.get("DNS_LISTEN_HOST", "0.0.0.0")
LISTEN_PORT: Final[int] = int(os.environ.get("DNS_PORT", "5354"))
UPSTREAM_DNS_HOST: Final[str] = os.environ.get("UPSTREAM_DNS_HOST", "1.1.1.1")
UPSTREAM_DNS_PORT: Final[int] = int(os.environ.get("UPSTREAM_DNS_PORT", "53"))
UPSTREAM_TIMEOUT_SECONDS: Final[float] = float(
    os.environ.get("UPSTREAM_TIMEOUT_SECONDS", "5.0")
)
MONGODB_URI: Final[str | None] = os.environ.get("MONGODB_URI")
MONGODB_DB_NAME: Final[str] = os.environ.get("MONGODB_DB_NAME", "timehole")
CACHE_TTL_SECONDS: Final[float] = float(os.environ.get("CACHE_TTL_SECONDS", "300"))

mongo_client = MongoClient(MONGODB_URI) if MONGODB_URI else None
users_collection = (
    mongo_client[MONGODB_DB_NAME]["users"] if mongo_client is not None else None
)
dns_logs_collection = (
    mongo_client[MONGODB_DB_NAME]["dns_logs"] if mongo_client is not None else None
)


@dataclass
class CachedDecision:
    blocked: bool
    expires_at: float


@dataclass
class SourceIpCache:
    decisions: dict[str, CachedDecision] = field(default_factory=dict)


@dataclass(frozen=True)
class PolicyDecision:
    blocked: bool
    cache_hit: bool
    decision_reason: str
    blacklist_size: int


decision_cache: dict[str, SourceIpCache] = {}


def extract_query_name(data: bytes) -> tuple[DNSRecord, str, str]:
    request = DNSRecord.parse(data)
    question: DNSQuestion | None = request.q
    if question is None:
        return request, "", "A"

    qname = str(question.qname).rstrip(".").lower()
    qtype = QTYPE[question.qtype]
    return request, qname, qtype


def get_blacklist_for_source_ip(source_ip: str) -> list[str]:
    if users_collection is None:
        return []

    try:
        user = users_collection.find_one(
            {"focusConfig.sourceIp": source_ip},
            {"focusConfig.blacklist": 1, "username": 1},
        )
    except Exception:
        logging.exception("Failed to load blacklist for source ip %s", source_ip)
        return []

    if not user:
        return []

    blacklist = user.get("focusConfig", {}).get("blacklist", [])
    return normalize_blacklist(blacklist)


def get_user_context(source_ip: str) -> dict[str, Any] | None:
    if users_collection is None:
        return None

    try:
        return users_collection.find_one(
            {"focusConfig.sourceIp": source_ip},
            {
                "username": 1,
                "focusConfig.blacklist": 1,
                "focusConfig.blockedCategories": 1,
                "focusConfig.studyModeEnabled": 1,
                "focusConfig.schedules": 1,
                "focusConfig.timezone": 1,
            },
        )
    except Exception:
        logging.exception("Failed to load user context for source ip %s", source_ip)
        return None


def log_dns_event(
    *,
    source_ip: str,
    username: str | None,
    user_matched: bool,
    query_name: str,
    qtype: str,
    blocked: bool,
    cache_hit: bool,
    decision_reason: str,
    blacklist_size: int,
    response_code: str | None,
    answer_count: int,
    answers: list[str],
    upstream_latency_ms: float | None,
    error: str | None,
) -> None:
    if dns_logs_collection is None:
        return

    try:
        dns_logs_collection.insert_one(
            {
                "sourceIp": source_ip,
                "username": username,
                "userMatched": user_matched,
                "queryName": query_name,
                "queryType": qtype,
                "blocked": blocked,
                "cacheHit": cache_hit,
                "decisionReason": decision_reason,
                "blacklistSize": blacklist_size,
                "responseCode": response_code,
                "answerCount": answer_count,
                "answers": answers,
                "upstreamLatencyMs": upstream_latency_ms,
                "error": error,
                "createdAt": datetime.now(UTC).isoformat(),
            }
        )
    except Exception:
        logging.exception(
            "Failed to write DNS log entry for %s from source ip %s",
            query_name,
            source_ip,
        )


def get_source_cache(source_ip: str) -> SourceIpCache:
    cache = decision_cache.get(source_ip)
    if cache is None:
        cache = SourceIpCache()
        decision_cache[source_ip] = cache
    return cache


def get_cached_decision(source_ip: str, query_name: str) -> bool | None:
    cache = decision_cache.get(source_ip)
    if cache is None:
        return None

    decision = cache.decisions.get(query_name)
    if decision is None:
        return None

    if decision.expires_at <= monotonic():
        del cache.decisions[query_name]
        if not cache.decisions:
            del decision_cache[source_ip]
        return None

    return decision.blocked


def cache_decision(source_ip: str, query_name: str, blocked: bool) -> None:
    cache = get_source_cache(source_ip)
    cache.decisions[query_name] = CachedDecision(
        blocked=blocked,
        expires_at=monotonic() + CACHE_TTL_SECONDS,
    )


def normalize_blacklist(blacklist: Any) -> list[str]:
    if not isinstance(blacklist, list):
        return []

    return [str(entry).lower() for entry in blacklist]


def is_blocked(query_name: str, blacklist: list[str]) -> bool:
    return any(entry in query_name for entry in blacklist)


def parse_time_to_minutes(value: str) -> int:
    try:
        hours_raw, minutes_raw = value.split(":", 1)
        return (int(hours_raw) * 60) + int(minutes_raw)
    except Exception:
        return 0


def is_filtering_active(user: dict[str, Any] | None) -> bool:
    if user is None:
        return False

    focus_config = user.get("focusConfig", {})
    if not isinstance(focus_config, dict):
        return False

    if bool(focus_config.get("studyModeEnabled")):
        return True

    timezone_name = str(focus_config.get("timezone") or "America/Los_Angeles")
    try:
        now = datetime.now(ZoneInfo(timezone_name))
    except Exception:
        now = datetime.now()

    current_day = (now.weekday() + 1) % 7
    current_minutes = (now.hour * 60) + now.minute
    schedules = focus_config.get("schedules", [])
    if not isinstance(schedules, list):
        return False

    for schedule in schedules:
        if not isinstance(schedule, dict):
            continue

        days = schedule.get("days", [])
        if not isinstance(days, list) or current_day not in days:
            continue

        start_minutes = parse_time_to_minutes(str(schedule.get("start", "00:00")))
        end_minutes = parse_time_to_minutes(str(schedule.get("end", "00:00")))
        if start_minutes <= current_minutes < end_minutes:
            return True

    return False


def get_user_blacklist(user: dict[str, Any] | None) -> list[str]:
    if user is None:
        return []

    focus_config = user.get("focusConfig", {})
    if not isinstance(focus_config, dict):
        return []

    return normalize_blacklist(focus_config.get("blacklist", []))


def evaluate_policy_decision(
    *,
    source_ip: str,
    query_name: str,
    user: dict[str, Any] | None,
    cached_blocked: bool | None,
) -> PolicyDecision:
    if not is_filtering_active(user):
        return PolicyDecision(
            blocked=False,
            cache_hit=False,
            decision_reason="focus_inactive",
            blacklist_size=len(get_user_blacklist(user)),
        )

    if cached_blocked is not None:
        return PolicyDecision(
            blocked=cached_blocked,
            cache_hit=True,
            decision_reason="cache_blocked" if cached_blocked else "cache_allowed",
            blacklist_size=len(get_user_blacklist(user)),
        )

    blacklist = (
        get_user_blacklist(user)
        if user is not None
        else get_blacklist_for_source_ip(source_ip)
    )
    blocked = is_blocked(query_name, blacklist)
    cache_decision(source_ip, query_name, blocked)

    if blocked:
        decision_reason = "blacklist_match"
    elif user is None:
        decision_reason = "no_user_config"
    else:
        decision_reason = "allowed_no_match"

    return PolicyDecision(
        blocked=blocked,
        cache_hit=False,
        decision_reason=decision_reason,
        blacklist_size=len(blacklist),
    )


def build_blackhole_response(request: DNSRecord, query_name: str, qtype: str) -> bytes:
    reply = request.reply()
    reply.header = DNSHeader(
        id=request.header.id,
        bitmap=request.header.bitmap,
        qr=1,
        aa=1,
        ra=1,
    )

    if qtype == "AAAA":
        reply.add_answer(RR(query_name, QTYPE.AAAA, rdata=AAAA("::"), ttl=60))
    else:
        reply.add_answer(RR(query_name, QTYPE.A, rdata=A("0.0.0.0"), ttl=60))

    return reply.pack()


def build_servfail_response(request: DNSRecord) -> bytes:
    reply = request.reply()
    reply.header.rcode = 2
    return reply.pack()


def summarize_response(data: bytes) -> tuple[str | None, int, list[str]]:
    try:
        response = DNSRecord.parse(data)
    except Exception:
        return None, 0, []

    answers = []
    for answer in response.rr:
        try:
            answers.append(str(answer.rdata))
        except Exception:
            continue

    response_code = str(response.header.rcode) if response.header else None
    return (response_code, len(response.rr), answers[:10])


def relay_to_upstream(data: bytes) -> bytes:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as upstream:
        upstream.settimeout(UPSTREAM_TIMEOUT_SECONDS)
        upstream.sendto(data, (UPSTREAM_DNS_HOST, UPSTREAM_DNS_PORT))
        response, _ = upstream.recvfrom(4096)
        return response


def serve() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as server:
        server.bind((LISTEN_HOST, LISTEN_PORT))
        logging.info(
            "DNS relay listening on %s:%s and forwarding to %s:%s using db=%s",
            LISTEN_HOST,
            LISTEN_PORT,
            UPSTREAM_DNS_HOST,
            UPSTREAM_DNS_PORT,
            MONGODB_DB_NAME,
        )

        while True:
            data, client_address = server.recvfrom(4096)

            try:
                request, query_name, qtype = extract_query_name(data)
                source_ip = client_address[0]

                if not query_name:
                    logging.warning("Received DNS query without a question section")
                    continue

                cached_blocked = get_cached_decision(source_ip, query_name)
                user = get_user_context(source_ip)
                username = str(user.get("username")) if user and user.get("username") else None
                policy = evaluate_policy_decision(
                    source_ip=source_ip,
                    query_name=query_name,
                    user=user,
                    cached_blocked=cached_blocked,
                )
                blocked = policy.blocked
                cache_hit = policy.cache_hit
                decision_reason = policy.decision_reason
                blacklist_size = policy.blacklist_size

                if decision_reason == "focus_inactive":
                    logging.info(
                        "Focus inactive for %s from source ip %s; bypassing blacklist",
                        query_name,
                        source_ip,
                    )
                elif cached_blocked is not None:
                    logging.info(
                        "Cache hit for %s from source ip %s: %s",
                        query_name,
                        source_ip,
                        "blocked" if blocked else "allowed",
                    )
                else:
                    logging.info(
                        "Cache miss for %s from source ip %s; evaluated against %s blacklist entries",
                        query_name,
                        source_ip,
                        blacklist_size,
                    )

                if blocked:
                    logging.info(
                        "Blocked query for %s from source ip %s",
                        query_name,
                        source_ip,
                    )
                    response = build_blackhole_response(request, query_name, qtype)
                    response_code = "NOERROR"
                    answer_count = 1
                    answers = ["::" if qtype == "AAAA" else "0.0.0.0"]
                    upstream_latency_ms = None
                    error = None
                else:
                    logging.info(
                        "Relaying query for %s from source ip %s",
                        query_name,
                        source_ip,
                    )
                    start = monotonic()
                    try:
                        response = relay_to_upstream(data)
                        upstream_latency_ms = round((monotonic() - start) * 1000, 2)
                        response_code, answer_count, answers = summarize_response(response)
                        error = None
                    except Exception as relay_error:
                        upstream_latency_ms = round((monotonic() - start) * 1000, 2)
                        response = build_servfail_response(request)
                        response_code = "SERVFAIL"
                        answer_count = 0
                        answers = []
                        error = str(relay_error)
                        decision_reason = "upstream_error"

                log_dns_event(
                    source_ip=source_ip,
                    username=username,
                    user_matched=user is not None,
                    query_name=query_name,
                    qtype=qtype,
                    blocked=blocked,
                    cache_hit=cache_hit,
                    decision_reason=decision_reason,
                    blacklist_size=blacklist_size,
                    response_code=response_code,
                    answer_count=answer_count,
                    answers=answers,
                    upstream_latency_ms=upstream_latency_ms,
                    error=error,
                )
                server.sendto(response, client_address)
            except Exception:
                logging.exception(
                    "Failed to process DNS request from %s", client_address
                )


if __name__ == "__main__":
    serve()
