from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo

GOOGLE_STATIC_HOSTS = {
    "gstatic.com",
    "googleusercontent.com",
    "googleapis.com",
    "googleadservices.com",
    "googletagmanager.com",
    "googletagservices.com",
    "google-analytics.com",
    "doubleclick.net",
}

GOOGLE_STATIC_PATH_PREFIXES = (
    "/images/",
    "/logos/",
    "/xjs/",
    "/complete/",
    "/generate_204",
    "/gen_204",
    "/favicon.ico",
)

STATIC_ASSET_EXTENSIONS = (
    ".avif",
    ".bmp",
    ".css",
    ".gif",
    ".ico",
    ".jpeg",
    ".jpg",
    ".js",
    ".json",
    ".map",
    ".mjs",
    ".otf",
    ".png",
    ".svg",
    ".ttf",
    ".wasm",
    ".webp",
    ".woff",
    ".woff2",
)


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
        return (
            scheme,
            host,
            target_path,
            query,
            build_proxy_target_url(
                scheme,
                host,
                target_path,
                query,
            ),
        )

    parsed = urlsplit(path)
    scheme = default_scheme
    host = host_header
    target_path = parsed.path or "/"
    query = parsed.query
    return (
        scheme,
        host,
        target_path,
        query,
        build_proxy_target_url(
            scheme,
            host,
            target_path,
            query,
        ),
    )


def is_likely_main_document_request(
    *,
    method: str,
    path: str,
    headers: dict[str, str] | None = None,
) -> bool:
    normalized_method = method.upper()
    if normalized_method not in {"GET", "HEAD"}:
        return False

    parsed = urlsplit(path)
    normalized_path = (parsed.path or "/").lower()
    if normalized_path.endswith(STATIC_ASSET_EXTENSIONS):
        return False

    lowered_headers = {key.lower(): value for key, value in (headers or {}).items()}
    sec_fetch_dest = lowered_headers.get("sec-fetch-dest", "").strip().lower()
    if sec_fetch_dest and sec_fetch_dest not in {"document", "iframe", "frame"}:
        return False

    sec_fetch_mode = lowered_headers.get("sec-fetch-mode", "").strip().lower()
    if sec_fetch_mode == "navigate":
        return True

    accept = lowered_headers.get("accept", "").lower()
    if "text/html" in accept or "application/xhtml+xml" in accept:
        return True

    if accept and all(token not in accept for token in ("text/html", "application/xhtml+xml")):
        return False

    last_segment = normalized_path.rsplit("/", 1)[-1]
    if "." in last_segment:
        extension = f".{last_segment.rsplit('.', 1)[-1]}"
        if extension in STATIC_ASSET_EXTENSIONS:
            return False

    return True


def extract_page_metadata(
    *,
    content_type: str | None,
    response_body: bytes,
    max_chars: int = 4000,
) -> dict[str, str]:
    text = ""

    if content_type and "text/html" not in content_type.lower():
        return {"title": "", "description": "", "text": ""}

    try:
        text = response_body.decode("utf-8", errors="ignore")
    except Exception:
        return {"title": "", "description": "", "text": ""}

    title = ""
    description = ""

    import re

    title_match = re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S)
    if title_match:
        title = re.sub(r"\s+", " ", title_match.group(1)).strip()

    desc_match = re.search(
        r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)["\']',
        text,
        re.I | re.S,
    )
    if desc_match:
        description = re.sub(r"\s+", " ", desc_match.group(1)).strip()

    visible_text = re.sub(r"<script.*?</script>", " ", text, flags=re.I | re.S)
    visible_text = re.sub(r"<style.*?</style>", " ", visible_text, flags=re.I | re.S)
    visible_text = re.sub(r"<[^>]+>", " ", visible_text)
    visible_text = re.sub(r"\s+", " ", visible_text).strip()

    return {
        "title": title[:500],
        "description": description[:1000],
        "text": visible_text[:max_chars],
    }


