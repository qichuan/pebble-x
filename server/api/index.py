"""Peep scraper REST API (FastAPI).

The Pebble app reaches these endpoints over the internet:
    GET  /api/timeline?feed=following|foryou   -> { feed, tweets: [...] }
    POST /api/like   {tweet_id}                 -> { ok: true }
    POST /api/retweet {tweet_id}                -> { ok: true }
    GET  /api/health                            -> { ok: true }   (no auth)

Both data endpoints require `Authorization: Bearer <APP_TOKEN>`.

Deployed on Vercel as an ASGI app (see vercel.json rewrites). For local dev:
    uvicorn api.index:app --port 9099
"""
import os
import sys

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel

sys.path.insert(0, os.path.dirname(__file__))
from _common import media_urls, make_client, render_media_for_watch, tweet_to_dict

MAX_TWEETS = 15

app = FastAPI(title="Peep", docs_url=None, redoc_url=None)


def require_token(authorization: str = Header(default="")) -> None:
    expected = os.environ.get("APP_TOKEN")
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


@app.get("/api/health")
async def health() -> dict:
    return {"ok": True}


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
