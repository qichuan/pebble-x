/* Peep - X (Twitter) client for Pebble.
 * All networking lives here (PebbleKit JS runs inside the Pebble phone app).
 * Auth: OAuth 2.0 Authorization Code + PKCE, public client, no backend.
 */

var CONFIG_URL = 'https://qichuan.github.io/pebble-x/';
var TOKEN_URL = 'https://api.x.com/2/oauth2/token';
var API_BASE = 'https://api.x.com/2';
var MAX_TWEETS = 15;          // also the per-fetch read cost cap (pay-per-use API)
var MAX_TEXT_BYTES = 437;     // must fit the watch-side buffer (TEXT_LEN 441)
var MAX_AUTHOR_BYTES = 23;

var STATUS_OK = 0;
var STATUS_NOT_LOGGED_IN = 1;
var STATUS_NETWORK_ERROR = 2;
var STATUS_API_ERROR = 3;
var STATUS_FETCHING = 4;

// ---- Small utilities ----

// Compact SHA-256 (pkjs has no WebCrypto). Input: ASCII string. Output: byte array.
function sha256Bytes(ascii) {
  var K = [];
  var H = [];
  (function () {
    function frac(x) { return (x - Math.floor(x)) * 0x100000000 | 0; }
    var n = 2, primes = 0;
    for (; primes < 64; n++) {
      var isPrime = true;
      for (var f = 2; f * f <= n; f++) { if (n % f === 0) { isPrime = false; break; } }
      if (!isPrime) continue;
      if (primes < 8) H[primes] = frac(Math.pow(n, 1 / 2));
      K[primes] = frac(Math.pow(n, 1 / 3));
      primes++;
    }
  })();

  function rotr(x, n) { return (x >>> n) | (x << (32 - n)); }

  var bytes = [];
  for (var i = 0; i < ascii.length; i++) bytes.push(ascii.charCodeAt(i) & 0xff);
  var bitLen = bytes.length * 8;
  bytes.push(0x80);
  while (bytes.length % 64 !== 56) bytes.push(0);
  for (var s = 56; s >= 0; s -= 8) bytes.push((bitLen / Math.pow(2, s)) & 0xff);

  var w = new Array(64);
  for (var block = 0; block < bytes.length; block += 64) {
    for (var t = 0; t < 16; t++) {
      w[t] = (bytes[block + t * 4] << 24) | (bytes[block + t * 4 + 1] << 16) |
             (bytes[block + t * 4 + 2] << 8) | bytes[block + t * 4 + 3];
    }
    for (t = 16; t < 64; t++) {
      var s0 = rotr(w[t - 15], 7) ^ rotr(w[t - 15], 18) ^ (w[t - 15] >>> 3);
      var s1 = rotr(w[t - 2], 17) ^ rotr(w[t - 2], 19) ^ (w[t - 2] >>> 10);
      w[t] = (w[t - 16] + s0 + w[t - 7] + s1) | 0;
    }
    var a = H[0], b = H[1], c = H[2], d = H[3], e = H[4], f = H[5], g = H[6], h = H[7];
    for (t = 0; t < 64; t++) {
      var S1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25);
      var ch = (e & f) ^ (~e & g);
      var temp1 = (h + S1 + ch + K[t] + w[t]) | 0;
      var S0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
      var maj = (a & b) ^ (a & c) ^ (b & c);
      var temp2 = (S0 + maj) | 0;
      h = g; g = f; f = e; e = (d + temp1) | 0;
      d = c; c = b; b = a; a = (temp1 + temp2) | 0;
    }
    H[0] = (H[0] + a) | 0; H[1] = (H[1] + b) | 0; H[2] = (H[2] + c) | 0; H[3] = (H[3] + d) | 0;
    H[4] = (H[4] + e) | 0; H[5] = (H[5] + f) | 0; H[6] = (H[6] + g) | 0; H[7] = (H[7] + h) | 0;
  }
  var out = [];
  for (i = 0; i < 8; i++) {
    out.push((H[i] >>> 24) & 0xff, (H[i] >>> 16) & 0xff, (H[i] >>> 8) & 0xff, H[i] & 0xff);
  }
  return out;
}

var B64_CHARS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_';

function base64UrlEncode(bytes) {
  var out = '';
  for (var i = 0; i < bytes.length; i += 3) {
    var b1 = bytes[i], b2 = bytes[i + 1], b3 = bytes[i + 2];
    out += B64_CHARS[b1 >> 2];
    out += B64_CHARS[((b1 & 3) << 4) | (b2 === undefined ? 0 : b2 >> 4)];
    if (b2 !== undefined) out += B64_CHARS[((b2 & 15) << 2) | (b3 === undefined ? 0 : b3 >> 6)];
    if (b3 !== undefined) out += B64_CHARS[b3 & 63];
  }
  return out;
}

function randomString(len) {
  var out = '';
  for (var i = 0; i < len; i++) out += B64_CHARS[Math.floor(Math.random() * 64)];
  return out;
}

