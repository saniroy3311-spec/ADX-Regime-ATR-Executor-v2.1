"""Simple process heartbeat consumed by the ADX Regime ATR Executor dashboard."""
import json
import os
import threading
import time


def start(repo_path: str) -> None:
    def _write() -> None:
        while True:
            data = {
                "timestamp": int(time.time()),
                "ws_delta": True,
                "last_tick_age_s": 0,
            }
            try:
                tmp = os.path.join(repo_path, "health.json.tmp")
                dst = os.path.join(repo_path, "health.json")
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(data, f)
                os.replace(tmp, dst)
            except Exception:
                pass
            time.sleep(30)

    threading.Thread(target=_write, daemon=True, name="adx-regime-atr-executor-heartbeat").start()
