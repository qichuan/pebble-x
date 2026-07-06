# Peep — an X (Twitter) client for Pebble

Read your X timeline (Following and For You) and like tweets from your wrist.

The official X API is now pay-per-use and too expensive for a hobby app, so Peep
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
 detail + like                                                          /api/like
```

- **Watch**: a timeline list with a **feed toggle** at the top (Following ⇄ For
  You) and a section header showing the current feed. SELECT opens a tweet;
  SELECT again likes it. Long-press SELECT refreshes.
- **pkjs** (inside the Pebble phone app): calls your server, caches each feed so
  reopening the app is instant/free, and bridges to the watch over AppMessage.
- **Server** (`server/`): fetches the timeline and posts likes with twikit.
- **Settings page** (`docs/`): where you enter the server URL and access token.

Tweets are text-only for now; a tweet with photos shows a `[photo]` marker.

## REST API

The server is a FastAPI app (`server/api/index.py`). The watch reaches it over the
internet; `/api/timeline` and `/api/like` require `Authorization: Bearer <APP_TOKEN>`.

| Method | Path                                  | Body            | Response               |
|--------|---------------------------------------|-----------------|------------------------|
| GET    | `/api/health`                         | —               | `{ ok: true }`         |
| GET    | `/api/timeline?feed=following\|foryou` | —               | `{ feed, tweets: [] }` |
| POST   | `/api/like`                           | `{ tweet_id }`  | `{ ok: true }`         |

Each `tweets[]` item: `{ id, name, handle, text, created_at, favorited, has_media }`
(max 15 per response). Errors: `401` (bad/missing token), `422` (bad body),
`502` (twikit/upstream failure, with `detail`).

## Setup

### 1. Stand up the server

Follow [`server/README.md`](server/README.md): run `python login.py` once on your
own machine to mint an X session cookie and an access token, deploy to Vercel, and
set the `X_COOKIES` + `APP_TOKEN` environment variables. You'll get a URL like
`https://peep-xyz.vercel.app`.

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

Open the Pebble phone app → Peep → Settings. Enter your **server URL** and the
**access token** (`APP_TOKEN`), then Save. The watch loads your timeline.

## Watch controls

| Screen   | Button            | Action                          |
|----------|-------------------|---------------------------------|
| Timeline | UP / DOWN         | scroll                          |
| Timeline | SELECT on top row | switch feed (Following ⇄ For You)|
| Timeline | SELECT on a tweet | open it                         |
| Timeline | long-press SELECT | refresh                         |
| Detail   | UP / DOWN         | scroll text                     |
| Detail   | SELECT            | like                            |

## Limitations

- Tweets longer than ~437 UTF-8 bytes are truncated on the watch.
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
