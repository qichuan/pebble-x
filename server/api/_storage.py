"""Redis persistence (cookies, app token, pairing code).

Leading underscore keeps Vercel from routing this file as an endpoint.

Vercel serverless has a read-only filesystem and env vars are fixed at deploy
time, so runtime writes (the /setup wizard) need external storage. Two
transports, since different Vercel storage integrations inject different env
vars:

- Upstash-style REST (UPSTASH_REDIS_REST_* or KV_REST_API_*): plain HTTPS via
  httpx (already a twikit dependency). Preferred when available.
- Plain Redis protocol (REDIS_URL or KV_URL, e.g. Marketplace Redis Cloud):
  redis-py over TCP/TLS.

Stored values must never be logged.
"""
import os

import httpx

COOKIES_KEY = "tweetfit:x_cookies"  # exact X_COOKIES JSON shape — interchangeable
TOKEN_KEY = "tweetfit:app_token"    # watch<->server shared secret
PAIR_KEY = "tweetfit:pair"          # active pairing code (short TTL)

_TIMEOUT = 5.0


def _rest_env():
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


def _tcp_url():
    return os.environ.get("REDIS_URL") or os.environ.get("KV_URL")


def storage_configured() -> bool:
    return _rest_env() is not None or _tcp_url() is not None


def _rest_request(method: str, path: str, **kwargs):
    url, token = _rest_env()
    resp = httpx.request(
        method,
        url + path,
        headers={"Authorization": "Bearer " + token},
        timeout=_TIMEOUT,
        **kwargs,
    )
    resp.raise_for_status()
    return resp.json()


def _tcp_client():
    import redis  # lazy: only some deployments use the TCP transport

    return redis.Redis.from_url(
        _tcp_url(),
        decode_responses=True,
        socket_connect_timeout=_TIMEOUT,
        socket_timeout=_TIMEOUT,
    )


def kv_get(key: str):
    """Return the stored string, or None when storage is not configured or the
    key is absent/expired."""
    if _rest_env():
        return _rest_request("GET", f"/get/{key}").get("result")
    if _tcp_url():
        client = _tcp_client()
        try:
            return client.get(key)
        finally:
            client.close()
    return None


def kv_set(key: str, value: str, ex_seconds: int | None = None) -> None:
    if _rest_env():
        # Value goes in the POST body so it needs no URL-encoding.
        suffix = f"?EX={int(ex_seconds)}" if ex_seconds else ""
        _rest_request("POST", f"/set/{key}{suffix}", content=value)
        return
    client = _tcp_client()
    try:
        client.set(key, value, ex=ex_seconds)
    finally:
        client.close()


def kv_del(key: str) -> None:
    if _rest_env():
        _rest_request("GET", f"/del/{key}")
        return
    client = _tcp_client()
    try:
        client.delete(key)
    finally:
        client.close()
