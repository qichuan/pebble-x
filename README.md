# Peep — an X (Twitter) client for Pebble

Read your home timeline and like tweets from your wrist. No companion app: all
networking runs in PebbleKit JS inside the official Pebble phone app, and login
happens through the standard Pebble app-settings page using OAuth 2.0 + PKCE
(no backend server, no client secret).

```
watch/            Pebble app (C watchapp + PebbleKit JS)
docs/index.html   Settings/login page, served by GitHub Pages
                  → https://qichuan.github.io/pebble-x/
```

## How it works

- **Watch app**: timeline list → press SELECT to open a tweet → press SELECT
  again to like it. Long-press SELECT in the list to refresh.
- **PebbleKit JS** (runs inside the Pebble phone app): performs the OAuth PKCE
  token exchange, fetches the timeline from the X API v2, posts likes, and
  caches the last timeline so opening the app costs nothing.
- **Settings page** (GitHub Pages): where you enter your X app Client ID and
  log in with X. It doubles as the OAuth redirect target.

Because the X API is **pay-per-use** (~$0.005 per tweet read), the app never
polls. It fetches at most 15 tweets, only when the cache is empty or when you
explicitly refresh (~$0.08 per refresh).

## One-time setup

### 1. Create an X developer app

1. Sign up at [developer.x.com](https://developer.x.com) (pay-per-use plan)
   and create a project + app.
2. In the app's **User authentication settings**:
   - Type of app: **Native app** (public client — no secret needed)
   - Callback / redirect URI: `https://qichuan.github.io/pebble-x/`
   - Requested scopes must include: `tweet.read`, `users.read`, `like.write`,
     `offline.access`
3. Copy the **OAuth 2.0 Client ID** (Keys and tokens tab).

### 2. Enable GitHub Pages

Repo → Settings → Pages → Source: **main branch, `/docs` folder**. Confirm
`https://qichuan.github.io/pebble-x/` loads.

> If you fork this repo, replace the URL in `watch/src/pkjs/index.js`
> (`CONFIG_URL`) and in the redirect URI above with your own Pages URL.

### 3. Build and install the watch app

```sh
cd watch
pebble build
pebble install --phone <phone-ip>     # or --emulator basalt
```

### 4. Log in

Open the Pebble phone app → Peep → Settings. Paste your Client ID, tap
**Log in with X**, and authorize. The page returns you to the Pebble app and
the watch loads your timeline.

## Watch controls

| Screen   | Button            | Action            |
|----------|-------------------|-------------------|
| Timeline | UP / DOWN         | scroll            |
| Timeline | SELECT            | open tweet        |
| Timeline | long-press SELECT | refresh (costs $) |
| Detail   | UP / DOWN         | scroll text       |
| Detail   | SELECT            | like              |

## Limitations

- Tweets longer than ~437 UTF-8 bytes are truncated on the watch.
- Non-Latin scripts (CJK, etc.) render as boxes unless the watch firmware has
  the matching language pack installed (Settings → Language on the phone app).
- The timeline doesn't know which tweets you liked before opening the app;
  the heart marker tracks likes made from the watch.

## Development

```sh
cd watch
pebble build && pebble install --emulator basalt
pebble logs --emulator basalt                      # pkjs console output
pebble emu-app-config                              # exercise the settings page
```
