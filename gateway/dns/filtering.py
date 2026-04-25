from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class PolicyDecision:
    blocked: bool
    cache_hit: bool
    decision_reason: str
    blacklist_size: int


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


def is_filtering_active(
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


def get_user_blacklist(user: dict[str, Any] | None) -> list[str]:
    if user is None:
        return []

    focus_config = user.get("focusConfig", {})
    if not isinstance(focus_config, dict):
        return []

    return normalize_blacklist(focus_config.get("blacklist", []))


def get_user_manual_blacklist(user: dict[str, Any] | None) -> list[str]:
    if user is None:
        return []

    focus_config = user.get("focusConfig", {})
    if not isinstance(focus_config, dict):
        return []

    return normalize_blacklist(focus_config.get("manualBlacklist", []))


def evaluate_policy_decision(
    *,
    source_ip: str,
    query_name: str,
    user: dict[str, Any] | None,
    cached_blocked: bool | None,
    source_blacklist_loader: Callable[[str], list[str]],
    cache_decision: Callable[[str, str, bool], None],
    now_provider: Callable[[str], datetime] | None = None,
) -> PolicyDecision:
    manual_blacklist = get_user_manual_blacklist(user)
    if manual_blacklist and is_blocked(query_name, manual_blacklist):
        cache_decision(source_ip, query_name, True)
        return PolicyDecision(
            blocked=True,
            cache_hit=False,
            decision_reason="manual_blacklist_match",
            blacklist_size=len(manual_blacklist),
        )

    if user is not None and not is_filtering_active(user, now_provider=now_provider):
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
        else source_blacklist_loader(source_ip)
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
