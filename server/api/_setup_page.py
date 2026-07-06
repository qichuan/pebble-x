"""HTML for the /setup wizard, served by the FastAPI app itself.

Leading underscore keeps Vercel from routing this file as an endpoint.

Same-origin with /api/config, so no CORS. The page contains no secrets — the
user types the APP_TOKEN into it and it is sent as a Bearer header, never
persisted anywhere in the browser.
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
    background: #f5f5f5; color: #1a1a1a;
    margin: 0; padding: 24px 16px; max-width: 560px; margin-inline: auto;
  }
  @media (prefers-color-scheme: dark) {
    body { background: #121212; color: #eee; }
    input, textarea { background: #1e1e1e; color: #eee; border-color: #444 !important; }
    .card { background: #1e1e1e; }
    .hint, .sub { color: #999; }
    label { color: #eee !important; }
    code { background: #333; }
  }
  h1 { font-size: 24px; margin: 0 0 4px; }
  .sub { color: #777; margin: 0 0 24px; }
  .card { background: #fff; border-radius: 12px; padding: 16px; box-shadow: 0 1px 3px rgba(0,0,0,.12); margin-bottom: 16px; }
  ol { margin: 0 0 8px; padding-left: 20px; font-size: 14px; line-height: 1.6; }
  code { background: #eee; border-radius: 4px; padding: 1px 5px; font-size: 13px; }
  label { display: block; font-weight: 600; font-size: 14px; margin-bottom: 6px; color: #000; }
  input, textarea {
    width: 100%; padding: 10px 12px; font-size: 15px;
    border: 1px solid #ccc; border-radius: 8px; font-family: inherit;
  }
  textarea { height: 110px; font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 12px; resize: vertical; }
  .hint { font-size: 12px; color: #777; margin: 6px 0 16px; }
  button {
    width: 100%; padding: 12px; font-size: 16px; font-weight: 600;
    border: 0; border-radius: 8px; cursor: pointer; margin-top: 8px;
  }
  .primary { background: #1d9bf0; color: #fff; }
  .primary:disabled { opacity: .5; cursor: default; }
  #found { font-size: 13px; margin: 6px 0 4px; min-height: 18px; }
  #msg { margin-top: 16px; font-size: 14px; text-align: center; min-height: 20px; }
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
      <li>Right-click any request in the list &rarr; <b>Copy</b> &rarr;
          <b>Copy as cURL</b>.</li>
      <li>Paste it all below. Only the two session cookies are extracted and sent
          &mdash; to <b>this server</b>, nowhere else.</li>
    </ol>
  </div>

  <div class="card">
    <label for="token">Access token</label>
    <input id="token" placeholder="the APP_TOKEN you set when deploying"
           autocomplete="off" autocapitalize="off" autocorrect="off" spellcheck="false">
    <p class="hint">Set as the <b>APP_TOKEN</b> env var on this server. Also used in the
      watch settings.</p>

    <label for="paste">Copied from x.com</label>
    <textarea id="paste" placeholder="Paste the whole 'Copy as cURL' text here"
              autocomplete="off" autocapitalize="off" autocorrect="off" spellcheck="false"></textarea>
    <p id="found"></p>

    <button id="save" class="primary" disabled>Save to server</button>
  </div>
  <div id="msg"></div>

<script>
(function () {
  var AUTH_RE = /\bauth_token=([0-9A-Fa-f]{20,80})\b/;
  var CT0_RE = /\bct0=([0-9A-Fa-f]{16,200})\b/;
  var HEX_RE = /\b[0-9A-Fa-f]{16,200}\b/g;
  var cookies = null;

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
      found.textContent = text.trim() ?
        'No cookies found yet — make sure the paste includes auth_token and ct0.' : '';
    }
    document.getElementById('save').disabled = !cookies;
  }

  document.getElementById('paste').addEventListener('input', refresh);

  document.getElementById('save').addEventListener('click', function () {
    var token = document.getElementById('token').value.trim();
    if (!token) { setMsg('Enter your APP_TOKEN first.', 'warn'); return; }
    if (!cookies) return;
    setMsg('Saving…');
    fetch('/api/config', {
      method: 'POST',
      headers: {
        'Authorization': 'Bearer ' + token,
        'Content-Type': 'application/json'
      },
      body: JSON.stringify(cookies)
    }).then(function (resp) {
      return resp.json().then(function (body) { return { resp: resp, body: body }; });
    }).then(function (r) {
      if (r.resp.status === 401) {
        setMsg('Wrong access token — use the APP_TOKEN set on this server.', 'err');
      } else if (r.resp.status === 503) {
        setMsg(r.body.detail || 'No cookie storage configured on the server.', 'err');
      } else if (!r.resp.ok) {
        setMsg('Save failed: ' + (r.body.detail ?
          JSON.stringify(r.body.detail) : r.resp.status), 'err');
      } else if (r.body.verified) {
        setMsg('✓ Connected as @' + r.body.screen_name +
          ' — your watch is ready.', 'ok');
      } else {
        setMsg('Saved, but could not verify with X (' + (r.body.detail || 'unknown') +
          '). Try the watch anyway.', 'warn');
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
