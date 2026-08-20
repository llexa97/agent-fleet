import asyncio
import time
from collections import defaultdict, deque


class LocalRateLimiter:
    """Fallback fail-closed par processus quand Redis est indisponible."""

    def __init__(self) -> None:
        self._attempts: defaultdict[str, deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def allow(self, key: str, *, limit: int, window_seconds: int = 60) -> bool:
        now = time.monotonic()
        threshold = now - window_seconds
        async with self._lock:
            values = self._attempts[key]
            while values and values[0] < threshold:
                values.popleft()
            if len(values) >= limit:
                return False
            values.append(now)
            return True


login_limiter = LocalRateLimiter()
