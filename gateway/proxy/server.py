from __future__ import annotations

import http.client
import hashlib
import json
import logging
import os
import select
import socket
import ssl
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from time import monotonic
from typing import Final
from urllib.parse import urlsplit

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from gateway.cache import DecisionCache
from gateway.proxy.certificates import CertificateAuthorityManager
from gateway.proxy.filtering import (
    ProxyPolicyDecision,
    build_proxy_target_url,
    evaluate_proxy_decision,
    is_likely_main_document_request,
    normalize_http_target,
)
from gateway.proxy.gemma import GeminiGemmaClassifier
from gateway.store import MongoGatewayStore

PROXY_LISTEN_HOST: Final[str] = os.environ.get("PROXY_LISTEN_HOST", "0.0.0.0")
PROXY_LISTEN_PORT: Final[int] = int(os.environ.get("PROXY_PORT", "8080"))
PROXY_CONNECT_TIMEOUT_SECONDS: Final[float] = float(
    os.environ.get("PROXY_CONNECT_TIMEOUT_SECONDS", "10.0")
)
PROXY_CACHE_TTL_SECONDS: Final[float] = float(
    os.environ.get("PROXY_CACHE_TTL_SECONDS", os.environ.get("CACHE_TTL_SECONDS", "300"))
)
ENABLE_HTTPS_MITM: Final[bool] = os.environ.get("PROXY_ENABLE_HTTPS_MITM", "true").lower() == "true"
ENABLE_GEMMA_CLASSIFIER: Final[bool] = (
    os.environ.get("PROXY_ENABLE_GEMMA_CLASSIFIER", "false").lower() == "true"
)
CLASSIFIER_CACHE_VERSION: Final[str] = os.environ.get(
    "CLASSIFIER_CACHE_VERSION",
    "remote-gemma-v2-lenient-platforms",
)
GEMMA_RATE_LIMIT_CALLS: Final[int] = int(os.environ.get("GEMMA_RATE_LIMIT_CALLS", "20"))
GEMMA_RATE_LIMIT_WINDOW_SECONDS: Final[float] = float(
    os.environ.get("GEMMA_RATE_LIMIT_WINDOW_SECONDS", "60")
)
PROXY_CERTS_DIR: Final[str] = os.environ.get(
    "PROXY_CERTS_DIR",
    os.path.join(os.path.dirname(__file__), "certs"),
)

store = MongoGatewayStore.from_env()
decision_cache = DecisionCache(ttl_seconds=PROXY_CACHE_TTL_SECONDS)
cert_manager = CertificateAuthorityManager(PROXY_CERTS_DIR)
gemma_classifier = GeminiGemmaClassifier.from_env() if ENABLE_GEMMA_CLASSIFIER else None

HOP_BY_HOP_HEADERS = {
    "connection",
    "proxy-authenticate",
    "proxy-authorization",
    "keep-alive",
    "proxy-connection",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}


class SlidingWindowRateLimiter:
    def __init__(self, *, max_calls: int, window_seconds: float) -> None:
        self.max_calls = max_calls
        self.window_seconds = window_seconds
        self._timestamps: list[float] = []
        self._lock = threading.Lock()

    def allow(self, now: float | None = None) -> bool:
        current_time = monotonic() if now is None else now
        window_start = current_time - self.window_seconds

        with self._lock:
            self._timestamps = [timestamp for timestamp in self._timestamps if timestamp > window_start]
            if len(self._timestamps) >= self.max_calls:
                return False
            self._timestamps.append(current_time)
            return True


gemma_rate_limiter = SlidingWindowRateLimiter(
    max_calls=GEMMA_RATE_LIMIT_CALLS,
    window_seconds=GEMMA_RATE_LIMIT_WINDOW_SECONDS,
)


def parse_host_port(authority: str, default_port: int) -> tuple[str, int]:
    if authority.startswith("[") and "]" in authority:
        host, _, port_text = authority[1:].partition("]")
        if port_text.startswith(":"):
            return host, int(port_text[1:])
        return host, default_port

    if authority.count(":") == 1:
        host, port_text = authority.rsplit(":", 1)
        return host, int(port_text)

    return authority, default_port


def get_control_paths(path: str) -> tuple[str, str]:
    parsed = urlsplit(path)
    return parsed.path or "/", parsed.query


