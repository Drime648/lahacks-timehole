from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

MONGODB_URI = os.environ.get("MONGODB_URI")
MONGODB_DB_NAME = os.environ.get("MONGODB_DB_NAME", "timehole")


def normalize_blacklist(blacklist: Any) -> list[str]:
    if not isinstance(blacklist, list):
        return []

    return [str(entry).lower() for entry in blacklist]


def candidate_source_ips(source_ip: str) -> list[str]:
    normalized = source_ip.strip()
    loopback_aliases = ["127.0.0.1", "::1", "0:0:0:0:0:0:0:1", "localhost"]
    if normalized in loopback_aliases:
        return loopback_aliases
    return [normalized]


class MongoGatewayStore:
    def __init__(
        self,
        users_collection=None,
        dns_logs_collection=None,
        proxy_logs_collection=None,
    ):
        self.users_collection = users_collection
        self.dns_logs_collection = dns_logs_collection
        self.proxy_logs_collection = proxy_logs_collection

    @classmethod
    def from_env(cls) -> "MongoGatewayStore":
        if not MONGODB_URI:
            return cls()

        mongo_client = MongoClient(MONGODB_URI)
        db = mongo_client[MONGODB_DB_NAME]
        return cls(
            users_collection=db["users"],
            dns_logs_collection=db["dns_logs"],
            proxy_logs_collection=db["proxy_logs"],
        )

    def get_blacklist_for_source_ip(self, source_ip: str) -> list[str]:
        if self.users_collection is None:
            return []

        try:
            user = self.users_collection.find_one(
                {"focusConfig.sourceIp": {"$in": candidate_source_ips(source_ip)}},
                {"focusConfig.blacklist": 1, "username": 1},
            )
        except Exception:
            logging.exception("Failed to load blacklist for source ip %s", source_ip)
            return []

        if not user:
            return []

        blacklist = user.get("focusConfig", {}).get("blacklist", [])
        return normalize_blacklist(blacklist)

    def get_user_context(self, source_ip: str) -> dict[str, Any] | None:
        if self.users_collection is None:
            return None

        try:
            return self.users_collection.find_one(
                {"focusConfig.sourceIp": {"$in": candidate_source_ips(source_ip)}},
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
        self,
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
        if self.dns_logs_collection is None:
            return

        try:
            self.dns_logs_collection.insert_one(
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

    def log_proxy_event(
        self,
        *,
        source_ip: str,
        username: str | None,
        user_matched: bool,
        method: str,
        scheme: str,
        host: str,
        path: str,
        query: str,
        target_url: str,
        blocked: bool,
        cache_hit: bool,
        decision_reason: str,
        status_code: int | None,
        upstream_latency_ms: float | None,
        https_tunnel: bool,
        mitm_enabled: bool,
        error: str | None,
    ) -> None:
        if self.proxy_logs_collection is None:
            return

        try:
            self.proxy_logs_collection.insert_one(
                {
                    "sourceIp": source_ip,
                    "username": username,
                    "userMatched": user_matched,
                    "method": method,
                    "scheme": scheme,
                    "host": host,
                    "path": path,
                    "query": query,
                    "targetUrl": target_url,
                    "blocked": blocked,
                    "cacheHit": cache_hit,
                    "decisionReason": decision_reason,
                    "statusCode": status_code,
                    "upstreamLatencyMs": upstream_latency_ms,
                    "httpsTunnel": https_tunnel,
                    "mitmEnabled": mitm_enabled,
                    "error": error,
                    "createdAt": datetime.now(UTC).isoformat(),
                }
            )
        except Exception:
            logging.exception(
                "Failed to write proxy log entry for %s from source ip %s",
                target_url,
                source_ip,
            )