function utf8ByteLength(ch) {
  var code = ch.charCodeAt(0);
  if (code < 0x80) return 1;
  if (code < 0x800) return 2;
  return 3; // surrogate halves count 3+3 = valid 4-byte pair total
}

function truncateUtf8(str, maxBytes) {
  var bytes = 0, i = 0;
  for (; i < str.length; i++) {
    var n = utf8ByteLength(str[i]);
    if (bytes + n > maxBytes) break;
    bytes += n;
  }
  if (i >= str.length) return str;
  // Don't split a surrogate pair
  if (i > 0 && str.charCodeAt(i - 1) >= 0xD800 && str.charCodeAt(i - 1) <= 0xDBFF) i--;
  return str.slice(0, i);
}

function encodeForm(params) {
  var parts = [];
  for (var k in params) {
    parts.push(encodeURIComponent(k) + '=' + encodeURIComponent(params[k]));
  }
  return parts.join('&');
}

function loadJSON(key) {
  try { return JSON.parse(localStorage.getItem(key)); } catch (e) { return null; }
}

function saveJSON(key, value) {
  localStorage.setItem(key, JSON.stringify(value));
}

// ---- Token management ----

function isLoggedIn() {
  return !!localStorage.getItem('access_token');
}

function clearTokens() {
  localStorage.removeItem('access_token');
  localStorage.removeItem('refresh_token');
  localStorage.removeItem('user_id');
  localStorage.removeItem('timeline_cache');
}

function tokenRequest(params, callback) {
  var xhr = new XMLHttpRequest();
  xhr.open('POST', TOKEN_URL);
  xhr.setRequestHeader('Content-Type', 'application/x-www-form-urlencoded');
  xhr.onload = function () {
    var data = null;
    try { data = JSON.parse(xhr.responseText); } catch (e) {}
    if (xhr.status >= 200 && xhr.status < 300 && data && data.access_token) {
      localStorage.setItem('access_token', data.access_token);
      // X refresh tokens are single-use: always store the new one
      if (data.refresh_token) localStorage.setItem('refresh_token', data.refresh_token);
      callback(null);
    } else {
      console.log('Token request failed: ' + xhr.status + ' ' + xhr.responseText);
      callback('token_error');
    }
  };
  xhr.onerror = function () { callback('network'); };
  xhr.send(encodeForm(params));
}

function refreshAccessToken(callback) {
  var refreshToken = localStorage.getItem('refresh_token');
  var clientId = localStorage.getItem('client_id');
  if (!refreshToken || !clientId) return callback('not_logged_in');
  tokenRequest({
    grant_type: 'refresh_token',
    refresh_token: refreshToken,
    client_id: clientId
  }, function (err) {
    if (err === 'token_error') clearTokens(); // refresh token burned/revoked: force re-login
    callback(err);
  });
}

// ---- X API ----

function apiRequest(method, path, body, callback, isRetry) {
  var token = localStorage.getItem('access_token');
  if (!token) return callback('not_logged_in');
  var xhr = new XMLHttpRequest();
  xhr.open(method, API_BASE + path);
  xhr.setRequestHeader('Authorization', 'Bearer ' + token);
  if (body) xhr.setRequestHeader('Content-Type', 'application/json');
  xhr.onload = function () {
    if (xhr.status === 401 && !isRetry) {
      return refreshAccessToken(function (err) {
        if (err) return callback('not_logged_in');
        apiRequest(method, path, body, callback, true);
      });
    }
    if (xhr.status >= 200 && xhr.status < 300) {
      var data = null;
      try { data = JSON.parse(xhr.responseText); } catch (e) {}
      callback(null, data);
    } else {
      console.log('API ' + method + ' ' + path + ' -> ' + xhr.status + ' ' + xhr.responseText);
      callback('api');
    }
  };
  xhr.onerror = function () { callback('network'); };
  xhr.send(body ? JSON.stringify(body) : null);
}

function getUserId(callback) {
  var cached = localStorage.getItem('user_id');
  if (cached) return callback(null, cached);
  apiRequest('GET', '/users/me', null, function (err, data) {
    if (err || !data || !data.data) return callback(err || 'api');
    localStorage.setItem('user_id', data.data.id);
    callback(null, data.data.id);
  });
}

function fetchTimeline(callback) {
  getUserId(function (err, userId) {
    if (err) return callback(err);
    var path = '/users/' + userId + '/timelines/reverse_chronological' +
        '?max_results=' + MAX_TWEETS +
        '&tweet.fields=created_at&expansions=author_id&user.fields=username';
    apiRequest('GET', path, null, function (err2, data) {
      if (err2) return callback(err2);
      var users = {};
      if (data && data.includes && data.includes.users) {
        data.includes.users.forEach(function (u) { users[u.id] = u.username; });
      }
      var tweets = ((data && data.data) || []).map(function (t) {
        return {
          id: t.id,
          author: users[t.author_id] || '?',
          text: t.text,
          created_at: t.created_at,
          liked: false
        };
      });
      saveJSON('timeline_cache', { fetchedAt: Date.now(), tweets: tweets });
      callback(null, tweets);
    });
  });
}

