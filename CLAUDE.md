# CLAUDE.md

Guidance for Claude Code when working in this repo.

## What this is

**TweetFit** — a Pebble watchapp that acts as an X (Twitter) client: read the
Following / For You timeline and like tweets. To avoid the paid official X API,
it talks to a **self-hosted FastAPI server** that scrapes X via `twikit`.

Three moving parts, three languages:

| Dir     | Language      | Role                                                        |
|---------|---------------|-------------------------------------------------------------|
| `watch/`| C + JS (pkjs) | Pebble watchapp UI (C) + phone-side networking bridge (JS)  |
| `server/`| Python        | FastAPI REST API on Vercel; scrapes X with twikit           |
| `docs/` | HTML          | Settings page on GitHub Pages (server URL + token entry)    |

Data flow: **watch C ⇄ (AppMessage) ⇄ pkjs ⇄ (HTTPS+Bearer) ⇄ FastAPI ⇄ twikit ⇄ X.**
The watch never sees X directly; the phone (pkjs) never talks to X directly.

## Build / run / test

```sh
# Watch — always from watch/
cd watch
pebble build                          # builds all 7 platforms
pebble install --emulator basalt      # or --phone <ip>
pebble logs --emulator basalt         # pkjs console.log output
pebble emu-button --emulator basalt click <up|down|select|back>
pebble screenshot --emulator basalt out.png
pebble kill                           # stop emulators
# After editing package.json messageKeys or capabilities, `pebble clean` first —
# the generated appinfo/message-key header is not always regenerated otherwise.

# Server — from server/ with the venv active
cd server && python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python test_local.py                  # smoke tests, mocked twikit (no X creds)
python mock_server.py 9099            # live endpoint w/ fake data for the emulator
uvicorn api.index:app --port 9099    # the real app (needs X_COOKIES env)
```

There is no automated test runner beyond `server/test_local.py`. Verify watch
changes in the emulator (screenshots + `pebble logs`), not just by building.

## Watch ⇄ phone protocol (AppMessage)

Keys live in `watch/package.json` `messageKeys` (order matters — they map to
sequential IDs) and are mirrored as `#define`s / status codes in
`watch/src/c/main.c`. Keep the two in sync.

- **watch → phone:** `CMD` (0 fetch, 1 refresh) + `FEED` (0 following, 1 for-you);
  `CMD` 2 requests the photo for `TWEET_INDEX` and `IMAGE_INDEX` using `IMAGE_W`,
  `IMAGE_H`, `IMAGE_COLOR`, `IMAGE_HEAP`, and `IMAGE_ID`; `LIKE_INDEX` likes the
  tweet at that list index; `RETWEET_INDEX` retweets it.
- **phone → watch:** `TWEET_COUNT`, then one message per tweet with `TWEET_INDEX`,
  `AUTHOR`, `TEXT`, `TIME_AGO`, `LIKED`, `HAS_MEDIA`, `MEDIA_COUNT`; `STATUS`
  (0 ok, 1 not-configured, 2 network, 3 server, 4 fetching); `LIKE_RESULT` and
  `RETWEET_RESULT` echo the index, or send `-(index + 1)` on failure. Image
  responses start with `IMAGE_TOTAL` and `IMAGE_CHUNK_COUNT`, then ordered
  `IMAGE_OFFSET` + `IMAGE_DATA` byte-array chunks, all tagged with `IMAGE_ID`;
  `IMAGE_ERROR` reports failures.

Tweet **IDs are 64-bit and never sent to the watch** — the watch refers to tweets
by list index; pkjs holds the id/media URL list (in its per-feed `localStorage`
cache) and maps index → id for likes/retweets or index → `media_urls[IMAGE_INDEX]`
for photo rendering. On B/W watch builds the C app ignores `MEDIA_COUNT`, so
`[photo]` may remain in the tweet text but the Images action is not shown.

## Key constraints & gotchas

- **Watch RAM is tiny** (aplite: 24 KB heap). The tweet store is a fixed
  `Tweet s_tweets[MAX_TWEETS]` (15). `TEXT_LEN` is 441 bytes; pkjs truncates text
  to `MAX_TEXT_BYTES` (437) on **UTF-8 boundaries** and C re-trims any split
  multibyte tail (`prv_fix_utf8_tail`). Don't send unbounded strings.
