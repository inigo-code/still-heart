from __future__ import annotations

import threading
import time

from app import engine, guards, jobs, settings

_started = False
_lock = threading.Lock()


def start() -> None:
    global _started
    with _lock:
        if _started:
            return
        _started = True

    def loop() -> None:
        while True:
            try:
                cfg = settings.load()
                cap = int(cfg.get("max_active") or 3)
                if guards.snapshot().get("blocked") or engine.is_updating():
                    time.sleep(2)
                    continue
                if jobs.active_count() < cap:
                    nxt = jobs.take_queued()
                    if nxt:
                        threading.Thread(target=engine.run_job, args=(nxt["id"],), daemon=True).start()
            except Exception:
                pass
            time.sleep(0.25)

    threading.Thread(target=loop, daemon=True).start()

    def _warmup() -> None:
        time.sleep(1.5)
        try:
            from app.runtime import warm_runtime

            warm_runtime()
            from app.runtime import gallery_dl_version

            engine._update_state["current"] = gallery_dl_version()
        except Exception:
            pass
        try:
            engine.load_extractors(False)
        except Exception:
            pass
        try:
            engine.check_engine()
        except Exception:
            pass

    threading.Thread(target=_warmup, daemon=True).start()
