from __future__ import annotations

import http.client
import json
import logging
import os
import select
import socket
import ssl
import sys
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
    build_proxy_target_url,
    evaluate_proxy_decision,
    get_user_blacklist,
    get_user_manual_blacklist,
    normalize_http_target,
)
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
PROXY_CERTS_DIR: Final[str] = os.environ.get(
    "PROXY_CERTS_DIR",
    os.path.join(os.path.dirname(__file__), "certs"),
)

store = MongoGatewayStore.from_env()
decision_cache = DecisionCache(ttl_seconds=PROXY_CACHE_TTL_SECONDS)
cert_manager = CertificateAuthorityManager(PROXY_CERTS_DIR)

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
        return None

    focus_config = user.get("focusConfig", {})
    if not isinstance(focus_config, dict):
        return None

    updated_at = focus_config.get("updatedAt")
    if updated_at is None:
        return None

    return str(updated_at)


def log_active_blacklist(
    *,
    source_ip: str,
    username: str | None,
    config_version: str | None,
    user: dict | None,
) -> None:
    blacklist = get_user_blacklist(user)
    manual_blacklist = get_user_manual_blacklist(user)
    logging.info(
        "Active proxy blacklist for source ip %s username=%s config=%s manual_size=%s manual_entries=%s effective_size=%s effective_entries=%s",
        source_ip,
        username,
        config_version,
        len(manual_blacklist),
        manual_blacklist,
        len(blacklist),
        blacklist,
    )


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


def relay_http_response(handler: BaseHTTPRequestHandler, upstream_response: http.client.HTTPResponse) -> None:
    body = upstream_response.read()
    handler.send_response(upstream_response.status, upstream_response.reason)
    for header, value in upstream_response.getheaders():
        if header.lower() in HOP_BY_HOP_HEADERS or header.lower() == "content-length":
            continue
        handler.send_header(header, value)
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Connection", "close")
    handler.end_headers()
    if handler.command != "HEAD":
        handler.wfile.write(body)


def relay_https_response(stream, upstream_response: http.client.HTTPResponse) -> None:
    body = upstream_response.read()
    headers = {
        header: value
        for header, value in upstream_response.getheaders()
        if header.lower() not in HOP_BY_HOP_HEADERS and header.lower() != "content-length"
    }
    headers["Content-Length"] = str(len(body))
    headers["Connection"] = "close"
    write_raw_response(
        stream,
        status_code=upstream_response.status,
        reason=upstream_response.reason or "OK",
        headers=headers,
        body=body,
    )


class TimeHoleProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "TimeHoleProxy/0.2"

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
        log_active_blacklist(
            source_ip=source_ip,
            username=username,
            config_version=config_version,
            user=user,
        )
        cached_blocked = decision_cache.get_cached_decision(
            source_ip,
            target_url,
            config_version=config_version,
        )
        policy = evaluate_proxy_decision(
            source_ip=source_ip,
            target_url=target_url,
            path=path,
            query=query,
            user=user,
            cached_blocked=cached_blocked,
            cache_decision=lambda ip, url, blocked: decision_cache.cache_decision(
                ip,
                url,
                blocked,
                config_version=config_version,
            ),
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
        upstream_headers = {
            key: value
            for key, value in self.headers.items()
            if key.lower() not in HOP_BY_HOP_HEADERS
        }
        upstream_headers["Host"] = host
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
            relay_http_response(self, upstream_response)
            status_code = upstream_response.status
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
            log_active_blacklist(
                source_ip=source_ip,
                username=username,
                config_version=config_version,
                user=user,
            )
            cached_blocked = decision_cache.get_cached_decision(
                source_ip,
                target_url,
                config_version=config_version,
            )
            policy = evaluate_proxy_decision(
                source_ip=source_ip,
                target_url=target_url,
                path=path,
                query=query,
                user=user,
                cached_blocked=cached_blocked,
                cache_decision=lambda ip, url, blocked: decision_cache.cache_decision(
                    ip,
                    url,
                    blocked,
                    config_version=config_version,
                ),
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

            upstream_headers = {
                key: value
                for key, value in headers.items()
                if key.lower() not in HOP_BY_HOP_HEADERS
            }
            upstream_headers["Host"] = host
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
                relay_https_response(writer, upstream_response)
                status_code = upstream_response.status
            except Exception as upstream_error:
                error = str(upstream_error)
                error_body = error.encode("utf-8", "replace")
                write_raw_response(
                    writer,
                    status_code=502,
                    reason="Bad Gateway",
                    headers={
                        "Content-Type": "text/plain; charset=utf-8",
                        "Content-Length": str(len(error_body)),
                        "Connection": "close",
                    },
                    body=error_body,
                )
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
        return


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
