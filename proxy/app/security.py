import hashlib
import hmac
import os
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable

from fastapi import HTTPException, Request, status


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


replay_window = ReplayWindow(ttl_seconds=300)


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
