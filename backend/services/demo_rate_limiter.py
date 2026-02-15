import time
import threading


class DemoRateLimiter:
    """In-memory IP rate limiter for the demo audit endpoint."""

    def __init__(self, max_requests: int = 3, window_seconds: int = 600):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._requests: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def check(self, ip: str) -> tuple[bool, int]:
        """
        Check if a request from this IP is allowed.

        Returns:
            (allowed, retry_after_seconds)
        """
        now = time.time()
        cutoff = now - self.window_seconds

        with self._lock:
            # Prune stale entries for this IP
            if ip in self._requests:
                self._requests[ip] = [
                    t for t in self._requests[ip] if t > cutoff
                ]
            else:
                self._requests[ip] = []

            timestamps = self._requests[ip]

            if len(timestamps) >= self.max_requests:
                # Earliest request still in window — retry after it expires
                retry_after = int(timestamps[0] - cutoff) + 1
                return False, max(retry_after, 1)

            timestamps.append(now)
            return True, 0


# Singleton instance
rate_limiter = DemoRateLimiter()
