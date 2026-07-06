"""HTML for the /setup wizard, served by the FastAPI app itself.

Leading underscore keeps Vercel from routing this file as an endpoint.

Same-origin with /api/config, so no CORS. On an unclaimed server the first save
claims it: the server mints the access token, the wizard keeps it in this
browser's localStorage, and the watch gets it via the short pairing code — the
user never handles the long secret.
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
    font-size: 28px; font-weight: 700; letter-spacing: 3px; text-align: center;
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
    <p id="claim_note" class="hint" style="display:none">This server is brand new &mdash;
      saving will claim it and generate its access secret automatically.</p>

    <div id="token_row" style="display:none">
      <label for="token">Access token</label>
      <input id="token" placeholder="this server's access token"
             autocomplete="off" autocapitalize="off" autocorrect="off" spellcheck="false">
      <p class="hint">This server is already claimed and this browser doesn't know its
        token. Enter it, or reset by deleting the <b>tweetfit:app_token</b> key in the
        Upstash console and reloading this page.</p>
    </div>

    <label for="paste">Copied from x.com</label>
    <textarea id="paste" placeholder="Paste the whole 'Copy as cURL' text here"
              autocomplete="off" autocapitalize="off" autocorrect="off" spellcheck="false"></textarea>
    <p id="found"></p>

    <button id="save" class="primary" disabled>Save to server</button>
  </div>

  <div class="card" id="pair_card" style="display:none">
    <label>Watch pairing code</label>
    <div class="paircode" id="pair_code"></div>
    <p class="hint">On your phone: Pebble app &rarr; <b>TweetFit</b> &rarr; Settings &rarr;
      enter this server's URL and this code. Expires in <span id="pair_ttl">10:00</span>;
      re-save here for a fresh one.</p>
  </div>
  <div id="msg"></div>

<script>
(function () {
  var AUTH_RE = /\bauth_token=([0-9A-Fa-f]{20,80})\b/;
  var CT0_RE = /\bct0=([0-9A-Fa-f]{16,200})\b/;
  var HEX_RE = /\b[0-9A-Fa-f]{16,200}\b/g;
  var cookies = null;
  var claimed = null;  // null until /api/config/status answers
  var savedToken = null;
  var pairTimer = null;
  try { savedToken = localStorage.getItem('tweetfit_token'); } catch (e) {}

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

  function currentToken() {
    var typed = document.getElementById('token').value.trim();
    return typed || savedToken || '';
  }

  function updateSave() {
    var needToken = claimed === true && !currentToken();
    document.getElementById('save').disabled = !cookies || needToken;
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
    updateSave();
  }

  function showPair(code, ttlSeconds) {
    show('pair_card', true);
    document.getElementById('pair_code').textContent =
      code.slice(0, 4) + '-' + code.slice(4);
    var end = Date.now() + ttlSeconds * 1000;
    if (pairTimer) clearInterval(pairTimer);
    pairTimer = setInterval(function () {
      var left = Math.max(0, Math.round((end - Date.now()) / 1000));
      var m = Math.floor(left / 60), s = left % 60;
      document.getElementById('pair_ttl').textContent = m + ':' + (s < 10 ? '0' : '') + s;
      if (!left) { clearInterval(pairTimer); show('pair_card', false); }
    }, 1000);
  }

  fetch('/api/config/status').then(function (r) { return r.json(); }).then(function (s) {
    claimed = !!s.claimed;
    show('claim_note', !claimed);
    show('token_row', claimed && !savedToken);
    updateSave();
  }).catch(function () {
    claimed = true;  // fail closed: assume a token is needed
    show('token_row', !savedToken);
    updateSave();
  });

  document.getElementById('paste').addEventListener('input', refresh);
  document.getElementById('token').addEventListener('input', updateSave);

  document.getElementById('save').addEventListener('click', function () {
    if (!cookies) return;
    var token = currentToken();
    if (claimed === true && !token) {
      setMsg("Enter this server's access token first.", 'warn');
      return;
    }
    setMsg('Saving…');
    var headers = { 'Content-Type': 'application/json' };
    if (token) headers['Authorization'] = 'Bearer ' + token;
    fetch('/api/config', {
      method: 'POST',
      headers: headers,
      body: JSON.stringify(cookies)
    }).then(function (resp) {
      return resp.json().then(function (body) { return { resp: resp, body: body }; });
    }).then(function (r) {
      if (r.resp.status === 401) {
        try { localStorage.removeItem('tweetfit_token'); } catch (e) {}
        savedToken = null;
        show('token_row', true);
        updateSave();
        setMsg("Wrong access token — enter this server's token.", 'err');
      } else if (r.resp.status === 503) {
        setMsg(r.body.detail || 'No cookie storage configured on the server.', 'err');
      } else if (!r.resp.ok) {
        setMsg('Save failed: ' + (r.body.detail ?
          JSON.stringify(r.body.detail) : r.resp.status), 'err');
      } else {
        var keep = r.body.app_token || token;
        if (keep) {
          savedToken = keep;
          try { localStorage.setItem('tweetfit_token', keep); } catch (e) {}
        }
        claimed = true;
        show('claim_note', false);
        show('token_row', false);
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
