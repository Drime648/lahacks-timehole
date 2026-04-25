from __future__ import annotations

import logging
import os
import signal
import sys
import threading
from pathlib import Path
from typing import Callable

if __package__ in {None, ""}:
    script_dir = Path(__file__).resolve().parent
    repo_root = script_dir.parent
    script_dir_str = str(script_dir)
    repo_root_str = str(repo_root)
    sys.path[:] = [entry for entry in sys.path if Path(entry or ".").resolve() != script_dir]
    if repo_root_str not in sys.path:
        sys.path.insert(0, repo_root_str)

# Set gateway-wide defaults before importing service modules, since they read
# their listen ports from the environment at import time.
os.environ.setdefault("DNS_PORT", "53")
os.environ.setdefault("PROXY_PORT", "8080")

from gateway.dns.relay import serve as serve_dns
from gateway.proxy.server import serve as serve_proxy


def run_gateway(
    dns_runner: Callable[[], None] = serve_dns,
    proxy_runner: Callable[[], None] = serve_proxy,
) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    threads = [
        threading.Thread(target=dns_runner, name="timehole-dns", daemon=True),
        threading.Thread(target=proxy_runner, name="timehole-proxy", daemon=True),
    ]

    for thread in threads:
        thread.start()

    stop_event = threading.Event()

    def handle_signal(_signum, _frame) -> None:
        logging.info("Gateway shutdown signal received")
        stop_event.set()

    if threading.current_thread() is threading.main_thread():
        for signum in (signal.SIGINT, signal.SIGTERM):
            signal.signal(signum, handle_signal)

    try:
        while not stop_event.is_set():
            for thread in threads:
                if not thread.is_alive():
                    raise RuntimeError(f"{thread.name} stopped unexpectedly")
            stop_event.wait(0.5)
    finally:
        logging.info("Gateway main process exiting")


if __name__ == "__main__":
    run_gateway()
