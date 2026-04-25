from __future__ import annotations

import logging
import os
import socket
from typing import Final

from dnslib import A, AAAA, DNSHeader, DNSQuestion, DNSRecord, QTYPE, RR

LISTEN_HOST: Final[str] = os.environ.get("DNS_LISTEN_HOST", "0.0.0.0")
LISTEN_PORT: Final[int] = int(os.environ.get("DNS_PORT", "5354"))
UPSTREAM_DNS_HOST: Final[str] = os.environ.get("UPSTREAM_DNS_HOST", "1.1.1.1")
UPSTREAM_DNS_PORT: Final[int] = int(os.environ.get("UPSTREAM_DNS_PORT", "53"))
UPSTREAM_TIMEOUT_SECONDS: Final[float] = float(
    os.environ.get("UPSTREAM_TIMEOUT_SECONDS", "5.0")
)

BLACKLIST: Final[list[str]] = [
    "tiktok",
    "reddit",
    "roblox",
]


def extract_query_name(data: bytes) -> tuple[DNSRecord, str, str]:
    request = DNSRecord.parse(data)
    question: DNSQuestion | None = request.q
    if question is None:
        return request, "", "A"

    qname = str(question.qname).rstrip(".").lower()
    qtype = QTYPE[question.qtype]
    return request, qname, qtype


def is_blocked(query_name: str) -> bool:
    return any(entry in query_name for entry in BLACKLIST)


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
            "DNS relay listening on %s:%s and forwarding to %s:%s",
            LISTEN_HOST,
            LISTEN_PORT,
            UPSTREAM_DNS_HOST,
            UPSTREAM_DNS_PORT,
        )

        while True:
            data, client_address = server.recvfrom(4096)

            try:
                request, query_name, qtype = extract_query_name(data)

                if not query_name:
                    logging.warning("Received DNS query without a question section")
                    continue

                if is_blocked(query_name):
                    logging.info("Blocked query for %s", query_name)
                    response = build_blackhole_response(request, query_name, qtype)
                else:
                    logging.info("Relaying query for %s", query_name)
                    response = relay_to_upstream(data)

                server.sendto(response, client_address)
            except Exception:
                logging.exception(
                    "Failed to process DNS request from %s", client_address
                )


if __name__ == "__main__":
    serve()
