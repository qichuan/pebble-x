# TweetFit — an X (Twitter) client for Pebble

Read your X timeline (Following and For You) and like tweets from your wrist.

The official X API is now pay-per-use and too expensive for a hobby app, so TweetFit
instead talks to a small **self-hosted scraper server** that uses
[twikit](https://github.com/d60/twikit) (X's internal API — no API key, no fees).
The watch and the Pebble phone app never touch X directly; they only call your
server over authenticated HTTPS. Login is a one-time local script, not an app.

```
watch/            Pebble app (C watchapp + PebbleKit JS)
server/           FastAPI service (on Vercel) that scrapes X via twikit
docs/index.html   Settings page (GitHub Pages) — enter server URL + token
```

## How it works

```
Watch (C)  ──AppMessage──▶  Pebble phone app (pkjs)  ──HTTPS+Bearer──▶  your server  ──twikit──▶  X
 timeline list                fetch + cache                              /api/timeline
 detail actions                                                         /api/like, /api/retweet
```

- **Watch**: a timeline list with a **status row** at the top showing the
  current feed (Following / For You), with an animated indicator while a
  refresh is in flight. SELECT on the status row opens a feed action menu
  (switch feed, refresh); SELECT on a tweet opens it, and SELECT in the detail
  opens an action menu for retweet, like, and images. Long-press SELECT
  refreshes from the timeline.
- **pkjs** (inside the Pebble phone app): calls your server, caches each feed so
  reopening the app is instant/free, and bridges to the watch over AppMessage.
- **Server** (`server/`): fetches the timeline, posts likes, and renders photos
  into watch-sized Pebble-compatible PNGs.
- **Settings page** (`docs/`): where you enter the server URL and access token.

Tweets with photos show a `[photo]` marker. Opening the photo loads it on demand
on color watches so timeline browsing stays fast and cheap.

## REST API

The server is a FastAPI app (`server/api/index.py`). The watch reaches it over the
internet; `/api/timeline`, `/api/like`, `/api/retweet`, and `/api/media` require
`Authorization: Bearer <APP_TOKEN>`.

| Method | Path                                  | Body            | Response               |
|--------|---------------------------------------|-----------------|------------------------|
| GET    | `/api/health`                         | —               | `{ ok: true }`         |
| GET    | `/api/timeline?feed=following\|foryou` | —                 | `{ feed, tweets: [] }` |
| POST   | `/api/like`                           | `{ tweet_id }`    | `{ ok: true }`         |
| POST   | `/api/retweet`                        | `{ tweet_id }`    | `{ ok: true }`         |
| POST   | `/api/media`                          | `{ media_url?, tweet_id?, image_index?, width, height, color, heap? }` | `{ width, height, byte_count, image_base64 }` |

Each `tweets[]` item: `{ id, name, handle, text, created_at, favorited, has_media, media_url, media_urls }`
(max 15 per response). `media_url` is the first photo for compatibility; `media_urls`
contains all photo URLs. Errors: `401` (bad/missing token), `422` (bad body),
`502` (twikit/upstream failure, with `detail`).

## Setup

### 1. Stand up the server

Follow [`server/README.md`](server/README.md): run `python login.py` once on your
own machine to mint an X session cookie and an access token, deploy to Vercel, and
set the `X_COOKIES` + `APP_TOKEN` environment variables. You'll get a URL like
`https://tweetfit-xyz.vercel.app`.

### 2. Enable GitHub Pages (settings page)

Repo → Settings → Pages → Source: **main branch, `/docs` folder**. Confirm
`https://qichuan.github.io/pebble-x/` loads. (Fork? Change `CONFIG_URL` in
`watch/src/pkjs/index.js` to your Pages URL.)

### 3. Build and install the watch app

```sh
cd watch
pebble build
pebble install --phone <phone-ip>     # or --emulator basalt
```

### 4. Configure

Open the Pebble phone app → TweetFit → Settings. Enter your **server URL** and the
**access token** (`APP_TOKEN`), then Save. The watch loads your timeline.

## Watch controls

| Screen   | Button            | Action                          |
|----------|-------------------|---------------------------------|
| Timeline | UP / DOWN         | scroll                          |
| Timeline | SELECT on top row | open feed actions               |
| Timeline | SELECT on a tweet | open it                         |
| Timeline | long-press SELECT | refresh                         |
| Timeline | BACK              | exit the app                    |
| Feed actions | UP            | switch feed (Following ⇄ For You)|
| Feed actions | SELECT        | refresh                         |
| Feed actions | BACK          | close                           |
| Detail   | UP / DOWN         | scroll text                     |
| Detail   | SELECT            | open action menu                |
| Actions  | UP                | retweet                         |
| Actions  | SELECT            | like                            |
| Actions  | DOWN              | open images on color watches    |
| Photo    | UP / DOWN         | previous / next image           |
| Photo    | BACK              | return to actions               |

## Limitations

- Tweets longer than ~437 UTF-8 bytes are truncated on the watch.
- Photos are resized to the active color watch screen using the watch-reported
  dimensions (144x168, 180x180, or 200x228) and converted to Pebble's 64-color
  palette. B/W watches do not show the Images action.
- Non-Latin scripts render as boxes unless the watch firmware has the matching
  language pack (Settings → Language in the phone app).
- twikit uses X's internal API and **breaks every few weeks** when X changes it;
  fixing means `pip install -U twikit` and redeploying (see `server/README.md`).
- X may throttle requests from datacenter IPs; if the server returns errors,
  the same code runs on any host (Fly.io, a VPS, a home Cloudflare tunnel).

## Development

```sh
# Server logic (no X account needed — uses a mocked twikit client)
cd server && python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python test_local.py            # unit-style smoke test of the endpoints
python mock_server.py 9099      # live mock endpoint for the emulator

# Watch
cd watch
pebble build && pebble install --emulator basalt
pebble logs --emulator basalt   # pkjs console output
pebble emu-app-config           # open the settings page
```
