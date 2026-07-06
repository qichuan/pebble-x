/* Peep - X (Twitter) client for Pebble.
 * Talks to a self-hosted scraper server (see /server) over authenticated HTTPS.
 * No official X API, no OAuth - the phone just forwards to our server.
 */

var MAX_TWEETS = 15;
var MAX_TEXT_BYTES = 437;   // must fit the watch-side buffer (TEXT_LEN 441)
var MAX_AUTHOR_BYTES = 23;
var CACHE_FRESH_MS = 10 * 60 * 1000;  // older caches are re-fetched in the background
var IMAGE_CHUNK_BYTES = 512;

var CMD_FETCH = 0;
var CMD_REFRESH = 1;
var CMD_IMAGE = 2;

var STATUS_OK = 0;
var STATUS_NOT_CONFIGURED = 1;
var STATUS_NETWORK_ERROR = 2;
var STATUS_SERVER_ERROR = 3;
var STATUS_FETCHING = 4;

var IMAGE_ERROR_MISSING = 1;
var IMAGE_ERROR_NETWORK = 2;
var IMAGE_ERROR_SERVER = 3;
var IMAGE_ERROR_DECODE = 4;

var FEED_NAMES = ['following', 'foryou'];

// ---- Small utilities ----

function utf8ByteLength(ch) {
  var code = ch.charCodeAt(0);
  if (code < 0x80) return 1;
  if (code < 0x800) return 2;
  return 3;
}

function truncateUtf8(str, maxBytes) {
  var bytes = 0, i = 0;
  for (; i < str.length; i++) {
    var n = utf8ByteLength(str[i]);
    if (bytes + n > maxBytes) break;
    bytes += n;
  }
  if (i >= str.length) return str;
  if (i > 0 && str.charCodeAt(i - 1) >= 0xD800 && str.charCodeAt(i - 1) <= 0xDBFF) i--;
  return str.slice(0, i);
}

function loadJSON(key) {
  try { return JSON.parse(localStorage.getItem(key)); } catch (e) { return null; }
}

function saveJSON(key, value) {
  localStorage.setItem(key, JSON.stringify(value));
}

function serverConfigured() {
  return !!(localStorage.getItem('server_url') && localStorage.getItem('app_token'));
}

function serverBase() {
  return (localStorage.getItem('server_url') || '').replace(/\/+$/, '');
}

function decodeBase64(input) {
  input = (input || '').replace(/[^A-Za-z0-9+/=]/g, '');
  if (typeof atob === 'function') {
    var binary = atob(input);
    var bytes = [];
    for (var i = 0; i < binary.length; i++) bytes.push(binary.charCodeAt(i) & 255);
    return bytes;
  }

  var chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/';
  var out = [];
  var buffer = 0;
  var bits = 0;
  for (var j = 0; j < input.length; j++) {
    var ch = input.charAt(j);
    if (ch === '=') break;
    var value = chars.indexOf(ch);
    if (value < 0) continue;
    buffer = (buffer << 6) | value;
    bits += 6;
    if (bits >= 8) {
      bits -= 8;
      out.push((buffer >> bits) & 255);
    }
  }
  return out;
}

// ---- Server requests ----

function serverRequest(method, path, body, callback) {
  if (!serverConfigured()) return callback('not_configured');
  var xhr = new XMLHttpRequest();
  xhr.open(method, serverBase() + path);
  xhr.setRequestHeader('Authorization', 'Bearer ' + localStorage.getItem('app_token'));
  if (body) xhr.setRequestHeader('Content-Type', 'application/json');
  xhr.timeout = 25000;
  xhr.onload = function () {
    var data = null;
    try { data = JSON.parse(xhr.responseText); } catch (e) {}
    if (xhr.status >= 200 && xhr.status < 300) {
      callback(null, data);
    } else {
      console.log(method + ' ' + path + ' -> ' + xhr.status + ' ' + xhr.responseText);
      callback('server');
    }
  };
  xhr.onerror = function () { callback('network'); };
  xhr.ontimeout = function () { callback('network'); };
  xhr.send(body ? JSON.stringify(body) : null);
}

