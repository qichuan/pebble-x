"""Shared helpers for the TweetFit scraper API.

Leading underscore keeps Vercel from routing this file as an endpoint.
"""
import base64
import io
import json
import os
import re
import urllib.parse
import urllib.request

from PIL import Image, ImageOps
from twikit import Client

import _twikit_patch  # noqa: F401  applies the ondemand.s login fix on import
import _storage

MAX_MEDIA_DOWNLOAD_BYTES = 5 * 1024 * 1024
MAX_RENDERED_MEDIA_BYTES = 28 * 1024
MIN_RENDERED_DIMENSION = 64
ALLOWED_MEDIA_HOST_SUFFIXES = ("twimg.com",)


def load_cookies() -> tuple[dict, str]:
    """Return (cookies, source) — Upstash Redis first, X_COOKIES env fallback.

    No caching across invocations: warm instances must not keep serving stale
    cookies after the user re-pastes fresh ones via /setup. If Redis is
    configured but unreachable, the error propagates (falling back to a
    possibly-stale env var would mask it).
    """
    raw = _storage.kv_get(_storage.COOKIES_KEY)
    if raw:
        return json.loads(raw), "redis"
    raw = os.environ.get("X_COOKIES")
    if raw:
        return json.loads(raw), "env"
    raise RuntimeError(
        "X session not configured — open your server's URL in a browser "
        "and paste your x.com cookies into the setup page"
    )


def make_client_with(cookies: dict) -> Client:
    client = Client("en-US")
    client.set_cookies(cookies)
    return client


def make_client() -> Client:
    """Build a twikit client from the stored cookie blob.

    Stateless: every serverless invocation rebuilds the client from Upstash
    Redis (written by the /setup wizard) or the X_COOKIES env var. No login
    happens here.
    """
    cookies, _ = load_cookies()
    return make_client_with(cookies)


