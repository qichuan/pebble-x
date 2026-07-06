"""Upstash Redis persistence for the X cookie blob.

Leading underscore keeps Vercel from routing this file as an endpoint.

Vercel serverless has a read-only filesystem and env vars are fixed at deploy
time, so runtime cookie updates (the /setup wizard) need external storage. The
Upstash REST API is plain HTTPS, so httpx (already a twikit dependency) is
enough — no Redis client library.

The stored value is the exact X_COOKIES JSON shape, so the Redis blob and the
env var are interchangeable. Cookie values must never be logged.
"""
import os

import httpx

REDIS_KEY = "tweetfit:x_cookies"
_TIMEOUT = 5.0


def _redis_env():
    """Return (rest_url, token) or None. Supports both env naming schemes the
    Vercel Upstash integration has used over time."""
    for url_var, token_var in (
        ("UPSTASH_REDIS_REST_URL", "UPSTASH_REDIS_REST_TOKEN"),
        ("KV_REST_API_URL", "KV_REST_API_TOKEN"),
    ):
        url = os.environ.get(url_var)
        token = os.environ.get(token_var)
        if url and token:
            return url.rstrip("/"), token
    return None


def storage_configured() -> bool:
    return _redis_env() is not None


def load_cookies_raw():
    """Return the stored cookie JSON string, or None when storage is not
    configured or nothing has been stored yet."""
    env = _redis_env()
    if not env:
        return None
    url, token = env
    resp = httpx.get(
        f"{url}/get/{REDIS_KEY}",
        headers={"Authorization": "Bearer " + token},
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json().get("result")


def store_cookies_raw(raw: str) -> None:
    env = _redis_env()
    if not env:
        raise RuntimeError("cookie storage is not configured")
    url, token = env
    # Value goes in the POST body so it needs no URL-encoding.
    resp = httpx.post(
        f"{url}/set/{REDIS_KEY}",
        headers={"Authorization": "Bearer " + token},
        content=raw,
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
