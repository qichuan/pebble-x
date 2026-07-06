"""TweetFit scraper REST API (FastAPI).

The Pebble app reaches these endpoints over the internet:
    GET  /api/timeline?feed=following|foryou   -> { feed, tweets: [...] }
    POST /api/like   {tweet_id}                 -> { ok: true }
    POST /api/retweet {tweet_id}                -> { ok: true }
    GET  /api/health                            -> { ok: true }   (no auth)

The setup wizard (browser, not the watch) uses:
    GET  /setup                                 -> HTML wizard    (no auth)
    POST /api/config {auth_token, ct0}          -> { ok, verified, screen_name,
                                                     pair_code, claimed, app_token? }
                                                   (claims an unclaimed server; Bearer after)
    POST /api/pair/new                          -> { pair_code }  (Bearer; mint code on demand)
    POST /api/pair {code}                       -> { app_token }  (one-time exchange, no auth)
    GET  /api/config/status                     -> { claimed, storage, cookies, source } (no auth)

The data endpoints require `Authorization: Bearer <token>`. The token is minted
by the server on first setup ("claim") and stored in Upstash Redis; the
APP_TOKEN env var is the legacy fallback.

Deployed on Vercel as an ASGI app (see vercel.json rewrites). For local dev:
    uvicorn api.index:app --port 9099
"""
import json
import os
import secrets
import sys

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

sys.path.insert(0, os.path.dirname(__file__))
import _storage
from _common import (
    load_cookies,
    make_client,
    make_client_with,
    media_urls,
    render_media_for_watch,
    tweet_to_dict,
)
from _setup_page import SETUP_HTML

PAIR_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no 0/O/1/I/L lookalikes
PAIR_TTL_SECONDS = 600

MAX_TWEETS = 15

app = FastAPI(title="TweetFit", docs_url=None, redoc_url=None)

# The GitHub Pages settings page calls /api/pair cross-origin; everything
# sensitive is Bearer-protected, so open CORS exposes nothing.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def expected_token():
    """The shared secret: minted at claim time in Redis, APP_TOKEN env as the
    legacy fallback. None means the server is unclaimed."""
    return _storage.kv_get(_storage.TOKEN_KEY) or os.environ.get("APP_TOKEN")


def require_token(authorization: str = Header(default="")) -> None:
    expected = expected_token()
    if not expected or authorization != "Bearer " + expected:
        raise HTTPException(status_code=401, detail="unauthorized")


class LikeBody(BaseModel):
    tweet_id: str


class RetweetBody(BaseModel):
    tweet_id: str


class MediaBody(BaseModel):
    media_url: str = ""
    tweet_id: str = ""
    image_index: int = 0
    width: int
    height: int
    color: bool = True
    heap: int = 0


class ConfigBody(BaseModel):
    auth_token: str = Field(pattern=r"^[0-9A-Fa-f]{20,80}$")
    ct0: str = Field(pattern=r"^[0-9A-Fa-f]{16,200}$")


class PairBody(BaseModel):
    code: str


@app.get("/api/health")
async def health() -> dict:
    return {"ok": True}


@app.get("/setup")
async def setup_page() -> HTMLResponse:
    return HTMLResponse(SETUP_HTML)


@app.post("/api/config")
async def config(body: ConfigBody, authorization: str = Header(default="")) -> dict:
    if not _storage.storage_configured():
        raise HTTPException(
            status_code=503,
            detail="no cookie storage configured — connect Upstash Redis "
            "(Vercel project → Storage) and redeploy",
        )
    # First-ever save claims the server: the token is minted here, not by the
    # user. After that, saves require the Bearer token like everything else.
    expected = expected_token()
    claimed_now = expected is None
    if claimed_now:
        expected = secrets.token_urlsafe(24)
        _storage.kv_set(_storage.TOKEN_KEY, expected)
    elif authorization != "Bearer " + expected:
        raise HTTPException(status_code=401, detail="unauthorized")

    cookies = {"auth_token": body.auth_token, "ct0": body.ct0}
    _storage.kv_set(_storage.COOKIES_KEY, json.dumps(cookies))

    pair_code = _mint_pair_code()

    result = {
        "ok": True,
        "verified": False,
        "screen_name": None,
        "detail": None,
        "claimed": claimed_now,
        "pair_code": pair_code,
        "pair_expires_s": PAIR_TTL_SECONDS,
    }
    if claimed_now:
        result["app_token"] = expected
    # Store first, verify best-effort: Cloudflare can block the verify call
    # from a datacenter IP even when the cookies themselves are good.
    try:
        user = await make_client_with(cookies).user()
        result["verified"] = True
        result["screen_name"] = user.screen_name
    except Exception as e:
        result["detail"] = str(e)
    return result


