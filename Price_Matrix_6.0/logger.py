"""
logger.py — the one place progress messages are routed.

Every module (scrapper, parser, comparator, app) reports through `log()`, so the
same messages can be:

    * printed to the terminal (unchanged look, still `print()`-like), and
    * streamed to the browser's "Live scan log" panel over SSE.

app.py installs a sink for the active scrape job:

    from logger import set_sink, clear_sink
    set_sink(job.emit)      # each message -> job log + SSE queue
    ...
    clear_sink()

Running the scripts standalone (no Flask) works exactly as before: messages
just print. Nothing here changes any parsing/comparison logic — it only decides
where the text goes.
"""

from __future__ import annotations

import sys
import threading
import time
from typing import Callable, Optional

# The active sink (or None when running standalone). Guarded by a lock because
# app.py may run Amazon and Flipkart scrape threads at the same time.
_SINK: Optional[Callable[[str], None]] = None
_LOCK = threading.Lock()


def set_sink(sink: Optional[Callable[[str], None]]) -> None:
    """Install (or replace) the running job's log sink."""
    global _SINK
    with _LOCK:
        _SINK = sink


def clear_sink() -> None:
    """Remove the sink — subsequent messages only print to stdout."""
    global _SINK
    with _LOCK:
        _SINK = None


def get_sink() -> Optional[Callable[[str], None]]:
    with _LOCK:
        return _SINK


def log(message: str = "") -> None:
    """
    Publish one progress line.

    Always prints (with an optional HH:MM:SS prefix) and additionally forwards
    the raw message to the active sink so the web UI can show it live.
    """
    text = "" if message is None else str(message)

    try:
        stamp = time.strftime("%H:%M:%S")
        print(f"[{stamp}] {text}", file=sys.stdout, flush=True)
    except Exception:                      # never let logging break a scrape
        pass

    sink = get_sink()
    if sink is not None:
        try:
            sink(text)
        except Exception:
            # A dead SSE client must not kill the pipeline.
            clear_sink()


__all__ = ["log", "set_sink", "clear_sink", "get_sink"]