def _value(obj, key):
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _is_allowed_media_url(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower()
    return (
        parsed.scheme in ("https", "http")
        and bool(host)
        and any(
            host == suffix or host.endswith("." + suffix)
            for suffix in ALLOWED_MEDIA_HOST_SUFFIXES
        )
    )


def _first_http_url(value):
    if (
        isinstance(value, str)
        and value.startswith(("https://", "http://"))
        and _is_allowed_media_url(value)
    ):
        return value
    if isinstance(value, (list, tuple)):
        for item in value:
            found = _first_http_url(item)
            if found:
                return found
    if isinstance(value, dict):
        for key in (
            "media_url_https",
            "media_url",
            "url",
            "preview_image_url",
            "thumbnail_url",
            "image_url",
        ):
            found = _first_http_url(value.get(key))
            if found:
                return found
    return None


def media_url(media_item) -> str:
    """Best-effort extraction of a directly downloadable image URL."""
    direct = _first_http_url(media_item)
    if direct:
        return direct
    for key in (
        "media_url_https",
        "media_url",
        "url",
        "preview_image_url",
        "thumbnail_url",
        "image_url",
    ):
        direct = _first_http_url(_value(media_item, key))
        if direct:
            return direct
    return ""


def first_media_url(tweet) -> str:
    urls = media_urls(tweet)
    return urls[0] if urls else ""


def media_urls(tweet) -> list[str]:
    media = getattr(tweet, "media", None) or []
    urls = []
    seen = set()
    for item in media:
        direct = media_url(item)
        if direct and direct not in seen:
            urls.append(direct)
            seen.add(direct)
    return urls


# X appends a t.co shortlink for attached media / quoted tweets to the end of
# the tweet body; strip trailing ones so the watch doesn't show a dead URL
# (links embedded mid-text are left alone).
_TRAILING_TCO = re.compile(r"(?:\s*https?://t\.co/\w+)+\s*$")


def strip_trailing_tco(text: str) -> str:
    return _TRAILING_TCO.sub("", text or "").rstrip()


def tweet_to_dict(t) -> dict:
    """Flatten a twikit Tweet into the small shape the watch needs."""
    urls = media_urls(t)
    url = urls[0] if urls else ""
    return {
        "id": t.id,
        "name": getattr(t.user, "name", "") or "",
        "handle": getattr(t.user, "screen_name", "") or "",
        "text": strip_trailing_tco(t.text),
        "created_at": getattr(t, "created_at", "") or "",
        "favorited": bool(getattr(t, "favorited", False)),
        "has_media": bool(url),
        "media_url": url,
        "media_urls": urls,
        "reply_count": int(getattr(t, "reply_count", 0) or 0),
    }


def _validate_media_url(url: str) -> str:
    if not _is_allowed_media_url(url):
        raise ValueError("unsupported media host")
    return url


def _fetch_media(url: str) -> bytes:
    req = urllib.request.Request(
        _validate_media_url(url),
        headers={"User-Agent": "TweetFit Pebble image proxy/1.0"},
    )
    with urllib.request.urlopen(req, timeout=12) as resp:
        data = resp.read(MAX_MEDIA_DOWNLOAD_BYTES + 1)
    if len(data) > MAX_MEDIA_DOWNLOAD_BYTES:
        raise ValueError("media file is too large")
    return data


def _pebble_palette_image() -> Image.Image:
    levels = (0, 85, 170, 255)
    palette = []
    for r in levels:
        for g in levels:
            for b in levels:
                palette.extend((r, g, b))
    palette.extend((0, 0, 0) * (256 - 64))
    pal = Image.new("P", (1, 1))
    pal.putpalette(palette)
    return pal


def _fit_image(source: Image.Image, max_width: int, max_height: int) -> Image.Image:
    width = max(1, min(int(max_width), 240))
    height = max(1, min(int(max_height), 240))
    image = ImageOps.exif_transpose(source)
    image.thumbnail((width, height), Image.Resampling.LANCZOS)
    if image.mode in ("RGBA", "LA") or "transparency" in image.info:
        background = Image.new("RGBA", image.size, (255, 255, 255, 255))
        background.alpha_composite(image.convert("RGBA"))
        image = background.convert("RGB")
    else:
        image = image.convert("RGB")
    return image


def _encode_watch_png(image: Image.Image, color: bool) -> bytes:
    if color:
        output = image.quantize(
            palette=_pebble_palette_image(),
            dither=Image.Dither.FLOYDSTEINBERG,
        )
    else:
        output = ImageOps.grayscale(image).convert(
            "1",
            dither=Image.Dither.FLOYDSTEINBERG,
        )

    buf = io.BytesIO()
    output.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _decoded_bitmap_bytes(width: int, height: int, color: bool) -> int:
    if color:
        return width * height
    row_bytes = ((width + 31) // 32) * 4
    return row_bytes * height


def _heap_reserve_bytes(color: bool) -> int:
    return 8 * 1024 if color else 2 * 1024


def _rendered_media_byte_limit(width: int, height: int, color: bool, heap_bytes: int) -> int:
    if heap_bytes > 0:
        decoded_bytes = _decoded_bitmap_bytes(width, height, color)
        reserve = _heap_reserve_bytes(color)
        return max(
            2 * 1024,
            min(MAX_RENDERED_MEDIA_BYTES, heap_bytes - decoded_bytes - reserve),
        )
    if not color:
        return 6 * 1024
    if width >= 200 or height > 200:
        return MAX_RENDERED_MEDIA_BYTES
    if width * height >= 32000:
        return 10 * 1024
    return 14 * 1024


def render_media_for_watch(
    media_url_value: str,
    width: int,
    height: int,
    color: bool,
    heap_bytes: int = 0,
) -> dict:
    """Download and render media into a small PNG the watch can decode."""
    original = Image.open(io.BytesIO(_fetch_media(media_url_value)))
    max_width = max(1, min(int(width), 240))
    max_height = max(1, min(int(height), 240))

    while True:
        fitted = _fit_image(original.copy(), max_width, max_height)
        png = _encode_watch_png(fitted, color)
        heap = int(heap_bytes or 0)
        byte_limit = _rendered_media_byte_limit(fitted.width, fitted.height, color, heap)
        fits_heap = (
            heap <= 0
            or _decoded_bitmap_bytes(fitted.width, fitted.height, color)
            + len(png)
            + _heap_reserve_bytes(color)
            <= heap
        )
        if (
            (len(png) <= byte_limit and fits_heap)
            or max_width <= MIN_RENDERED_DIMENSION
            or max_height <= MIN_RENDERED_DIMENSION
        ):
            return {
                "width": fitted.width,
                "height": fitted.height,
                "byte_count": len(png),
                "image_base64": base64.b64encode(png).decode("ascii"),
            }
        max_width = max(MIN_RENDERED_DIMENSION, int(max_width * 0.9))
        max_height = max(MIN_RENDERED_DIMENSION, int(max_height * 0.9))
