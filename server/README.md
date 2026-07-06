# Peep scraper server

A small **FastAPI** service (deployed on Vercel) that fetches your X timeline and
likes tweets using [twikit](https://github.com/d60/twikit) (X's internal API — no
API key, no fees). The Pebble app reaches it over the internet as a plain REST API.

```
server/
  api/index.py      FastAPI app — all routes:
                      GET  /api/health                            → { ok: true }   (no auth)
                      GET  /api/timeline?feed=following|foryou     → { feed, tweets: [...] }
                      POST /api/like  {tweet_id}                   → { ok: true }
  api/_common.py    twikit client + tweet mapping
  vercel.json       rewrites all requests to the ASGI app
  login.py          run locally once to mint the X session cookie (not deployed)
  requirements.txt  twikit, fastapi, uvicorn
```

The `/api/timeline` and `/api/like` endpoints require `Authorization: Bearer <APP_TOKEN>`.

Run locally: `uvicorn api.index:app --port 9099` (or `python mock_server.py 9099`
for a version backed by a fake twikit client, no X account needed).

## Setup

### 1. Mint your X session cookie (run locally)

Do this on your own machine — X trusts your home IP far more than a datacenter one.

```sh
cd server
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python login.py
```

Enter your X username / email / password. The script logs in and prints two values:

- `X_COOKIES=...`  — the session cookie blob
- `APP_TOKEN=...`  — a freshly generated shared secret

Your password is used only for that one login and is never stored.

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

Note the production URL (e.g. `https://peep-xyz.vercel.app`).

### 3. Point the watch at it

In the Pebble app → Peep → Settings, enter the production URL and the `APP_TOKEN`.

## Test

```sh
TOKEN=... URL=https://peep-xyz.vercel.app
curl "$URL/api/health"
curl -H "Authorization: Bearer $TOKEN" "$URL/api/timeline?feed=following"
curl -X POST -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" -d '{"tweet_id":"123"}' "$URL/api/like"
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
- Keep `X_COOKIES` and `APP_TOKEN` secret — anyone with them can act as your X account.