function fetchTimeline(feed, callback) {
  serverRequest('GET', '/api/timeline?feed=' + feed, null, function (err, data) {
    if (err) return callback(err);
    var tweets = ((data && data.tweets) || []).map(function (t) {
      var mediaUrl = t.media_url || '';
      return {
        id: t.id,
        author: t.handle || t.name || '?',
        text: t.text || '',
        created_at: t.created_at,
        liked: !!t.favorited,
        has_media: !!mediaUrl,
        media_url: mediaUrl
      };
    });
    saveJSON('cache_' + feed, { fetchedAt: Date.now(), tweets: tweets });
    callback(null, tweets);
  });
}

function fetchMedia(mediaUrl, width, height, color, heap, callback) {
  serverRequest('POST', '/api/media', {
    media_url: mediaUrl,
    width: width,
    height: height,
    color: !!color,
    heap: heap || 0
  }, callback);
}

function likeTweet(feed, index, callback) {
  var cache = loadJSON('cache_' + feed);
  if (!cache || !cache.tweets[index]) return callback('server');
  serverRequest('POST', '/api/like', { tweet_id: cache.tweets[index].id }, function (err) {
    if (err) return callback(err);
    cache.tweets[index].liked = true;
    saveJSON('cache_' + feed, cache);
    callback(null);
  });
}

// ---- Watch messaging ----

var s_sendQueue = [];
var s_sending = false;
// Bumped whenever the queue is replaced wholesale (new timeline batch) so a
// callback from an in-flight send can't shift()/retry against the new queue.
var s_sendGeneration = 0;

function enqueue(message) {
  s_sendQueue.push(message);
  if (!s_sending) sendNext();
}