function likeTweet(index, callback) {
  var cache = loadJSON('timeline_cache');
  if (!cache || !cache.tweets[index]) return callback('api');
  getUserId(function (err, userId) {
    if (err) return callback(err);
    apiRequest('POST', '/users/' + userId + '/likes',
        { tweet_id: cache.tweets[index].id }, function (err2) {
      if (err2) return callback(err2);
      cache.tweets[index].liked = true;
      saveJSON('timeline_cache', cache);
      callback(null);
    });
  });
}

// ---- Watch messaging ----

var s_sendQueue = [];
var s_sending = false;

function enqueue(message) {
  s_sendQueue.push(message);
  if (!s_sending) sendNext();
}

function sendNext() {
  if (s_sendQueue.length === 0) { s_sending = false; return; }
  s_sending = true;
  var message = s_sendQueue[0];
  var retried = false;
  Pebble.sendAppMessage(message, function () {
    s_sendQueue.shift();
    sendNext();
  }, function () {
    if (!retried) {
      retried = true;
      setTimeout(sendNext, 250); // one retry, then drop
    } else {
      s_sendQueue.shift();
      sendNext();
    }
  });
}

function sendStatus(status) {
  enqueue({ STATUS: status });
}

function timeAgo(iso) {
  var seconds = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (seconds < 60) return 'now';
  if (seconds < 3600) return Math.floor(seconds / 60) + 'm';
  if (seconds < 86400) return Math.floor(seconds / 3600) + 'h';
  if (seconds < 604800) return Math.floor(seconds / 86400) + 'd';
  return Math.floor(seconds / 604800) + 'w';
}

function sendTimeline(tweets) {
  s_sendQueue = [];
  enqueue({ TWEET_COUNT: tweets.length, STATUS: STATUS_OK });
  tweets.forEach(function (t, i) {
    enqueue({
      TWEET_INDEX: i,
      AUTHOR: truncateUtf8(t.author, MAX_AUTHOR_BYTES),
      TEXT: truncateUtf8(t.text, MAX_TEXT_BYTES),
      TIME_AGO: timeAgo(t.created_at),
      LIKED: t.liked ? 1 : 0
    });
  });
}

function deliverTimeline(forceFetch) {
  if (!isLoggedIn()) return sendStatus(STATUS_NOT_LOGGED_IN);
  var cache = loadJSON('timeline_cache');
  if (!forceFetch && cache && cache.tweets && cache.tweets.length > 0) {
    return sendTimeline(cache.tweets);
  }
  sendStatus(STATUS_FETCHING);
  fetchTimeline(function (err, tweets) {
    if (err === 'not_logged_in') return sendStatus(STATUS_NOT_LOGGED_IN);
    if (err === 'network') return sendStatus(STATUS_NETWORK_ERROR);
    if (err) return sendStatus(STATUS_API_ERROR);
    sendTimeline(tweets);
  });
}

// ---- Pebble events ----

Pebble.addEventListener('ready', function () {
  console.log('Peep pkjs ready, logged in: ' + isLoggedIn());
});

Pebble.addEventListener('appmessage', function (e) {
  var payload = e.payload;
  if (payload.CMD !== undefined) {
    deliverTimeline(payload.CMD === 1);
  }
  if (payload.LIKE_INDEX !== undefined) {
    var index = payload.LIKE_INDEX;
    likeTweet(index, function (err) {
      // negative value signals failure for that index
      enqueue({ LIKE_RESULT: err ? -(index + 1) : index });
    });
  }
});

// ---- Configuration (login) ----

Pebble.addEventListener('showConfiguration', function () {
  var verifier = randomString(64);
  var state = randomString(16);
  localStorage.setItem('pkce_verifier', verifier);
  localStorage.setItem('oauth_state', state);
  var challenge = base64UrlEncode(sha256Bytes(verifier));
  var url = CONFIG_URL + '?' + encodeForm({
    challenge: challenge,
    state: state,
    client_id: localStorage.getItem('client_id') || '',
    logged_in: isLoggedIn() ? '1' : '0'
  });
  Pebble.openURL(url);
});

Pebble.addEventListener('webviewclosed', function (e) {
  if (!e.response) return;
  var resp;
  try { resp = JSON.parse(decodeURIComponent(e.response)); } catch (err) { return; }

  if (resp.action === 'logout') {
    clearTokens();
    sendStatus(STATUS_NOT_LOGGED_IN);
    return;
  }

  if (resp.action === 'login' && resp.code) {
    if (resp.state !== localStorage.getItem('oauth_state')) {
      console.log('OAuth state mismatch, ignoring response');
      return;
    }
    localStorage.setItem('client_id', resp.clientId);
    tokenRequest({
      grant_type: 'authorization_code',
      code: resp.code,
      client_id: resp.clientId,
      redirect_uri: CONFIG_URL,
      code_verifier: localStorage.getItem('pkce_verifier')
    }, function (err) {
      if (err) {
        sendStatus(err === 'network' ? STATUS_NETWORK_ERROR : STATUS_API_ERROR);
        return;
      }
      console.log('Login complete, fetching timeline');
      deliverTimeline(true);
    });
  }
});
