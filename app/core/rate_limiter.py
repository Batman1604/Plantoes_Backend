import time
from collections import defaultdict
from threading import Lock

from fastapi import HTTPException, Request

from app.core.config import settings

_buckets: dict[str, list[float]] = defaultdict(list)
_lock = Lock()


def _check(key: str, max_requests: int, window_seconds: int) -> bool:
    now = time.monotonic()
    with _lock:
        ts = _buckets[key]
        cutoff = now - window_seconds
        idx = 0
        while idx < len(ts) and ts[idx] < cutoff:
            idx += 1
        del ts[:idx]
        if len(ts) >= max_requests:
            return False
        ts.append(now)
        return True


def rate_limit(max_requests: int = 10, window_seconds: int = 60):
    def dependency(request: Request) -> None:
        if settings.environment != "production":
            return
        if request.client is None:
            return
        key = f"{request.client.host}:{request.url.path}"
        if not _check(key, max_requests, window_seconds):
            raise HTTPException(
                status_code=429,
                detail="Muitas tentativas. Tente novamente em alguns instantes.",
            )
    return dependency
