# TweetFit scraper server

A small **FastAPI** service (deployed on Vercel) that fetches your X timeline and
likes tweets using [twikit](https://github.com/d60/twikit) (X's internal API — no
API key, no fees). The Pebble app reaches it over the internet as a plain REST API.

```
server/
  api/index.py      FastAPI app — all routes:
                      GET  /api/health                            → { ok: true }   (no auth)
                      GET  /setup                                  → setup wizard (HTML, no auth)
                      GET  /api/timeline?feed=following|foryou     → { feed, tweets: [...] }
                      POST /api/like  {tweet_id}                   → { ok: true }
                      POST /api/retweet {tweet_id}                 → { ok: true }
                      POST /api/media {media_url?,tweet_id?,image_index?,width,height,color,heap} → watch PNG
                      POST /api/config {auth_token, ct0}           → { ok, verified, screen_name, pair_code, … }
                                                                     (claims an unclaimed server; Bearer after)
                      POST /api/pair {code}                        → { app_token }  (one-time, no auth)
                      GET  /api/config/status                      → { claimed, storage, cookies, source } (no auth)
  api/_common.py    twikit client, cookie read-through, tweet mapping, photo rendering
  api/_storage.py   Upstash Redis persistence (cookies, access token, pairing code)
  api/_setup_page.py  HTML for the /setup wizard
  vercel.json       rewrites all requests to the ASGI app
  login.py          legacy manual setup (not deployed) — prefer /setup
  requirements.txt  twikit, fastapi, uvicorn, pillow, httpx
```

The data endpoints (`/api/timeline`, `/api/like`, `/api/retweet`, `/api/media`)
require `Authorization: Bearer <token>`. The token is minted by the server the
first time the `/setup` wizard saves ("claiming" the server) and stored in
Upstash Redis; the `APP_TOKEN` env var still works as a legacy fallback.

Run locally: `uvicorn api.index:app --port 9099` (or `python mock_server.py 9099`
for a version backed by a fake twikit client, no X account needed).

## Setup

No Python, no terminal, no env vars — just a browser.

### 1. Deploy to Vercel (one click)

[![Deploy with Vercel](https://vercel.com/button)](https://vercel.com/new/clone?repository-url=https%3A%2F%2Fgithub.com%2Fqichuan%2Fpebble-x&root-directory=server&project-name=tweetfit)

Nothing to configure. (If the import screen doesn't pick up the root directory
automatically, set **Root Directory** to `server`.)

CLI alternative: `cd server && vercel --prod`.

### 2. Connect cookie storage (one-time)

In the Vercel dashboard → your project → **Storage** → **Create / Connect
Database** → **Upstash Redis** (free tier is plenty — the server stores three
tiny values). Then **redeploy once** so the injected credentials reach the
function.

### 3. Connect your X account — the /setup wizard

Open `https://<your-app>.vercel.app/setup` in a **desktop** browser and follow
the steps on the page:

1. Log in at **x.com**.
2. DevTools (F12) → **Network** tab → reload → type `home` in the filter box →
   right-click the `home` request (domain **x.com**) → **Copy → Copy as cURL**.
   Any request to `x.com`/`api.x.com` works; requests to `twimg.com`
   (images/CDN) don't carry the cookies.
3. Paste the whole thing into the wizard and hit save — it extracts the two
   session cookies (`auth_token` + `ct0`), sends them to *your* server only,
   and confirms with your @handle.

The **first save claims the server**: it generates the access token itself
(stored in Redis, remembered by that browser) and shows a short one-time
**pairing code** (valid 10 minutes) for the watch. Do this right after
deploying — an unclaimed server can be claimed by anyone who finds the URL.
To reset, delete the `tweetfit:app_token` key in the Upstash console.

X blocks automated username/password login behind Cloudflare, so the wizard
reuses the session of a browser where you're already logged in. Treat those
cookies like a password — they grant access to your X account.

When the session expires or X invalidates it, just re-open `/setup` and paste
fresh cookies — **no redeploy needed**.

### 4. Pair the watch

Pebble app → TweetFit → Settings: enter the server URL and, in the single
secret field, the **pairing code** from the wizard — the page exchanges it for
the access token behind the scenes. (Pasting a full access token into the same
field also works; the wizard can reveal it via the Show/Copy buttons.)

### Migrating from the env-var setup

If your project still has `APP_TOKEN`/`X_COOKIES` env vars from the old manual
flow, everything keeps working — the wizard will ask for your `APP_TOKEN` value
(it can't read env vars) and remember it in that browser. To switch fully to
server-managed secrets: delete both env vars in Vercel, redeploy, reload
`/setup` — the next save claims the server and mints a fresh token.

### Manual fallback (no Upstash)

The pre-wizard flow still works: run `python login.py` locally to format the
two cookies and mint a token, then set `X_COOKIES` and `APP_TOKEN` as Vercel
env vars and redeploy. The server reads Upstash first and falls back to the
env vars.

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
  `pip install -U twikit` and redeploy; occasionally fresh cookies are needed —
  re-open `/setup` and re-paste (no redeploy).
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