def evaluate_semantic_response(
    *,
    target_url: str,
    metadata: dict[str, str],
    user: dict[str, Any],
    semantic_classifier: Callable[[dict[str, Any]], bool] | None = None,
) -> tuple[bool, str]:
    focus_config = user.get("focusConfig", {})

    payload = {
        "phase": "html",
        "target_url": target_url,
        "title": metadata.get("title", ""),
        "description": metadata.get("description", ""),
        "text": metadata.get("text", ""),
        "focus_summary": focus_config.get("focusSummary", ""),
        "blocked_categories": focus_config.get("blockedCategories", []),
    }

    if semantic_classifier is None:
        return False, "semantic_classifier_missing"

    decision = semantic_classifier(payload)
    blocked = decision == "block" if isinstance(decision, str) else bool(decision)

    return (
        blocked,
        "semantic_blocked" if blocked else "semantic_allowed",
    )


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
    response_body: bytes | None = None,
    response_content_type: str | None = None,
    semantic_classifier: Callable[[dict[str, Any]], bool] | None = None,
) -> ProxyPolicyDecision:
    if not is_proxy_filtering_active(user, now_provider=now_provider):
        return ProxyPolicyDecision(
            blocked=False,
            cache_hit=False,
            decision_reason="focus_inactive",
            blacklist_size=0,
        )

    if cached_blocked is not None:
        return ProxyPolicyDecision(
            blocked=cached_blocked,
            cache_hit=True,
            decision_reason="cache_blocked" if cached_blocked else "cache_allowed",
            blacklist_size=0,
        )

    if semantic_classifier is None or user is None:
        cache_decision(source_ip, target_url, False)
        return ProxyPolicyDecision(
            blocked=False,
            cache_hit=False,
            decision_reason="semantic_classifier_missing",
            blacklist_size=0,
        )

    if response_body is None:
        focus_config = user.get("focusConfig", {})
        decision = semantic_classifier(
            {
                "phase": "url",
                "target_url": target_url,
                "focus_summary": focus_config.get("focusSummary", ""),
                "blocked_categories": focus_config.get("blockedCategories", []),
            }
        )
        if decision == "block":
            cache_decision(source_ip, target_url, True)
            return ProxyPolicyDecision(
                blocked=True,
                cache_hit=False,
                decision_reason="gemma_url_blocked",
                blacklist_size=0,
            )
        if decision == "needs_html":
            return ProxyPolicyDecision(
                blocked=False,
                cache_hit=False,
                decision_reason="gemma_needs_html",
                blacklist_size=0,
            )
        cache_decision(source_ip, target_url, False)
        return ProxyPolicyDecision(
            blocked=False,
            cache_hit=False,
            decision_reason="gemma_url_allowed",
            blacklist_size=0,
        )

    if response_body is not None:
        metadata = extract_page_metadata(
            content_type=response_content_type,
            response_body=response_body,
        )

        if not any(metadata.values()):
            cache_decision(source_ip, target_url, False)
            return ProxyPolicyDecision(
                blocked=False,
                cache_hit=False,
                decision_reason="allowed_non_html_response",
                blacklist_size=0,
            )

        semantic_blocked, reason = evaluate_semantic_response(
            target_url=target_url,
            metadata=metadata,
            user=user,
            semantic_classifier=semantic_classifier,
        )

        cache_decision(source_ip, target_url, semantic_blocked)

        return ProxyPolicyDecision(
            blocked=semantic_blocked,
            cache_hit=False,
            decision_reason="gemma_html_blocked" if semantic_blocked else "gemma_html_allowed",
            blacklist_size=0,
        )

    cache_decision(source_ip, target_url, False)

    return ProxyPolicyDecision(
        blocked=False,
        cache_hit=False,
        decision_reason="allowed_no_response_to_analyze",
        blacklist_size=0,
    )
