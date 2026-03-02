import hashlib
import hmac
import os
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable

from fastapi import HTTPException, Request, status


def _read_int_env(name: str, default: int, minimum: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        parsed = int(raw)
    except ValueError:
        return default
    return parsed if parsed >= minimum else minimum


class ReplayWindow:
    def __init__(self, ttl_seconds: int = 300, max_entries: int = 10000) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self._cache: OrderedDict[str, int] = OrderedDict()

    def _prune_expired(self, now: int) -> None:
        # OrderedDict keeps insertion order, so expired entries cluster at the front.
        while self._cache:
            key, ts = next(iter(self._cache.items()))
            if now - ts <= self.ttl_seconds:
                break
            self._cache.pop(key, None)

    def check_and_store(self, nonce: str) -> None:
        now = int(time.time())
        self._prune_expired(now)
        if nonce in self._cache:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Replay detected")
        if len(self._cache) >= self.max_entries:
            self._cache.popitem(last=False)
        self._cache[nonce] = now

    def clear(self) -> None:
        self._cache.clear()


class FixedWindowRateLimiter:
    def __init__(self, window_seconds: int = 60, max_principals: int = 10000) -> None:
        self.window_seconds = window_seconds
        self.max_principals = max_principals
        self._cache: OrderedDict[str, tuple[int, int]] = OrderedDict()

    def check(self, principal: str, limit: int) -> None:
        if limit <= 0:
            return
        now = int(time.time())
        window_id = now // self.window_seconds

        value = self._cache.get(principal)
        if value is None or value[0] != window_id:
            count = 1
        else:
            count = value[1] + 1

        self._cache[principal] = (window_id, count)
        self._cache.move_to_end(principal)

        if len(self._cache) > self.max_principals:
            self._cache.popitem(last=False)

        if count > limit:
            raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Rate limit exceeded")

    def clear(self) -> None:
        self._cache.clear()


replay_window = ReplayWindow(
    ttl_seconds=_read_int_env("INTERNAL_NONCE_TTL_SEC", 300, 1),
    max_entries=_read_int_env("INTERNAL_NONCE_MAX_ENTRIES", 10000, 1000),
)
rate_limiter = FixedWindowRateLimiter(
    window_seconds=_read_int_env("INTERNAL_RATE_LIMIT_WINDOW_SEC", 60, 1),
    max_principals=_read_int_env("INTERNAL_RATE_LIMIT_MAX_PRINCIPALS", 10000, 100),
)


def _verify_signature(secret: str, timestamp: str, nonce: str, body: bytes, received: str) -> bool:
    payload = f"{timestamp}.{nonce}.".encode("utf-8") + body
    expected = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, received)


async def verify_internal_request(request: Request) -> None:
    token = request.headers.get("x-internal-token", "")
    timestamp = request.headers.get("x-timestamp", "")
    nonce = request.headers.get("x-nonce", "")
    signature = request.headers.get("x-signature", "")

    expected_token = os.getenv("INTERNAL_API_TOKEN", "change-me-in-env")
    signing_secret = os.getenv("INTERNAL_SIGNING_SECRET", "change-signing-secret")

    if not hmac.compare_digest(token, expected_token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    rate_limit_per_minute = _read_int_env("INTERNAL_RATE_LIMIT_PER_MINUTE", 120, 0)
    client_host = getattr(getattr(request, "client", None), "host", "unknown")
    principal = f"{client_host}:{token[-8:]}" if token else str(client_host)
    rate_limiter.check(principal=principal, limit=rate_limit_per_minute)

    try:
        ts = int(timestamp)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid timestamp") from exc

    now = int(time.time())
    if abs(now - ts) > 300:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Timestamp expired")

    if not nonce:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing nonce")

    body = await request.body()
    if not _verify_signature(signing_secret, timestamp, nonce, body, signature):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid signature")
    replay_window.check_and_store(nonce)


Verifier = Callable[[Request], Awaitable[None]]
