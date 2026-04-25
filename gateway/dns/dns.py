from __future__ import annotations

import logging
import os
import socket
from dataclasses import dataclass, field
from time import monotonic
from typing import Final

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


@dataclass
class CachedDecision:
    blocked: bool
    expires_at: float


@dataclass
class SourceIpCache:
    decisions: dict[str, CachedDecision] = field(default_factory=dict)


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

    user = users_collection.find_one(
        {"focusConfig.sourceIp": source_ip},
        {"focusConfig.blacklist": 1, "username": 1},
    )

    if not user:
        return []

    blacklist = user.get("focusConfig", {}).get("blacklist", [])
    if not isinstance(blacklist, list):
        return []

    return [str(entry).lower() for entry in blacklist]


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


def is_blocked(query_name: str, blacklist: list[str]) -> bool:
    return any(entry in query_name for entry in blacklist)


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
                if cached_blocked is not None:
                    blocked = cached_blocked
                    logging.info(
                        "Cache hit for %s from source ip %s: %s",
                        query_name,
                        source_ip,
                        "blocked" if blocked else "allowed",
                    )
                else:
                    blacklist = get_blacklist_for_source_ip(source_ip)
                    blocked = is_blocked(query_name, blacklist)
                    cache_decision(source_ip, query_name, blocked)
                    logging.info(
                        "Cache miss for %s from source ip %s; evaluated against %s blacklist entries",
                        query_name,
                        source_ip,
                        len(blacklist),
                    )

                if blocked:
                    logging.info(
                        "Blocked query for %s from source ip %s",
                        query_name,
                        source_ip,
                    )
                    response = build_blackhole_response(request, query_name, qtype)
                else:
                    logging.info(
                        "Relaying query for %s from source ip %s",
                        query_name,
                        source_ip,
                    )
                    response = relay_to_upstream(data)

                server.sendto(response, client_address)
            except Exception:
                logging.exception(
                    "Failed to process DNS request from %s", client_address
                )


if __name__ == "__main__":
    serve()
