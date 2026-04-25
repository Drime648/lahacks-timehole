from __future__ import annotations

import threading
import time

from gateway import main


def test_run_gateway_starts_both_runners():
    started = []
    hold = threading.Event()

    def dns_runner():
        started.append("dns")
        hold.wait()

    def proxy_runner():
        started.append("proxy")
        hold.wait()

    runner_thread = threading.Thread(
        target=main.run_gateway,
        kwargs={"dns_runner": dns_runner, "proxy_runner": proxy_runner},
        daemon=True,
    )
    runner_thread.start()

    deadline = time.time() + 1.0
    while len(started) < 2 and time.time() < deadline:
        time.sleep(0.01)

    assert sorted(started) == ["dns", "proxy"]
