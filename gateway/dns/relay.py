from __future__ import annotations

import logging
import os
import socket
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from time import monotonic
from typing import Final

from dnslib import A, AAAA, DNSHeader, DNSQuestion, DNSRecord, QTYPE, RR

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from gateway.cache import DecisionCache
from gateway.dns.filtering import evaluate_policy_decision
from gateway.store import MongoGatewayStore

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
MAX_WORKERS: Final[int] = int(os.environ.get("DNS_MAX_WORKERS", "10"))

store = MongoGatewayStore.from_env()
decision_cache = DecisionCache(ttl_seconds=CACHE_TTL_SECONDS)


def extract_query_name(data: bytes) -> tuple[DNSRecord, str, str]:
    request = DNSRecord.parse(data)
    question: DNSQuestion | None = request.q
    if question is None:
        return request, "", "A"

    qname = str(question.qname).rstrip(".").lower()
    qtype = QTYPE[question.qtype]
    return request, qname, qtype


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


def get_focus_config_version(user: dict | None) -> str | None:
    if user is None:
        return None

    focus_config = user.get("focusConfig", {})
    if not isinstance(focus_config, dict):
        return None

    updated_at = focus_config.get("updatedAt")
    if updated_at is None:
        return None

    return str(updated_at)


def relay_to_upstream(data: bytes) -> bytes:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as upstream:
        upstream.settimeout(UPSTREAM_TIMEOUT_SECONDS)
        upstream.sendto(data, (UPSTREAM_DNS_HOST, UPSTREAM_DNS_PORT))
        response, _ = upstream.recvfrom(4096)
        return response


def handle_dns_request(
    data: bytes, client_address: tuple[str, int], server_socket: socket.socket
) -> None:
    try:
        request, query_name, qtype = extract_query_name(data)
        source_ip = client_address[0]

        if not query_name:
            logging.warning("Received DNS query without a question section")
            return

        # Bypass store for MongoDB Atlas hostnames to prevent deadlocks
        # if the system DNS is set to this relay.
        is_mongodb_query = "mongodb.net" in query_name
        if is_mongodb_query:
            logging.info("Bypassing policy check for MongoDB host: %s", query_name)
            try:
                response = relay_to_upstream(data)
                server_socket.sendto(response, client_address)
                return
            except Exception as e:
                logging.error("Failed to relay MongoDB query %s: %s", query_name, e)
                response = build_servfail_response(request)
                server_socket.sendto(response, client_address)
                return

        user = store.get_user_context(source_ip)
        config_version = get_focus_config_version(user)
        cached_blocked = decision_cache.get_cached_decision(
            source_ip,
            query_name,
            config_version=config_version,
        )
        username = (
            str(user.get("username")) if user and user.get("username") else None
        )
        policy = evaluate_policy_decision(
            source_ip=source_ip,
            query_name=query_name,
            user=user,
            cached_blocked=cached_blocked,
            source_blacklist_loader=store.get_blacklist_for_source_ip,
            cache_decision=lambda ip, query, blocked: decision_cache.cache_decision(
                ip,
                query,
                blocked,
                config_version=config_version,
            ),
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

        store.log_dns_event(
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
        server_socket.sendto(response, client_address)
    except Exception:
        logging.exception("Failed to process DNS request from %s", client_address)


def serve() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as server:
        server.bind((LISTEN_HOST, LISTEN_PORT))
        logging.info(
            "DNS relay listening on %s:%s and forwarding to %s:%s using db=%s (workers=%d)",
            LISTEN_HOST,
            LISTEN_PORT,
            UPSTREAM_DNS_HOST,
            UPSTREAM_DNS_PORT,
            MONGODB_DB_NAME,
            MAX_WORKERS,
        )

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            while True:
                try:
                    data, client_address = server.recvfrom(4096)
                except ConnectionResetError:
                    # On Windows, a 10054 error can be raised on a UDP socket if a 
                    # previous sendto failed (e.g., ICMP port unreachable).
                    continue
                except Exception:
                    logging.exception("Failed to receive DNS packet")
                    continue

                executor.submit(handle_dns_request, data, client_address, server)


if __name__ == "__main__":
    serve()
