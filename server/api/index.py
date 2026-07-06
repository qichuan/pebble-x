"""Peep scraper REST API (FastAPI).

The Pebble app reaches these endpoints over the internet:
    GET  /api/timeline?feed=following|foryou   -> { feed, tweets: [...] }
    POST /api/like   {tweet_id}                 -> { ok: true }
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
from _common import make_client, tweet_to_dict

MAX_TWEETS = 15

app = FastAPI(title="Peep", docs_url=None, redoc_url=None)


def require_token(authorization: str = Header(default="")) -> None:
    expected = os.environ.get("APP_TOKEN")
    if not expected or authorization != "Bearer " + expected:
        raise HTTPException(status_code=401, detail="unauthorized")


class LikeBody(BaseModel):
    tweet_id: str


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