- **Timeline list layout:** row 0 is a status row showing the current feed
  (and an animated refresh indicator); SELECT on it opens the feed action
  overlay (switch feed / refresh). Tweets are rows 1..N (`prv_row_to_tweet`
  = row − 1). Current feed persists via `persist_write_int(PERSIST_FEED, …)`.
  The timeline wraps the menu layer's click config so BACK always exits the
  app — don't subscribe BACK on the timeline window without re-binding it.
- **twikit is fragile**: it uses X's private GraphQL API and breaks every few
  weeks. Server catches upstream errors and returns `502` with `detail`. Fix =
  `pip install -U twikit`, sometimes fresh cookies via the `/setup` wizard (no
  redeploy). Known breakages are patched at import time in
  `server/api/_twikit_patch.py` (login key extraction, user-payload backfill) —
  a 502 whose `detail` is a bare quoted key name (e.g. `"'urls'"`) means X
  dropped another field; add it to the backfill table there.
- **X session**: X blocks automated login behind Cloudflare, so no password is
  ever handled — the user pastes a "Copy as cURL" blob from a logged-in x.com
  DevTools tab into the server's `/setup` wizard, which extracts `auth_token` +
  `ct0` (`POST /api/config`) and stores them in Upstash Redis (key
  `tweetfit:x_cookies`, generic KV helpers in `server/api/_storage.py`). The
  cookies ride only on requests to `x.com`/`api.x.com`, never `twimg.com` CDN
  assets. Cookie read-through is `_common.load_cookies()`: Redis first,
  `X_COOKIES` env var as fallback — never cache cookies across invocations.
- **Access token**: minted by the server on the wizard's first save
  ("claiming"; `secrets.token_urlsafe`, key `tweetfit:app_token`) and handed to
  the watch via a one-time 10-minute pairing code (`tweetfit:pair`,
  `POST /api/pair`, exchanged by the settings page over CORS — its single
  secret field auto-detects: 8 chars of the code alphabet = pairing code,
  anything else = full token; `POST /api/pair/new` (Bearer) mints a code on
  demand for the wizard's "Get pairing code" button). `expected_token()` in `index.py` reads Redis
  first, `APP_TOKEN` env as legacy fallback; `/api/config/status` reports the
  provenance as `token_source` so the wizard can explain env-claimed servers.
  `GET /api/config/status` is deliberately unauthenticated but boolean/
  provenance-only.
  An unclaimed server is claimable by the first visitor — reset by deleting
  `tweetfit:app_token` in Upstash. `server/login.py` is the legacy manual path.
- **Cost discipline**: the watch never auto-polls. It shows cached tweets on
  launch and only hits the network on explicit refresh (long-press SELECT) or an
  empty cache. Preserve this.
- **Secrets**: never commit `X_COOKIES`, `APP_TOKEN`, `cookies.json`, or `.env`
  (see `.gitignore`). `server/.venv/` and `watch/build/` are ignored too.

## Config / hosting specifics

- pkjs `CONFIG_URL` and the OAuth-free settings page point at
  `https://qichuan.github.io/pebble-x/`. On a fork, change both.
- Vercel serves the single ASGI app via a catch-all rewrite in
  `server/vercel.json`; all routes live in `server/api/index.py` (including the
  browser-facing `GET /setup` wizard, `POST /api/config`, and
  `GET /api/config/status`). Files starting with `_` (`_common.py`,
  `_storage.py`, `_setup_page.py`, `_twikit_patch.py`) are helpers, not routed.
  Root-level `.py` (`login.py`, `mock_server.py`, `test_local.py`) are dev-only,
  not deployed.
- App UUID: `46056075-4bc7-4d0c-8c90-51e4bba892fd`. `capabilities: ["configurable"]`
  in `package.json` is what makes the phone app show the Settings gear — don't drop it.

## Conventions

- C: static funcs prefixed `prv_`, file-scope state prefixed `s_`.
- pkjs is plain ES5-ish (runs in the Pebble JS runtime) — no modern syntax, no npm deps.
- Commit messages describe the business change, not the file list.
