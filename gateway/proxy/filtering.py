from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable
from urllib.parse import SplitResult, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

URL_PATH_BLACKLIST = [
    "/r/all",
    "/shorts",
    "/reels",
    "/explore",
    "/foryou",
    "/feed",
    "sort=hot",
    "sort=top",
]


@dataclass(frozen=True)
class ProxyPolicyDecision:
    blocked: bool
    cache_hit: bool
    decision_reason: str
    blacklist_size: int


def parse_time_to_minutes(value: str) -> int:
    try:
        hours_raw, minutes_raw = value.split(":", 1)
        return (int(hours_raw) * 60) + int(minutes_raw)
    except Exception:
        return 0


def is_proxy_filtering_active(
    user: dict[str, Any] | None,
    now_provider: Callable[[str], datetime] | None = None,
) -> bool:
    if user is None:
        return False

    focus_config = user.get("focusConfig", {})
    if not isinstance(focus_config, dict):
        return False

    if bool(focus_config.get("studyModeEnabled")):
        return True

    timezone_name = str(focus_config.get("timezone") or "America/Los_Angeles")
    if now_provider is None:
        try:
            now = datetime.now(ZoneInfo(timezone_name))
        except Exception:
            now = datetime.now()
    else:
        now = now_provider(timezone_name)

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


def build_proxy_target_url(scheme: str, host: str, path: str, query: str) -> str:
    normalized_path = path or "/"
    return urlunsplit((scheme, host, normalized_path, query, ""))


def normalize_http_target(
    *,
    path: str,
    host_header: str,
    default_scheme: str = "http",
) -> tuple[str, str, str, str, str]:
    if path.startswith("http://") or path.startswith("https://"):
        parsed = urlsplit(path)
        scheme = parsed.scheme or default_scheme
        host = parsed.netloc or host_header
        target_path = parsed.path or "/"
        query = parsed.query
        return scheme, host, target_path, query, build_proxy_target_url(
            scheme,
            host,
            target_path,
            query,
        )

    parsed = urlsplit(path)
    scheme = default_scheme
    host = host_header
    target_path = parsed.path or "/"
    query = parsed.query
    return scheme, host, target_path, query, build_proxy_target_url(
        scheme,
        host,
        target_path,
        query,
    )


def should_block_url_path(path: str, query: str) -> bool:
    path_and_query = path.lower()
    if query:
        path_and_query = f"{path_and_query}?{query.lower()}"
    return any(entry in path_and_query for entry in URL_PATH_BLACKLIST)


def evaluate_proxy_decision(
    *,
    source_ip: str,
    target_url: str,
    path: str,
    query: str,
    user: dict[str, Any] | None,
    cached_blocked: bool | None,
    cache_decision: Callable[[str, str, bool], None],
    now_provider: Callable[[str], datetime] | None = None,
) -> ProxyPolicyDecision:
    if not is_proxy_filtering_active(user, now_provider=now_provider):
        return ProxyPolicyDecision(
            blocked=False,
            cache_hit=False,
            decision_reason="focus_inactive",
            blacklist_size=len(URL_PATH_BLACKLIST),
        )

    if cached_blocked is not None:
        return ProxyPolicyDecision(
            blocked=cached_blocked,
            cache_hit=True,
            decision_reason="cache_blocked" if cached_blocked else "cache_allowed",
            blacklist_size=len(URL_PATH_BLACKLIST),
        )

    blocked = should_block_url_path(path, query)
    cache_decision(source_ip, target_url, blocked)
    return ProxyPolicyDecision(
        blocked=blocked,
        cache_hit=False,
        decision_reason="path_blacklist_match" if blocked else "allowed_no_match",
        blacklist_size=len(URL_PATH_BLACKLIST),
    )
