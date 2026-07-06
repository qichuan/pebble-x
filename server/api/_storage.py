"""Upstash Redis persistence (cookies, app token, pairing code).

Leading underscore keeps Vercel from routing this file as an endpoint.

Vercel serverless has a read-only filesystem and env vars are fixed at deploy
time, so runtime writes (the /setup wizard) need external storage. The Upstash
REST API is plain HTTPS, so httpx (already a twikit dependency) is enough — no
Redis client library.

Stored values must never be logged.
"""
import os

import httpx

COOKIES_KEY = "tweetfit:x_cookies"  # exact X_COOKIES JSON shape — interchangeable
TOKEN_KEY = "tweetfit:app_token"    # watch<->server shared secret
PAIR_KEY = "tweetfit:pair"          # active pairing code (short TTL)

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


def _request(method: str, path: str, **kwargs):
    env = _redis_env()
    if not env:
        raise RuntimeError("cookie storage is not configured")
    url, token = env
    resp = httpx.request(
        method,
        url + path,
        headers={"Authorization": "Bearer " + token},
        timeout=_TIMEOUT,
        **kwargs,
    )
    resp.raise_for_status()
    return resp.json()


def kv_get(key: str):
    """Return the stored string, or None when storage is not configured or the
    key is absent/expired."""
    if not storage_configured():
        return None
    return _request("GET", f"/get/{key}").get("result")


def kv_set(key: str, value: str, ex_seconds: int | None = None) -> None:
    # Value goes in the POST body so it needs no URL-encoding.
    suffix = f"?EX={int(ex_seconds)}" if ex_seconds else ""
    _request("POST", f"/set/{key}{suffix}", content=value)


def kv_del(key: str) -> None:
    _request("GET", f"/del/{key}")