def should_serve_control_route(handler: BaseHTTPRequestHandler) -> bool:
    path, _ = get_control_paths(handler.path)
    return path in {"/__timehole/ca.crt", "/__timehole/setup"}


def read_request_body(handler: BaseHTTPRequestHandler) -> bytes:
    content_length = handler.headers.get("Content-Length")
    if not content_length:
        return b""
    return handler.rfile.read(int(content_length))


def get_focus_config_version(user: dict | None) -> str | None:
    if user is None:
        return CLASSIFIER_CACHE_VERSION

    focus_config = user.get("focusConfig", {})
    if not isinstance(focus_config, dict):
        return CLASSIFIER_CACHE_VERSION

    updated_at = focus_config.get("updatedAt")
    if updated_at is None:
        return CLASSIFIER_CACHE_VERSION

    return f"{updated_at}:{CLASSIFIER_CACHE_VERSION}"


def build_llm_cache_key(payload: dict) -> str:
    serialized = json.dumps(
        {
            "classifier_cache_version": CLASSIFIER_CACHE_VERSION,
            "payload": payload,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def write_raw_response(
    stream,
    *,
    status_code: int,
    reason: str,
    headers: dict[str, str],
    body: bytes,
) -> None:
    stream.write(f"HTTP/1.1 {status_code} {reason}\r\n".encode("utf-8"))
    for key, value in headers.items():
        stream.write(f"{key}: {value}\r\n".encode("utf-8"))
    stream.write(b"\r\n")
    if body:
        stream.write(body)
    stream.flush()


def try_write_raw_response(
    stream,
    *,
    status_code: int,
    reason: str,
    headers: dict[str, str],
    body: bytes,
    context: str,
) -> bool:
    try:
        write_raw_response(
            stream,
            status_code=status_code,
            reason=reason,
            headers=headers,
            body=body,
        )
        return True
    except (ssl.SSLError, BrokenPipeError, ConnectionResetError, OSError) as error:
        logging.info("HTTPS response write failed during %s: %s", context, error)
        return False


def build_block_page(target_url: str, reason: str) -> bytes:
    html = f"""<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Back On Task</title>
    <style>
      body {{
        margin: 0;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        background: linear-gradient(180deg, #fff8ef 0%, #f5ead8 100%);
        color: #1c241f;
        display: grid;
        place-items: center;
        min-height: 100vh;
        padding: 24px;
      }}
      .card {{
        max-width: 720px;
        background: rgba(255, 255, 255, 0.92);
        border: 1px solid rgba(28, 36, 31, 0.12);
        border-radius: 24px;
        padding: 28px;
        box-shadow: 0 18px 50px rgba(53, 36, 18, 0.1);
      }}
      h1 {{
        margin: 0 0 12px;
        font-size: 2rem;
      }}
      p {{
        line-height: 1.65;
        margin: 0 0 12px;
      }}
      code {{
        display: block;
        padding: 12px;
        border-radius: 14px;
        background: #10231c;
        color: #d9f4e8;
        overflow-wrap: anywhere;
      }}
    </style>
  </head>
  <body>
    <main class="card">
      <h1>Get back on task</h1>
      <p>This page was blocked by the TimeHole web proxy because it looks off-target for your active focus session.</p>
      <p>Decision: <strong>{reason}</strong></p>
      <p>Blocked URL:</p>
      <code>{target_url}</code>
      <p>If you expected this to be allowed, check whether your proxy rules, focus settings, or current study mode need to be adjusted.</p>
    </main>
  </body>
</html>"""
    return html.encode("utf-8")


def read_upstream_response(
    upstream_response: http.client.HTTPResponse,
) -> tuple[int, str, list[tuple[str, str]], bytes]:
    return (
        upstream_response.status,
        upstream_response.reason or "OK",
        list(upstream_response.getheaders()),
        upstream_response.read(),
    )


def send_http_response(
    handler: BaseHTTPRequestHandler,
    *,
    status_code: int,
    reason: str,
    headers: list[tuple[str, str]],
    body: bytes,
) -> None:
    handler.send_response(status_code, reason)
    for header, value in headers:
        if header.lower() in HOP_BY_HOP_HEADERS or header.lower() == "content-length":
            continue
        handler.send_header(header, value)
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Connection", "close")
    handler.end_headers()
    if handler.command != "HEAD":
        handler.wfile.write(body)


def send_https_response(
    stream,
    *,
    status_code: int,
    reason: str,
    headers: list[tuple[str, str]],
    body: bytes,
) -> None:
    headers = {
        header: value
        for header, value in headers
        if header.lower() not in HOP_BY_HOP_HEADERS and header.lower() != "content-length"
    }
    headers["Content-Length"] = str(len(body))
    headers["Connection"] = "close"
    if not try_write_raw_response(
        stream,
        status_code=status_code,
        reason=reason,
        headers=headers,
        body=body,
        context="https_upstream_response",
    ):
        raise ConnectionResetError("HTTPS client disconnected before upstream response could be written")


def get_header_value(headers: list[tuple[str, str]], name: str) -> str | None:
    lowered_name = name.lower()
    for header, value in headers:
        if header.lower() == lowered_name:
            return value
    return None


def build_upstream_headers(headers: dict[str, str], host: str) -> dict[str, str]:
    upstream_headers = {
        key: value
        for key, value in headers.items()
        if key.lower() not in HOP_BY_HOP_HEADERS
    }
    upstream_headers["Host"] = host
    # Ask upstream for an uncompressed response so HTML classification sees real text.
    upstream_headers["Accept-Encoding"] = "identity"
    return upstream_headers


class TimeHoleProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "TimeHoleProxy/0.2"

    def _cache_final_decision(
        self,
        source_ip: str,
        target_url: str,
        blocked: bool,
        config_version: str | None,
    ) -> None:
        decision_cache.cache_decision(
            source_ip,
            target_url,
            blocked,
            config_version=config_version,
        )

    def _classify_with_gemma_cache(
        self,
        *,
        source_ip: str,
        config_version: str | None,
        payload: dict,
    ) -> str:
        if gemma_classifier is None:
            return "allow"

        cache_key = build_llm_cache_key(payload)
        cached_decision = decision_cache.get_cached_llm_decision(
            source_ip,
            cache_key,
            config_version=config_version,
        )
        if cached_decision is not None:
            return cached_decision

        if not gemma_rate_limiter.allow():
            logging.warning(
                "Gemma rate limit reached (%s calls/%ss); allowing %s without remote classification",
                GEMMA_RATE_LIMIT_CALLS,
                GEMMA_RATE_LIMIT_WINDOW_SECONDS,
                payload.get("target_url"),
            )
            return "allow"

        try:
            decision = gemma_classifier(payload)
        except Exception:
            logging.exception(
                "Gemma classifier failed for phase=%s url=%s",
                payload.get("phase"),
                payload.get("target_url"),
            )
            return "allow"

        decision_cache.cache_llm_decision(
            source_ip,
            cache_key,
            decision,
            config_version=config_version,
        )
        return decision

    def _build_policy(
        self,
        *,
        source_ip: str,
        target_url: str,
        path: str,
        query: str,
        user: dict | None,
        cached_blocked: bool | None,
        config_version: str | None,
        response_body: bytes | None = None,
        response_content_type: str | None = None,
    ):
        return evaluate_proxy_decision(
            source_ip=source_ip,
            target_url=target_url,
            path=path,
            query=query,
            user=user,
            cached_blocked=cached_blocked,
            cache_decision=lambda ip, url, blocked: self._cache_final_decision(
                ip,
                url,
                blocked,
                config_version,
            ),
            response_body=response_body,
            response_content_type=response_content_type,
            semantic_classifier=lambda payload: self._classify_with_gemma_cache(
                source_ip=source_ip,
                config_version=config_version,
                payload=payload,
            ),
        )

    def _non_document_policy(
        self,
        *,
        source_ip: str,
        target_url: str,
        config_version: str | None,
    ):
        self._cache_final_decision(source_ip, target_url, False, config_version)
        return ProxyPolicyDecision(
            blocked=False,
            cache_hit=False,
            decision_reason="non_document_request",
            blacklist_size=0,
        )

    def do_CONNECT(self) -> None:
        # A CONNECT request upgrades this socket into either a raw tunnel or a
        # locally-terminated TLS session. After that completes, the original
        # BaseHTTPRequestHandler connection should not be reused for another
        # HTTP request cycle.
        self.close_connection = True
        source_ip = self.client_address[0]
        authority = self.path.strip()
        host, port = parse_host_port(authority, 443)
        user = store.get_user_context(source_ip)
        username = str(user.get("username")) if user and user.get("username") else None

        if not ENABLE_HTTPS_MITM:
            self._handle_https_passthrough(
                source_ip=source_ip,
                authority=authority,
                host=host,
                port=port,
                user=user,
                username=username,
            )
            return

        self.send_response(200, "Connection Established")
        self.end_headers()

        try:
            client_context = cert_manager.build_server_context(host)
            client_tls = client_context.wrap_socket(
                self.connection,
                server_side=True,
            )
        except Exception as error:
            store.log_proxy_event(
                source_ip=source_ip,
                username=username,
                user_matched=user is not None,
                method="CONNECT",
                scheme="https",
                host=host,
                path="/",
                query="",
                target_url=build_proxy_target_url("https", authority, "/", ""),
                blocked=False,
                cache_hit=False,
                decision_reason="mitm_handshake_error",
                status_code=502,
                upstream_latency_ms=None,
                https_tunnel=True,
                mitm_enabled=True,
                error=str(error),
            )
            return

        self._handle_https_intercepted_session(
            client_tls=client_tls,
            source_ip=source_ip,
            host=host,
            port=port,
            user=user,
            username=username,
        )

    def do_GET(self) -> None:
        self._handle_http_request()

    def do_POST(self) -> None:
        self._handle_http_request()

    def do_PUT(self) -> None:
        self._handle_http_request()

    def do_DELETE(self) -> None:
        self._handle_http_request()

    def do_HEAD(self) -> None:
        self._handle_http_request()

    def do_OPTIONS(self) -> None:
        self._handle_http_request()

    def do_PATCH(self) -> None:
        self._handle_http_request()

    def _handle_http_request(self) -> None:
        if should_serve_control_route(self):
            self._serve_control_route()
            return

        source_ip = self.client_address[0]
        host_header = self.headers.get("Host", "")
        scheme, host, path, query, target_url = normalize_http_target(
            path=self.path,
            host_header=host_header,
        )
        if not host:
            self.send_error(400, "Host header is required")
            return

        user = store.get_user_context(source_ip)
        username = str(user.get("username")) if user and user.get("username") else None
        config_version = get_focus_config_version(user)
        if not is_likely_main_document_request(
            method=self.command,
            path=target_url,
            headers=dict(self.headers.items()),
        ):
            policy = self._non_document_policy(
                source_ip=source_ip,
                target_url=target_url,
                config_version=config_version,
            )
        else:
            policy = None
        cached_blocked = decision_cache.get_cached_decision(
            source_ip,
            target_url,
            config_version=config_version,
        )
        if policy is None:
            policy = self._build_policy(
                source_ip=source_ip,
                target_url=target_url,
                path=path,
                query=query,
                user=user,
                cached_blocked=cached_blocked,
                config_version=config_version,
            )

        if policy.blocked:
            body = build_block_page(target_url, policy.decision_reason)
            logging.info(
                "Blocked HTTP request for %s from source ip %s via reason=%s",
                target_url,
                source_ip,
                policy.decision_reason,
            )
            self.send_response(403, "Blocked by TimeHole proxy")
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)
            self._log_proxy(
                source_ip=source_ip,
                username=username,
                user=user,
                method=self.command,
                scheme=scheme,
                host=host,
                path=path,
                query=query,
                target_url=target_url,
                blocked=True,
                cache_hit=policy.cache_hit,
                decision_reason=policy.decision_reason,
                status_code=403,
                upstream_latency_ms=None,
                https_tunnel=False,
                mitm_enabled=False,
                error=None,
            )
            return

        body = read_request_body(self)
        upstream_headers = build_upstream_headers(dict(self.headers.items()), host)
        upstream_path = path if not query else f"{path}?{query}"

        upstream_host, upstream_port = parse_host_port(host, 443 if scheme == "https" else 80)
        connection_class = (
            http.client.HTTPSConnection if scheme == "https" else http.client.HTTPConnection
        )
        start = monotonic()
        connection = None
        status_code = None
        error = None

        try:
            connection = connection_class(
                upstream_host,
                upstream_port,
                timeout=PROXY_CONNECT_TIMEOUT_SECONDS,
            )
            connection.request(
                self.command,
                upstream_path,
                body=body if body else None,
                headers=upstream_headers,
            )
            upstream_response = connection.getresponse()
            status_code, reason, response_headers, response_body = read_upstream_response(upstream_response)
            final_policy = policy

            if policy.decision_reason == "gemma_needs_html":
                final_policy = self._build_policy(
                    source_ip=source_ip,
                    target_url=target_url,
                    path=path,
                    query=query,
                    user=user,
                    cached_blocked=None,
                    config_version=config_version,
                    response_body=response_body,
                    response_content_type=get_header_value(response_headers, "Content-Type"),
                )

            if final_policy.blocked:
                body = build_block_page(target_url, final_policy.decision_reason)
                logging.info(
                    "Blocked HTTP request for %s from source ip %s via reason=%s",
                    target_url,
                    source_ip,
                    final_policy.decision_reason,
                )
                self.send_response(403, "Blocked by TimeHole proxy")
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)
                self._log_proxy(
                    source_ip=source_ip,
                    username=username,
                    user=user,
                    method=self.command,
                    scheme=scheme,
                    host=host,
                    path=path,
                    query=query,
                    target_url=target_url,
                    blocked=True,
                    cache_hit=final_policy.cache_hit,
                    decision_reason=final_policy.decision_reason,
                    status_code=403,
                    upstream_latency_ms=None,
                    https_tunnel=False,
                    mitm_enabled=False,
                    error=None,
                )
                return

            send_http_response(
                self,
                status_code=status_code,
                reason=reason,
                headers=response_headers,
                body=response_body,
            )
            policy = final_policy
        except Exception as upstream_error:
            self.send_error(502, f"Proxy upstream failure: {upstream_error}")
            status_code = 502
            error = str(upstream_error)
        finally:
            upstream_latency_ms = round((monotonic() - start) * 1000, 2)
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass

        self._log_proxy(
            source_ip=source_ip,
            username=username,
            user=user,
            method=self.command,
            scheme=scheme,
            host=host,
            path=path,
            query=query,
            target_url=target_url,
            blocked=False,
            cache_hit=policy.cache_hit,
            decision_reason=policy.decision_reason if error is None else "upstream_error",
            status_code=status_code,
            upstream_latency_ms=upstream_latency_ms,
            https_tunnel=False,
            mitm_enabled=False,
            error=error,
        )

    def _serve_control_route(self) -> None:
        path, _ = get_control_paths(self.path)

        if path == "/__timehole/ca.crt":
            body = cert_manager.get_root_ca_pem()
            self.send_response(200, "OK")
            self.send_header("Content-Type", "application/x-pem-file")
            self.send_header("Content-Disposition", 'attachment; filename="timehole-root-ca.crt.pem"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        setup = {
            "proxyHost": PROXY_LISTEN_HOST if PROXY_LISTEN_HOST != "0.0.0.0" else "127.0.0.1",
            "proxyPort": PROXY_LISTEN_PORT,
            "caDownloadPath": "/__timehole/ca.crt",
            "mitmEnabled": ENABLE_HTTPS_MITM,
        }
        body = json.dumps(setup).encode("utf-8")
        self.send_response(200, "OK")
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle_https_passthrough(
        self,
        *,
        source_ip: str,
        authority: str,
        host: str,
        port: int,
        user: dict | None,
        username: str | None,
    ) -> None:
        start = monotonic()
        try:
            upstream = socket.create_connection((host, port), timeout=PROXY_CONNECT_TIMEOUT_SECONDS)
        except Exception as error:
            self.send_error(502, f"Failed to connect upstream: {error}")
            self._log_proxy(
                source_ip=source_ip,
                username=username,
                user=user,
                method="CONNECT",
                scheme="https",
                host=host,
                path="/",
                query="",
                target_url=build_proxy_target_url("https", authority, "/", ""),
                blocked=False,
                cache_hit=False,
                decision_reason="connect_error",
                status_code=502,
                upstream_latency_ms=round((monotonic() - start) * 1000, 2),
                https_tunnel=True,
                mitm_enabled=False,
                error=str(error),
            )
            return

        self.send_response(200, "Connection Established")
        self.end_headers()

        sockets = [self.connection, upstream]
        try:
            while True:
                readable, _, exceptional = select.select(sockets, [], sockets, 1.0)
                if exceptional:
                    break
                for ready in readable:
                    payload = ready.recv(64 * 1024)
                    if not payload:
                        return
                    target = upstream if ready is self.connection else self.connection
                    target.sendall(payload)
        finally:
            upstream.close()
            self._log_proxy(
                source_ip=source_ip,
                username=username,
                user=user,
                method="CONNECT",
                scheme="https",
                host=host,
                path="/",
                query="",
                target_url=build_proxy_target_url("https", authority, "/", ""),
                blocked=False,
                cache_hit=False,
                decision_reason="https_connect_passthrough",
                status_code=200,
                upstream_latency_ms=round((monotonic() - start) * 1000, 2),
                https_tunnel=True,
                mitm_enabled=False,
                error=None,
            )

    def _handle_https_intercepted_session(
        self,
        *,
        client_tls: ssl.SSLSocket,
        source_ip: str,
        host: str,
        port: int,
        user: dict | None,
        username: str | None,
    ) -> None:
        reader = client_tls.makefile("rb")
        writer = client_tls.makefile("wb")

        try:
            request_line = reader.readline().decode("iso-8859-1").strip()
            if not request_line:
                return

            parts = request_line.split(" ")
            if len(parts) != 3:
                write_raw_response(
                    writer,
                    status_code=400,
                    reason="Bad Request",
                    headers={"Content-Length": "0", "Connection": "close"},
                    body=b"",
                )
                return

            method, raw_path, _version = parts
            headers: dict[str, str] = {}
            while True:
                line = reader.readline().decode("iso-8859-1")
                if line in {"\r\n", "\n", ""}:
                    break
                name, value = line.split(":", 1)
                headers[name.strip()] = value.strip()

            content_length = int(headers.get("Content-Length", "0") or "0")
            body = reader.read(content_length) if content_length else b""
            parsed = urlsplit(raw_path)
            path = parsed.path or "/"
            query = parsed.query
            target_url = build_proxy_target_url("https", host, path, query)
            config_version = get_focus_config_version(user)
            if not is_likely_main_document_request(
                method=method,
                path=target_url,
                headers=headers,
            ):
                policy = self._non_document_policy(
                    source_ip=source_ip,
                    target_url=target_url,
                    config_version=config_version,
                )
            else:
                policy = None
            cached_blocked = decision_cache.get_cached_decision(
                source_ip,
                target_url,
                config_version=config_version,
            )
            if policy is None:
                policy = self._build_policy(
                    source_ip=source_ip,
                    target_url=target_url,
                    path=path,
                    query=query,
                    user=user,
                    cached_blocked=cached_blocked,
                    config_version=config_version,
                )

            if policy.blocked:
                blocked_body = build_block_page(target_url, policy.decision_reason)
                logging.info(
                    "Blocked HTTPS request for %s from source ip %s via reason=%s",
                    target_url,
                    source_ip,
                    policy.decision_reason,
                )
                write_raw_response(
                    writer,
                    status_code=403,
                    reason="Blocked by TimeHole proxy",
                    headers={
                        "Content-Type": "text/html; charset=utf-8",
                        "Content-Length": str(len(blocked_body)),
                        "Connection": "close",
                    },
                    body=blocked_body,
                )
                self._log_proxy(
                    source_ip=source_ip,
                    username=username,
                    user=user,
                    method=method,
                    scheme="https",
                    host=host,
                    path=path,
                    query=query,
                    target_url=target_url,
                    blocked=True,
                    cache_hit=policy.cache_hit,
                    decision_reason=policy.decision_reason,
                    status_code=403,
                    upstream_latency_ms=None,
                    https_tunnel=True,
                    mitm_enabled=True,
                    error=None,
                )
                return

            upstream_headers = build_upstream_headers(headers, host)
            start = monotonic()
            connection = None
            status_code = None
            error = None

            try:
                connection = http.client.HTTPSConnection(
                    host,
                    port,
                    timeout=PROXY_CONNECT_TIMEOUT_SECONDS,
                )
                upstream_path = path if not query else f"{path}?{query}"
                connection.request(
                    method,
                    upstream_path,
                    body=body if body else None,
                    headers=upstream_headers,
                )
                upstream_response = connection.getresponse()
                status_code, reason, response_headers, response_body = read_upstream_response(
                    upstream_response
                )
                final_policy = policy

                if policy.decision_reason == "gemma_needs_html":
                    final_policy = self._build_policy(
                        source_ip=source_ip,
                        target_url=target_url,
                        path=path,
                        query=query,
                        user=user,
                        cached_blocked=None,
                        config_version=config_version,
                        response_body=response_body,
                        response_content_type=get_header_value(response_headers, "Content-Type"),
                    )

                if final_policy.blocked:
                    blocked_body = build_block_page(target_url, final_policy.decision_reason)
                    logging.info(
                        "Blocked HTTPS request for %s from source ip %s via reason=%s",
                        target_url,
                        source_ip,
                        final_policy.decision_reason,
                    )
                    if not try_write_raw_response(
                        writer,
                        status_code=403,
                        reason="Blocked by TimeHole proxy",
                        headers={
                            "Content-Type": "text/html; charset=utf-8",
                            "Content-Length": str(len(blocked_body)),
                            "Connection": "close",
                        },
                        body=blocked_body,
                        context="https_block_page",
                    ):
                        return
                    self._log_proxy(
                        source_ip=source_ip,
                        username=username,
                        user=user,
                        method=method,
                        scheme="https",
                        host=host,
                        path=path,
                        query=query,
                        target_url=target_url,
                        blocked=True,
                        cache_hit=final_policy.cache_hit,
                        decision_reason=final_policy.decision_reason,
                        status_code=403,
                        upstream_latency_ms=None,
                        https_tunnel=True,
                        mitm_enabled=True,
                        error=None,
                    )
                    return

                send_https_response(
                    writer,
                    status_code=status_code,
                    reason=reason,
                    headers=response_headers,
                    body=response_body,
                )
                policy = final_policy
            except Exception as upstream_error:
                error = str(upstream_error)
                error_body = error.encode("utf-8", "replace")
                if not try_write_raw_response(
                    writer,
                    status_code=502,
                    reason="Bad Gateway",
                    headers={
                        "Content-Type": "text/plain; charset=utf-8",
                        "Content-Length": str(len(error_body)),
                        "Connection": "close",
                    },
                    body=error_body,
                    context="https_bad_gateway",
                ):
                    return
                status_code = 502
            finally:
                upstream_latency_ms = round((monotonic() - start) * 1000, 2)
                if connection is not None:
                    try:
                        connection.close()
                    except Exception:
                        pass

            self._log_proxy(
                source_ip=source_ip,
                username=username,
                user=user,
                method=method,
                scheme="https",
                host=host,
                path=path,
                query=query,
                target_url=target_url,
                blocked=False,
                cache_hit=policy.cache_hit,
                decision_reason=policy.decision_reason if error is None else "upstream_error",
                status_code=status_code,
                upstream_latency_ms=upstream_latency_ms,
                https_tunnel=True,
                mitm_enabled=True,
                error=error,
            )
        finally:
            try:
                writer.close()
            except Exception:
                pass
            try:
                reader.close()
            except Exception:
                pass
            try:
                client_tls.close()
            except Exception:
                pass

    def _log_proxy(
        self,
        *,
        source_ip: str,
        username: str | None,
        user: dict | None,
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
        pass
        store.log_proxy_event(
            source_ip=source_ip,
            username=username,
            user_matched=user is not None,
            method=method,
            scheme=scheme,
            host=host,
            path=path,
            query=query,
            target_url=target_url,
            blocked=blocked,
            cache_hit=cache_hit,
            decision_reason=decision_reason,
            status_code=status_code,
            upstream_latency_ms=upstream_latency_ms,
            https_tunnel=https_tunnel,
            mitm_enabled=mitm_enabled,
            error=error,
        )

    def log_message(self, format: str, *args) -> None:
       ## logging.info("%s - %s", self.client_address[0], format % args)
       pass

def serve() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    server = ThreadingHTTPServer((PROXY_LISTEN_HOST, PROXY_LISTEN_PORT), TimeHoleProxyHandler)
    logging.info(
        "HTTP proxy listening on %s:%s (HTTPS MITM=%s, CA=%s)",
        PROXY_LISTEN_HOST,
        PROXY_LISTEN_PORT,
        ENABLE_HTTPS_MITM,
        cert_manager.root_cert_path,
    )
    server.serve_forever()
