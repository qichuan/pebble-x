# TweetFit scraper server

A small **FastAPI** service (deployed on Vercel) that fetches your X timeline and
likes tweets using [twikit](https://github.com/d60/twikit) (X's internal API — no
API key, no fees). The Pebble app reaches it over the internet as a plain REST API.

```
server/
  api/index.py      FastAPI app — all routes:
                      GET  /api/health                            → { ok: true }   (no auth)
                      GET  /api/timeline?feed=following|foryou     → { feed, tweets: [...] }
                      POST /api/like  {tweet_id}                   → { ok: true }
                      POST /api/retweet {tweet_id}                 → { ok: true }
                      POST /api/media {media_url?,tweet_id?,image_index?,width,height,color,heap} → watch PNG
  api/_common.py    twikit client, tweet mapping, photo rendering
  vercel.json       rewrites all requests to the ASGI app
  login.py          run locally once to mint the X session cookie (not deployed)
  requirements.txt  twikit, fastapi, uvicorn, pillow
```

The `/api/timeline`, `/api/like`, `/api/retweet`, and `/api/media` endpoints require
`Authorization: Bearer <APP_TOKEN>`.

Run locally: `uvicorn api.index:app --port 9099` (or `python mock_server.py 9099`
for a version backed by a fake twikit client, no X account needed).

## Setup

### 1. Grab your X session cookies (run locally)

X blocks automated username/password login behind Cloudflare, so instead we reuse
the session from a browser where you're already logged in to x.com.

First, copy two cookies from a logged-in x.com browser tab:

1. Open **x.com**, then DevTools (F12 / Cmd-Opt-I).
2. **Application** (Chrome) or **Storage** (Firefox) → **Cookies** → `https://x.com`.
3. Copy the *Value* of `auth_token` (long hex string) and `ct0` (CSRF token).

Then run the helper, which formats them and mints a shared secret:

```sh
cd server
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python login.py
```

It prints two values:

- `X_COOKIES=...`  — the session cookie blob (`auth_token` + `ct0`)
- `APP_TOKEN=...`  — a freshly generated shared secret

Treat `auth_token` like a password — it grants access to your X account. Nothing is
stored on disk; the values only appear in the terminal for you to copy into Vercel.

### 2. Deploy to Vercel

```sh
cd server
vercel                       # first run links/creates the project
```

In the Vercel dashboard → Project → Settings → Environment Variables, add:

| Name        | Value                              |
|-------------|------------------------------------|
| `X_COOKIES` | the JSON blob from `login.py`      |
| `APP_TOKEN` | the token from `login.py`          |

Then ship it:

```sh
vercel --prod
```

Note the production URL (e.g. `https://tweetfit-xyz.vercel.app`).

### 3. Point the watch at it

In the Pebble app → TweetFit → Settings, enter the production URL and the `APP_TOKEN`.

## Test

```sh
TOKEN=... URL=https://tweetfit-xyz.vercel.app
curl "$URL/api/health"
curl -H "Authorization: Bearer $TOKEN" "$URL/api/timeline?feed=following"
curl -X POST -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" -d '{"tweet_id":"123"}' "$URL/api/like"
curl -X POST -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" -d '{"tweet_id":"123"}' "$URL/api/retweet"
curl -X POST -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"media_url":"https://pbs.twimg.com/media/example.jpg","tweet_id":"123","image_index":0,"width":144,"height":168,"color":true,"heap":50000}' \
     "$URL/api/media"
```

For a credential-free run of the endpoints, `python test_local.py` exercises the
app with a mocked twikit client.

## Caveats

- **Datacenter IPs**: X sometimes challenges/blocks requests from cloud IPs. If the
  timeline endpoint returns `502`, that's the likely cause. The app is host-agnostic —
  the same `uvicorn api.index:app` runs on Fly.io, a small VPS, or behind a Cloudflare
  tunnel from your home network if Vercel gets blocked.
- **twikit breaks periodically** when X rotates its internal API. Fix with
  `pip install -U twikit` and redeploy; occasionally a new login (`python login.py`)
  is needed to refresh cookies.
- **Runtime patches**: `api/_twikit_patch.py` fixes twikit 2.3.3 at import time
  (imported by `_common.py` and `login.py`), so we stay on the official PyPI package.
  It currently carries two fixes:
  - *Login* — X's 2026-03-18 webpack change broke login (`Couldn't get KEY_BYTE
    indices`). If that error returns, X changed the format again — update the
    regexes in that file (see d60/twikit issues #408 / PRs #410, #411).
  - *User parsing* — X's 2026-07 payload change dropped
    `legacy.entities.description.urls` from timeline users (part of moving user
    fields into `core`/`avatar`/…), so every timeline fetch 502'd with
    `{"detail":"'urls'"}`. The patch backfills missing `legacy` keys. If the
    timeline 502s again with a bare quoted key name as the detail, add that key
    to the backfill table.
- Keep `X_COOKIES` and `APP_TOKEN` secret — anyone with them can act as your X account.
