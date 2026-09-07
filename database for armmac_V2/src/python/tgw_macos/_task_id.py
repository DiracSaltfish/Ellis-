"""Thread-safe implementation of TGW's public ``GetTaskID`` contract.

The TGW C++ manual defines an id as local ``MMDDHHmmSS`` followed by a
per-second sequence in the inclusive range 1..1,000,000.  The example pads
ordinary sequence values to six decimal places; the documented upper bound is
one million, whose ordinary decimal spelling needs a seventh place.  It is a process
local identifier: obtaining one must not initialise a transport or contact a
server.
"""
from __future__ import annotations

import datetime as _datetime
import threading
from collections.abc import Callable


MAX_TASK_SEQUENCE = 1_000_000


def compose_task_id(second: _datetime.datetime, sequence: int) -> int:
    """Compose one documented task id for *second* and *sequence*.

    Converting the representation to ``int`` intentionally drops a leading
    zero for January through September, as it does in the official Python
    wrapper.  ``:06d`` is a minimum width, so the documented value 1,000,000
    remains representable without truncation.
    """
    if not 1 <= sequence <= MAX_TASK_SEQUENCE:
        raise ValueError(
            f"task-id sequence must be in 1..{MAX_TASK_SEQUENCE}, got {sequence}"
        )
    return int(f"{second.strftime('%m%d%H%M%S')}{sequence:06d}")


class TaskIdGenerator:
    """Allocate documented task ids from an injectable local wall clock."""

    def __init__(
        self,
        clock: Callable[[], _datetime.datetime] | None = None,
    ) -> None:
        self._clock = clock or _datetime.datetime.now
        self._lock = threading.Lock()
        self._second: _datetime.datetime | None = None
        self._sequence = 0

    def next(self) -> int:
        """Return the next unique id for the current local second.

        The vendor documentation gives no overflow behavior beyond sequence
        1,000,000.  Refuse a same-second 1,000,001st request rather than emit a
        malformed seven-digit sequence or silently duplicate an id.
        """
        with self._lock:
            # Read the clock while holding the allocation lock.  Otherwise a
            # thread paused after seeing one second could overwrite a newer
            # second already allocated by another thread.
            second = self._clock().replace(microsecond=0)
            if second != self._second:
                self._second = second
                self._sequence = 0
            if self._sequence >= MAX_TASK_SEQUENCE:
                raise RuntimeError(
                    "GetTaskID exhausted the documented per-second sequence range"
                )
            self._sequence += 1
            return compose_task_id(second, self._sequence)