function sendNext() {
  if (s_sendQueue.length === 0) { s_sending = false; return; }
  s_sending = true;
  var generation = s_sendGeneration;
  var message = s_sendQueue[0];
  var retried = false;
  Pebble.sendAppMessage(message, function () {
    if (generation === s_sendGeneration) s_sendQueue.shift();
    sendNext();
  }, function () {
    if (generation !== s_sendGeneration) return sendNext();
    if (!retried) {
      retried = true;
      setTimeout(sendNext, 250);
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
  var t = new Date(iso).getTime();
  if (isNaN(t)) return '';
  var seconds = Math.floor((Date.now() - t) / 1000);
  if (seconds < 60) return 'now';
  if (seconds < 3600) return Math.floor(seconds / 60) + 'm';
  if (seconds < 86400) return Math.floor(seconds / 3600) + 'h';
  if (seconds < 604800) return Math.floor(seconds / 86400) + 'd';
  return Math.floor(seconds / 604800) + 'w';
}

function sendTimeline(tweets) {
  s_sendGeneration++;
  s_sendQueue = [];
  enqueue({ TWEET_COUNT: tweets.length, STATUS: STATUS_OK });
  tweets.forEach(function (t, i) {
    var text = t.text;
    var hasPhoto = !!(t.has_media && t.media_url);
    if (hasPhoto) text += ' [photo]';
    enqueue({
      TWEET_INDEX: i,
      AUTHOR: truncateUtf8(t.author, MAX_AUTHOR_BYTES),
      TEXT: truncateUtf8(text, MAX_TEXT_BYTES),
      TIME_AGO: timeAgo(t.created_at),
      LIKED: t.liked ? 1 : 0,
      HAS_MEDIA: hasPhoto ? 1 : 0
    });
  });
}

function sendImageError(requestId, code) {
  enqueue({ IMAGE_ID: requestId, IMAGE_ERROR: code });
}

function sendImagePayload(requestId, data) {
  var bytes = decodeBase64(data && data.image_base64);
  if (!bytes.length) return sendImageError(requestId, IMAGE_ERROR_DECODE);

  var chunkCount = Math.ceil(bytes.length / IMAGE_CHUNK_BYTES);
  enqueue({
    IMAGE_ID: requestId,
    IMAGE_TOTAL: bytes.length,
    IMAGE_CHUNK_COUNT: chunkCount,
    IMAGE_W: data.width || 0,
    IMAGE_H: data.height || 0
  });

  for (var i = 0; i < chunkCount; i++) {
    var start = i * IMAGE_CHUNK_BYTES;
    var end = Math.min(start + IMAGE_CHUNK_BYTES, bytes.length);
    var chunk = [];
    for (var j = start; j < end; j++) chunk.push(bytes[j]);
    enqueue({
      IMAGE_ID: requestId,
      IMAGE_OFFSET: start,
      IMAGE_DATA: chunk
    });
  }
}

function deliverImage(payload, feed) {
  var requestId = payload.IMAGE_ID || 0;
  var index = payload.TWEET_INDEX;
  var cache = loadJSON('cache_' + feed);
  if (!cache || !cache.tweets || !cache.tweets[index] || !cache.tweets[index].media_url) {
    return sendImageError(requestId, IMAGE_ERROR_MISSING);
  }

  fetchMedia(
    cache.tweets[index].media_url,
    payload.IMAGE_W || 144,
    payload.IMAGE_H || 168,
    payload.IMAGE_COLOR !== 0,
    payload.IMAGE_HEAP || 0,
    function (err, data) {
      if (err === 'network') return sendImageError(requestId, IMAGE_ERROR_NETWORK);
      if (err) return sendImageError(requestId, IMAGE_ERROR_SERVER);
      sendImagePayload(requestId, data);
    }
  );
}

function currentFeed(payload) {
  if (payload && payload.FEED !== undefined) {
    var name = FEED_NAMES[payload.FEED] || 'following';
    localStorage.setItem('feed', name);
    return name;
  }
  return localStorage.getItem('feed') || 'following';
}

function deliverTimeline(forceFetch, feed) {
  if (!serverConfigured()) return sendStatus(STATUS_NOT_CONFIGURED);
  var cache = loadJSON('cache_' + feed);
  if (!forceFetch && cache && cache.tweets && cache.tweets.length > 0) {
    sendTimeline(cache.tweets);
    // Stale-while-revalidate: quietly refresh an old cache. Errors stay
    // silent - the user is already looking at usable cached tweets.
    if (Date.now() - (cache.fetchedAt || 0) > CACHE_FRESH_MS) {
      fetchTimeline(feed, function (err, tweets) {
        if (!err && tweets.length > 0) sendTimeline(tweets);
      });
    }
    return;
  }
  sendStatus(STATUS_FETCHING);
  fetchTimeline(feed, function (err, tweets) {
    if (err === 'not_configured') return sendStatus(STATUS_NOT_CONFIGURED);
    if (err === 'network') return sendStatus(STATUS_NETWORK_ERROR);
    if (err) return sendStatus(STATUS_SERVER_ERROR);
    sendTimeline(tweets);
  });
}

// ---- Pebble events ----

Pebble.addEventListener('ready', function () {
  console.log('Peep pkjs ready, configured: ' + serverConfigured());
});

Pebble.addEventListener('appmessage', function (e) {
  var payload = e.payload;
  var feed = currentFeed(payload);
  if (payload.CMD !== undefined) {
    if (payload.CMD === CMD_FETCH || payload.CMD === CMD_REFRESH) {
      deliverTimeline(payload.CMD === CMD_REFRESH, feed);
    } else if (payload.CMD === CMD_IMAGE) {
      deliverImage(payload, feed);
    }
  }
  if (payload.LIKE_INDEX !== undefined) {
    var index = payload.LIKE_INDEX;
    likeTweet(feed, index, function (err) {
      enqueue({ LIKE_RESULT: err ? -(index + 1) : index });
    });
  }
});

// ---- Configuration (server URL + token) ----

Pebble.addEventListener('showConfiguration', function () {
  var url = 'https://qichuan.github.io/pebble-x/?' +
    'server_url=' + encodeURIComponent(localStorage.getItem('server_url') || '') +
    '&has_token=' + (localStorage.getItem('app_token') ? '1' : '0');
  Pebble.openURL(url);
});

Pebble.addEventListener('webviewclosed', function (e) {
  if (!e.response) return;
  var resp;
  try { resp = JSON.parse(decodeURIComponent(e.response)); } catch (err) { return; }
  if (resp.action !== 'save') return;

  localStorage.setItem('server_url', (resp.serverUrl || '').trim());
  if (resp.token) localStorage.setItem('app_token', resp.token.trim());
  // Config changed - drop caches so the next launch fetches fresh.
  localStorage.removeItem('cache_following');
  localStorage.removeItem('cache_foryou');

  if (serverConfigured()) {
    deliverTimeline(true, localStorage.getItem('feed') || 'following');
  } else {
    sendStatus(STATUS_NOT_CONFIGURED);
  }
});
