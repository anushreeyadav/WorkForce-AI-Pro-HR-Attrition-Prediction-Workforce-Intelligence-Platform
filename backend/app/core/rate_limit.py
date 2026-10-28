"""In-process sliding-window rate limiting for the authentication endpoints (Sub-prompt 31).

Scope and limits, stated plainly:
- State lives in this process's memory. Each worker process counts separately and counts reset on restart. A
  deployment with several workers or hosts needs a shared store (for example Redis or a database table) to enforce
  one limit across all of them; until then the effective limit is "per worker".
- Keys are the client address as seen by this process (request.client.host). Behind a reverse proxy that is the
  proxy's address unless the server is started with trusted forwarding (uvicorn --proxy-headers and
  --forwarded-allow-ips set to the proxy), so all users would share one budget. Configure the proxy first.
- Memory is bounded: at most MAX_KEYS keys are tracked; the stalest keys are dropped first.
Thread-safe (FastAPI runs sync handlers in a thread pool).
"""

import hashlib
import threading
import time
from collections import OrderedDict, deque

MAX_KEYS = 20_000


class SlidingWindowLimiter:
    def __init__(self, max_keys: int = MAX_KEYS, clock=time.monotonic) -> None:
        self._events: OrderedDict[str, deque[float]] = OrderedDict()
        self._lock = threading.Lock()
        self._max_keys = max_keys
        self._clock = clock

    def _window(self, key: str, window: float, now: float) -> deque[float]:
        events = self._events.get(key)
        if events is None:
            events = deque()
            self._events[key] = events
            while len(self._events) > self._max_keys:
                self._events.popitem(last=False)
        else:
            self._events.move_to_end(key)
        while events and events[0] <= now - window:
            events.popleft()
        return events

    def retry_after(self, key: str, limit: int, window: float) -> int | None:
        """Seconds until another event is allowed, or None if the key is under its limit. Records nothing."""
        with self._lock:
            now = self._clock()
            events = self._window(key, window, now)
            if len(events) < limit:
                return None
            return max(1, int(events[0] + window - now + 0.999))

    def record(self, key: str, window: float) -> None:
        with self._lock:
            now = self._clock()
            self._window(key, window, now).append(now)

    def reset(self, key: str) -> None:
        with self._lock:
            self._events.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._events.clear()


limiter = SlidingWindowLimiter()


def account_key(client: str, email: str) -> str:
    """Per client and submitted email. The email is hashed so it is never held in memory as typed; the key is the
    same whether or not an account exists, so limits reveal nothing about accounts."""
    digest = hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()[:32]
    return f"login-account:{client}:{digest}"