def _mint_pair_code() -> str:
    code = "".join(secrets.choice(PAIR_ALPHABET) for _ in range(8))
    _storage.kv_set(_storage.PAIR_KEY, code, ex_seconds=PAIR_TTL_SECONDS)
    return code


@app.post("/api/pair/new", dependencies=[Depends(require_token)])
async def pair_new() -> dict:
    """Mint a fresh pairing code on demand — lets the wizard pair a watch
    without re-pasting cookies."""
    if not _storage.storage_configured():
        raise HTTPException(
            status_code=503,
            detail="no cookie storage configured — connect Upstash Redis "
            "(Vercel project → Storage) and redeploy",
        )
    return {"pair_code": _mint_pair_code(), "pair_expires_s": PAIR_TTL_SECONDS}


@app.post("/api/pair")
async def pair(body: PairBody) -> dict:
    code = body.code.strip().upper().replace("-", "").replace(" ", "")
    stored = _storage.kv_get(_storage.PAIR_KEY)
    if not code or not stored or not secrets.compare_digest(code, stored):
        raise HTTPException(status_code=404, detail="invalid or expired pairing code")
    _storage.kv_del(_storage.PAIR_KEY)  # single-use
    return {"app_token": expected_token()}


@app.get("/api/config/status")
async def config_status() -> dict:
    # Unauthenticated by design: the wizard needs the claimed/storage state
    # before it has a token. Booleans/provenance only — no secret material.
    try:
        _, source = load_cookies()
        cookies_ok = True
    except Exception:
        cookies_ok, source = False, None
    if _storage.kv_get(_storage.TOKEN_KEY):
        token_source = "redis"
    elif os.environ.get("APP_TOKEN"):
        token_source = "env"
    else:
        token_source = None
    return {
        "claimed": token_source is not None,
        "storage": _storage.storage_configured(),
        "cookies": cookies_ok,
        "source": source,
        "token_source": token_source,
    }


@app.get("/api/timeline", dependencies=[Depends(require_token)])
async def timeline(feed: str = "following") -> dict:
    try:
        client = make_client()
        if feed == "foryou":
            result = await client.get_timeline(count=20)
        else:
            result = await client.get_latest_timeline(count=20)
        # X mixes in pinned/promoted/thread items out of order; snowflake ids
        # encode creation time, so sort newest-first before truncating.
        ordered = sorted(result, key=lambda t: int(t.id), reverse=True)
        tweets = [tweet_to_dict(t) for t in ordered[:MAX_TWEETS]]
    except Exception as e:  # twikit breakage, blocked IP, bad cookies, etc.
        raise HTTPException(status_code=502, detail=str(e))
    return {"feed": feed, "tweets": tweets}


@app.post("/api/like", dependencies=[Depends(require_token)])
async def like(body: LikeBody) -> dict:
    try:
        client = make_client()
        await client.favorite_tweet(body.tweet_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))
    return {"ok": True}


@app.post("/api/retweet", dependencies=[Depends(require_token)])
async def retweet(body: RetweetBody) -> dict:
    try:
        client = make_client()
        await client.retweet(body.tweet_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))
    return {"ok": True}


@app.post("/api/media", dependencies=[Depends(require_token)])
async def media(body: MediaBody) -> dict:
    try:
        media_url = body.media_url
        if not media_url and body.tweet_id:
            client = make_client()
            tweet = await client.get_tweet_by_id(body.tweet_id)
            urls = media_urls(tweet)
            if 0 <= body.image_index < len(urls):
                media_url = urls[body.image_index]
        if not media_url:
            raise ValueError("no photo found")
        rendered = render_media_for_watch(media_url, body.width, body.height, body.color, body.heap)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))
    return rendered
