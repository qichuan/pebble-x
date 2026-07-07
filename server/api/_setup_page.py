"""HTML for the setup wizard, served by the FastAPI app at / and /setup.

Leading underscore keeps Vercel from routing this file as an endpoint.

Same-origin with /api/config, so no CORS. The access token is never shown
anywhere: the server mints it on the claiming save, this page keeps it in
localStorage for authorizing later saves, and the watch receives it via the
6-digit pairing code. Recovery (new browser / cleared storage) is deleting the
tweetfit:app_token key in Redis and re-claiming.
"""

SETUP_HTML = r"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>TweetFit Setup</title>
<style>
  :root { color-scheme: light dark; }
  * { box-sizing: border-box; }
  body {
    font-family: -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    background: #f5f5f5; color: #000;
    margin: 0; padding: 24px 16px; max-width: 560px; margin-inline: auto;
  }
  @media (prefers-color-scheme: dark) {
    body { background: #121212; color: #eee; }
    input, textarea { background: #1e1e1e; color: #eee; border-color: #444 !important; }
    .sub { color: #eee !important; }
    .card, .card label, .card .hint, .card ol { color: #000 !important; }
    .card code { background: #eee; color: #000; }
  }
  h1 { font-size: 24px; margin: 0 0 4px; }
  .sub { color: #000; margin: 0 0 24px; }
  .card { background: #fff; border-radius: 12px; padding: 16px; box-shadow: 0 1px 3px rgba(0,0,0,.12); margin-bottom: 16px; }
  ol { margin: 0 0 8px; padding-left: 20px; font-size: 14px; line-height: 1.6; }
  code { background: #eee; border-radius: 4px; padding: 1px 5px; font-size: 13px; }
  label { display: block; font-weight: 600; font-size: 14px; margin-bottom: 6px; color: #000; }
  input, textarea {
    width: 100%; padding: 10px 12px; font-size: 15px;
    border: 1px solid #ccc; border-radius: 8px; font-family: inherit;
  }
  textarea { height: 110px; font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 12px; resize: vertical; }
  .hint { font-size: 12px; color: #000; margin: 6px 0 16px; }
  button {
    width: 100%; padding: 12px; font-size: 16px; font-weight: 600;
    border: 0; border-radius: 8px; cursor: pointer; margin-top: 8px;
  }
  .primary { background: #1d9bf0; color: #fff; }
  .primary:disabled { opacity: .5; cursor: default; }
  #found { font-size: 13px; margin: 6px 0 4px; min-height: 18px; }
  #msg { margin-top: 16px; font-size: 14px; text-align: center; min-height: 20px; }
  .paircode {
    color: #000; font-family: ui-monospace, Menlo, Consolas, monospace;
    font-size: 32px; font-weight: 700; letter-spacing: 4px; text-align: center;
    padding: 10px 0 4px;
  }
  .ok { color: #15803d; }
  .warn { color: #b45309; }
  .err { color: #b91c1c; }
</style>
</head>
<body>
  <h1>TweetFit Setup</h1>
  <p class="sub">Connect your X account to this server &mdash; no passwords, just your
    browser session.</p>

  <div class="card">
    <ol>
      <li>On a <b>computer</b>, log in at <a href="https://x.com" target="_blank" rel="noopener">x.com</a>.</li>
      <li>Open DevTools (<code>F12</code> or <code>Cmd&#8288;+&#8288;Opt&#8288;+&#8288;I</code>)
          &rarr; <b>Network</b> tab &rarr; reload the page.</li>
      <li>Type <code>home</code> in the filter box, right-click the <code>home</code>
          request (domain <b>x.com</b>) &rarr; <b>Copy</b> &rarr; <b>Copy as cURL</b>.</li>
      <li>Paste it all below. Only the two session cookies are extracted and sent
          &mdash; to <b>this server</b>, nowhere else. Any request to <b>x.com</b> or
          <b>api.x.com</b> works; requests to <b>twimg.com</b> (images/CDN) don't
          carry the cookies.</li>
    </ol>
  </div>

  <div class="card">
    <p class="hint" id="state_note">Checking&hellip;</p>

    <label for="paste">Copied from x.com</label>
    <textarea id="paste" placeholder="Paste the whole 'Copy as cURL' text here"
              autocomplete="off" autocapitalize="off" autocorrect="off" spellcheck="false"></textarea>
    <p id="found"></p>

    <button id="save" class="primary" disabled>Save to server</button>
  </div>

  <div class="card">
    <label>Pair your watch</label>
    <div class="paircode" id="pair_code" style="display:none"></div>
    <p class="hint" id="pair_hint">On your phone: Pebble app &rarr; <b>TweetFit</b> &rarr;
      Settings &rarr; enter this server's URL and a pairing code.</p>
    <button class="primary" id="pair_btn" disabled>Get pairing code</button>
  </div>
  <div id="msg"></div>

<script>
(function () {
  var AUTH_RE = /\bauth_token=([0-9A-Fa-f]{20,80})\b/;
  var CT0_RE = /\bct0=([0-9A-Fa-f]{16,200})\b/;
  var HEX_RE = /\b[0-9A-Fa-f]{16,200}\b/g;
  var cookies = null;
  var claimed = null;       // null until /api/config/status answers
  var tokenSource = null;   // "redis" | "env" | null (from status)
  var savedToken = null;    // localStorage only — never rendered anywhere
  var pairTimer = null;
  try { savedToken = localStorage.getItem('tweetfit_token'); } catch (e) {}

  var NOTE_NEW = 'This server is brand new — saving will claim it and generate its ' +
    'access secret automatically.';
  var NOTE_ENV = 'This server is managed via the APP_TOKEN env var — delete that ' +
    'env var in Vercel and redeploy to manage it from this page. (Your watch keeps ' +
    'working meanwhile.)';
  var NOTE_RESET = 'This server was set up from another browser. To manage it here: ' +
    'delete the tweetfit:app_token key in your Redis store (Vercel → Storage), ' +
    'reload this page, save your cookies again, and re-pair your watch.';

  function mask(v) {
    return v.slice(0, 6) + '… (' + v.length + ' chars)';
  }

  // Labeled pairs work for cURL, raw Cookie headers, and "name=value"
  // fragments alike. Bare hex values are classified by length: X's
  // auth_token is 40 hex chars; ct0 is 160 (modern) or 32 (legacy).
  function parsePaste(text) {
    var auth = AUTH_RE.exec(text);
    var ct0 = CT0_RE.exec(text);
    if (auth && ct0) return { auth_token: auth[1], ct0: ct0[1] };
    var hexes = text.match(HEX_RE) || [];
    var bareAuth = auth ? auth[1] : null;
    var bareCt0 = ct0 ? ct0[1] : null;
    var longest = null;
    for (var i = 0; i < hexes.length; i++) {
      if (!bareAuth && hexes[i].length === 40) { bareAuth = hexes[i]; continue; }
      if (hexes[i].length !== 40 && (!longest || hexes[i].length > longest.length)) {
        longest = hexes[i];
      }
    }
    if (!bareCt0) bareCt0 = longest;
    if (bareAuth && bareCt0) return { auth_token: bareAuth, ct0: bareCt0 };
    return null;
  }

  function setMsg(text, cls) {
    var el = document.getElementById('msg');
    el.textContent = text; el.className = cls || '';
  }

  function show(id, on) {
    document.getElementById(id).style.display = on ? '' : 'none';
  }

  // Saving is possible when the server is unclaimed (the save claims it) or
  // this browser holds the token from a previous claim.
  function canAct() {
    return claimed === false || !!savedToken;
  }

  function updateUI() {
    var note = document.getElementById('state_note');
    if (claimed === null) {
      note.textContent = 'Checking…';
    } else if (claimed === false) {
      note.textContent = NOTE_NEW;
    } else if (savedToken) {
      note.textContent = '';
    } else if (tokenSource === 'env') {
      note.textContent = NOTE_ENV;
    } else {
      note.textContent = NOTE_RESET;
    }
    note.style.display = note.textContent ? '' : 'none';
    document.getElementById('save').disabled = !cookies || !canAct();
    document.getElementById('pair_btn').disabled = !savedToken;
  }

  function dropToken(message) {
    try { localStorage.removeItem('tweetfit_token'); } catch (e) {}
    savedToken = null;
    updateUI();
    setMsg(message, 'err');
  }

  function refresh() {
    var found = document.getElementById('found');
    var text = document.getElementById('paste').value;
    cookies = text.trim() ? parsePaste(text) : null;
    if (cookies) {
      found.className = 'ok';
      found.textContent = '✓ auth_token: ' + mask(cookies.auth_token) +
        ' · ct0: ' + mask(cookies.ct0);
    } else {
      found.className = text.trim() ? 'warn' : '';
      found.textContent = !text.trim() ? '' :
        (/\btwimg\.com\b/.test(text) ?
          "That request went to X's CDN (twimg.com) — copy one going to x.com instead." :
          'No cookies found yet — make sure the paste includes auth_token and ct0.');
    }
    updateUI();
  }

  var PAIR_HINT_IDLE = 'On your phone: Pebble app → TweetFit → Settings → ' +
    "enter this server's URL and a pairing code.";

  function showPair(code, ttlSeconds) {
    var codeEl = document.getElementById('pair_code');
    var hintEl = document.getElementById('pair_hint');
    show('pair_code', true);
    codeEl.textContent = code.slice(0, 3) + ' ' + code.slice(3);
    var end = Date.now() + ttlSeconds * 1000;
    if (pairTimer) clearInterval(pairTimer);
    function tick() {
      var left = Math.max(0, Math.round((end - Date.now()) / 1000));
      var m = Math.floor(left / 60), s = left % 60;
      hintEl.textContent = 'On your phone: Pebble app → TweetFit → Settings → ' +
        "enter this server's URL and this code. Expires in " +
        m + ':' + (s < 10 ? '0' : '') + s + '.';
      if (!left) {
        clearInterval(pairTimer);
        pairTimer = null;
        show('pair_code', false);
        hintEl.textContent = PAIR_HINT_IDLE;
      }
    }
    tick();
    pairTimer = setInterval(tick, 1000);
  }

  fetch('/api/config/status').then(function (r) { return r.json(); }).then(function (s) {
    claimed = !!s.claimed;
    tokenSource = s.token_source || null;
    updateUI();
  }).catch(function () {
    claimed = true;  // fail closed
    updateUI();
  });

  document.getElementById('paste').addEventListener('input', refresh);

  document.getElementById('pair_btn').addEventListener('click', function () {
    if (!savedToken) return;
    fetch('/api/pair/new', {
      method: 'POST',
      headers: { 'Authorization': 'Bearer ' + savedToken }
    }).then(function (resp) {
      return resp.json().then(function (body) { return { resp: resp, body: body }; });
    }).then(function (r) {
      if (r.resp.status === 401) {
        dropToken(tokenSource === 'env' ? NOTE_ENV : NOTE_RESET);
      } else if (!r.resp.ok) {
        setMsg(r.body.detail || ('Pairing failed: ' + r.resp.status), 'err');
      } else {
        setMsg('');
        showPair(r.body.pair_code, r.body.pair_expires_s || 600);
      }
    }).catch(function (e) {
      setMsg('Network error: ' + e, 'err');
    });
  });

  document.getElementById('save').addEventListener('click', function () {
    if (!cookies || !canAct()) return;
    setMsg('Saving…');
    var headers = { 'Content-Type': 'application/json' };
    if (savedToken) headers['Authorization'] = 'Bearer ' + savedToken;
    fetch('/api/config', {
      method: 'POST',
      headers: headers,
      body: JSON.stringify(cookies)
    }).then(function (resp) {
      return resp.json().then(function (body) { return { resp: resp, body: body }; });
    }).then(function (r) {
      if (r.resp.status === 401) {
        dropToken(tokenSource === 'env' ? NOTE_ENV : NOTE_RESET);
      } else if (r.resp.status === 503) {
        setMsg(r.body.detail || 'No cookie storage configured on the server.', 'err');
      } else if (!r.resp.ok) {
        setMsg('Save failed: ' + (r.body.detail ?
          JSON.stringify(r.body.detail) : r.resp.status), 'err');
      } else {
        if (r.body.app_token) {
          savedToken = r.body.app_token;  // kept invisible; used for later saves
          try { localStorage.setItem('tweetfit_token', savedToken); } catch (e) {}
          tokenSource = 'redis';
        }
        claimed = true;
        updateUI();
        showPair(r.body.pair_code, r.body.pair_expires_s || 600);
        if (r.body.verified) {
          setMsg('✓ Connected as @' + r.body.screen_name +
            ' — now pair your watch below.', 'ok');
        } else {
          setMsg('Saved, but could not verify with X (' + (r.body.detail || 'unknown') +
            '). Pair your watch and try anyway.', 'warn');
        }
      }
    }).catch(function (e) {
      setMsg('Network error: ' + e, 'err');
    });
  });
})();
</script>
</body>
</html>
"""
