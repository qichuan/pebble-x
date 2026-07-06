"""Shared helpers for the Peep scraper endpoints.

Leading underscore keeps Vercel from routing this file as an endpoint.
"""
import json
import os

from twikit import Client


def authorized(headers) -> bool:
    """True if the request carries the correct bearer token."""
    expected = os.environ.get("APP_TOKEN")
    if not expected:
        return False
    auth = headers.get("Authorization", "")
    return auth == "Bearer " + expected


def make_client() -> Client:
    """Build a twikit client from the cookie blob stored in the env.

    Stateless: every serverless invocation rebuilds the client from
    X_COOKIES (JSON produced by login.py). No login happens here.
    """
    cookies_raw = os.environ.get("X_COOKIES")
    if not cookies_raw:
        raise RuntimeError("X_COOKIES env var is not set")
    client = Client("en-US")
    client.set_cookies(json.loads(cookies_raw))
    return client


def tweet_to_dict(t) -> dict:
    """Flatten a twikit Tweet into the small shape the watch needs."""
    media = getattr(t, "media", None) or []
    return {
        "id": t.id,
        "name": getattr(t.user, "name", "") or "",
        "handle": getattr(t.user, "screen_name", "") or "",
        "text": t.text or "",
        "created_at": getattr(t, "created_at", "") or "",
        "favorited": bool(getattr(t, "favorited", False)),
        "has_media": len(media) > 0,
    }


def send_json(handler, status: int, obj: dict) -> None:
    body = json.dumps(obj).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)
