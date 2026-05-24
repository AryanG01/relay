"""
Simple in-process token-bucket rate limiter middleware for FastAPI.

No external Redis required — uses an asyncio-safe in-memory dict.
Suitable for single-process deployment on Oracle Cloud ARM VM.

Default limits:
  - 60 requests / 60 seconds per IP (1 req/sec sustained)
  - Burst of 30 tokens

Set RATE_LIMIT_ENABLED=false in .env to disable (useful in tests).
"""
from __future__ import annotations

import asyncio
import os
import time
from collections import defaultdict

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

# Config
_ENABLED = os.getenv("RATE_LIMIT_ENABLED", "true").lower() != "false"
_CAPACITY  = 30    # max burst tokens
_REFILL_RATE = 1.0  # tokens per second
_WINDOW_SECS = 1.0


class _Bucket:
    __slots__ = ("tokens", "last_refill")

    def __init__(self):
        self.tokens: float = _CAPACITY
        self.last_refill: float = time.monotonic()

    def consume(self) -> bool:
        """Refill based on elapsed time, then consume one token. Return True if allowed."""
        now = time.monotonic()
        elapsed = now - self.last_refill
        self.tokens = min(_CAPACITY, self.tokens + elapsed * _REFILL_RATE)
        self.last_refill = now
        if self.tokens >= 1:
            self.tokens -= 1
            return True
        return False


_buckets: dict[str, _Bucket] = defaultdict(_Bucket)
_lock = asyncio.Lock()


# Paths exempt from rate limiting (health check, static assets)
_EXEMPT_PREFIXES = ("/health", "/docs", "/openapi", "/redoc")


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        if not _ENABLED:
            return await call_next(request)

        path = request.url.path
        if any(path.startswith(p) for p in _EXEMPT_PREFIXES):
            return await call_next(request)

        # Identify client: use X-Forwarded-For behind nginx, else direct IP
        forwarded = request.headers.get("x-forwarded-for")
        client_ip = forwarded.split(",")[0].strip() if forwarded else (
            request.client.host if request.client else "unknown"
        )

        async with _lock:
            bucket = _buckets[client_ip]
            allowed = bucket.consume()

        if not allowed:
            return JSONResponse(
                status_code=429,
                content={"detail": "Too many requests — slow down"},
                headers={"Retry-After": "1"},
            )

        return await call_next(request)
