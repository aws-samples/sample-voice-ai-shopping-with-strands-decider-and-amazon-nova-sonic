"""Token buckets for per-session limits (browser messages in shop_agent, turns in turn_pipeline)."""

from __future__ import annotations

import time


class TokenBucket:
    """Allows `rate` events a second with bursts up to `burst`."""

    def __init__(self, rate: float, burst: int, clock=time.monotonic):
        self.rate, self.burst, self.clock = rate, float(burst), clock
        self.tokens, self.stamp = float(burst), clock()

    def take(self) -> bool:
        now = self.clock()
        self.tokens = min(self.burst, self.tokens + (now - self.stamp) * self.rate)
        self.stamp = now
        if self.tokens >= 1:
            self.tokens -= 1
            return True
        return False
