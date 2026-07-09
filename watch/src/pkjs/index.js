/* TweetFit - X (Twitter) client for Pebble.
 * Talks to a self-hosted scraper server (see /server) over authenticated HTTPS.
 * No official X API, no OAuth - the phone just forwards to our server.
 */

var MAX_TWEETS = 15;
var MAX_TEXT_BYTES = 437;   // must fit the watch-side buffer (TEXT_LEN 441)
var MAX_AUTHOR_BYTES = 23;
var MAX_NAME_BYTES = 23;    // display name, fits the watch NAME_LEN 24 buffer
var MAX_BODY_BYTES = 1800;  // full text + replies blob; must fit the watch COMMENTS buffer
var CACHE_FRESH_MS = 10 * 60 * 1000;  // older caches are re-fetched in the background
var IMAGE_CHUNK_BYTES = 512;
var COMMENTS_CHUNK_BYTES = 512;

var CMD_FETCH = 0;
var CMD_REFRESH = 1;
var CMD_IMAGE = 2;
var CMD_COMMENTS = 3;

var STATUS_OK = 0;
var STATUS_NOT_CONFIGURED = 1;
var STATUS_NETWORK_ERROR = 2;
var STATUS_SERVER_ERROR = 3;
var STATUS_FETCHING = 4;

var IMAGE_ERROR_MISSING = 1;
var IMAGE_ERROR_NETWORK = 2;
var IMAGE_ERROR_SERVER = 3;
var IMAGE_ERROR_DECODE = 4;

var COMMENTS_ERROR_MISSING = 1;
var COMMENTS_ERROR_NETWORK = 2;
var COMMENTS_ERROR_SERVER = 3;

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

// Encode a JS string to an array of UTF-8 byte values (for chunked AppMessage
// byte-array transfer, mirrored by the C side reassembling into a char buffer).
function utf8Bytes(str) {
  var out = [];
  for (var i = 0; i < str.length; i++) {
    var code = str.charCodeAt(i);
    if (code < 0x80) {
      out.push(code);
    } else if (code < 0x800) {
      out.push(0xC0 | (code >> 6), 0x80 | (code & 0x3F));
    } else if (code >= 0xD800 && code <= 0xDBFF && i + 1 < str.length) {
      var lo = str.charCodeAt(i + 1);
      var cp = 0x10000 + ((code - 0xD800) << 10) + (lo - 0xDC00);
      out.push(0xF0 | (cp >> 18), 0x80 | ((cp >> 12) & 0x3F),
               0x80 | ((cp >> 6) & 0x3F), 0x80 | (cp & 0x3F));
      i++;
    } else {
      out.push(0xE0 | (code >> 12), 0x80 | ((code >> 6) & 0x3F), 0x80 | (code & 0x3F));
    }
  }
  return out;
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

function normalizeMediaUrls(tweet) {
  var urls = [];
  var raw = tweet && tweet.media_urls;
  if (raw && typeof raw !== 'string' && raw.length !== undefined) {
    for (var i = 0; i < raw.length; i++) {
      if (raw[i]) urls.push(raw[i]);
    }
  }
  if (!urls.length && tweet && tweet.media_url) urls.push(tweet.media_url);
  return urls;
}

function fetchTimeline(feed, callback) {
  serverRequest('GET', '/api/timeline?feed=' + feed, null, function (err, data) {
    if (err) return callback(err);
    var tweets = ((data && data.tweets) || []).map(function (t) {
      var mediaUrls = normalizeMediaUrls(t);
      var mediaUrl = mediaUrls[0] || '';
      return {
        id: t.id,
        author: t.handle || t.name || '?',
        name: t.name || t.handle || '?',
        text: t.text || '',
        created_at: t.created_at,
        liked: !!t.favorited,
        has_media: mediaUrls.length > 0,
        media_url: mediaUrl,
        media_urls: mediaUrls,
        reply_count: t.reply_count || 0
      };
    });
    saveJSON('cache_' + feed, { fetchedAt: Date.now(), tweets: tweets });
    callback(null, tweets);
  });
}

function fetchMedia(mediaUrl, tweetId, imageIndex, width, height, color, heap, callback) {
  serverRequest('POST', '/api/media', {
    media_url: mediaUrl || '',
    tweet_id: tweetId || '',
    image_index: imageIndex || 0,
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

function retweetTweet(feed, index, callback) {
  var cache = loadJSON('cache_' + feed);
  if (!cache || !cache.tweets[index]) return callback('server');
  serverRequest('POST', '/api/retweet', { tweet_id: cache.tweets[index].id }, callback);
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
    var mediaUrls = normalizeMediaUrls(t);
    var mediaCount = mediaUrls.length;
    var hasPhoto = mediaCount > 0;
    if (hasPhoto) text += ' [photo]';
    enqueue({
      TWEET_INDEX: i,
      AUTHOR: truncateUtf8(t.author, MAX_AUTHOR_BYTES),
      NAME: truncateUtf8(t.name || t.author, MAX_NAME_BYTES),
      TEXT: truncateUtf8(text, MAX_TEXT_BYTES),
      TIME_AGO: timeAgo(t.created_at),
      LIKED: t.liked ? 1 : 0,
      HAS_MEDIA: hasPhoto ? 1 : 0,
      MEDIA_COUNT: mediaCount,
      REPLY_COUNT: t.reply_count || 0
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
  var imageIndex = payload.IMAGE_INDEX || 0;
  var cache = loadJSON('cache_' + feed);
  var tweet = cache && cache.tweets && cache.tweets[index];
  if (!tweet) {
    return sendImageError(requestId, IMAGE_ERROR_MISSING);
  }
  var mediaUrls = normalizeMediaUrls(tweet);
  var mediaUrl = mediaUrls[imageIndex] || '';
  if (!mediaUrl && !tweet.id) return sendImageError(requestId, IMAGE_ERROR_MISSING);

  fetchMedia(
    mediaUrl,
    tweet.id,
    imageIndex,
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

// ---- Comments (full text + replies), lazy-loaded on demand ----

function buildCommentsBody(fullText, replies, replyCount) {
  var body = fullText || '';
  var shown = replies.length;
  if (shown === 0) return body;  // detail auto-loads; keep reply-less tweets clean
  // Header count uses the tweet's reply_count metadata (same value the timeline
  // footer shows) so the two screens agree; the list itself is only what we
  // could actually fetch, which X caps and sometimes trims.
  var n = replyCount > 0 ? replyCount : shown;
  body += '\n\n----------\n' +
          n + (n === 1 ? ' reply' : ' replies') + '\n\n';
  for (var i = 0; i < shown; i++) {
    var r = replies[i];
    var who = r.handle || r.name || '?';
    body += '@' + who + ' · ' + timeAgo(r.created_at) + '\n' + (r.text || '');
    if (i < shown - 1) body += '\n\n';
  }
  return body;
}

function sendComments(requestId, body) {
  var bytes = utf8Bytes(truncateUtf8(body, MAX_BODY_BYTES));
  var chunkCount = Math.ceil(bytes.length / COMMENTS_CHUNK_BYTES);
  enqueue({
    COMMENTS_ID: requestId,
    COMMENTS_TOTAL: bytes.length,
    COMMENTS_CHUNK_COUNT: chunkCount
  });
  for (var i = 0; i < chunkCount; i++) {
    var start = i * COMMENTS_CHUNK_BYTES;
    var end = Math.min(start + COMMENTS_CHUNK_BYTES, bytes.length);
    var chunk = [];
    for (var j = start; j < end; j++) chunk.push(bytes[j]);
    enqueue({ COMMENTS_ID: requestId, COMMENTS_OFFSET: start, COMMENTS_DATA: chunk });
  }
}

function deliverComments(payload, feed) {
  var requestId = payload.COMMENTS_ID || 0;
  var index = payload.TWEET_INDEX;
  var cache = loadJSON('cache_' + feed);
  var tweet = cache && cache.tweets && cache.tweets[index];
  if (!tweet || !tweet.id) {
    return enqueue({ COMMENTS_ID: requestId, COMMENTS_ERROR: COMMENTS_ERROR_MISSING });
  }
  serverRequest('GET', '/api/tweet?id=' + encodeURIComponent(tweet.id), null,
    function (err, data) {
      if (err === 'network') return enqueue({ COMMENTS_ID: requestId, COMMENTS_ERROR: COMMENTS_ERROR_NETWORK });
      if (err) return enqueue({ COMMENTS_ID: requestId, COMMENTS_ERROR: COMMENTS_ERROR_SERVER });
      var fullText = (data && data.full_text) || tweet.text || '';
      var replies = (data && data.replies) || [];
      if (data && data.replies_error) {
        console.log('comments: 0 replies for ' + tweet.id + ' -> ' + data.replies_error);
      }
      sendComments(requestId, buildCommentsBody(fullText, replies, tweet.reply_count));
    });
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
    // Stale-while-revalidate: refresh an old cache in the background. The
    // watch shows its refresh indicator meanwhile; errors stay silent - the
    // user is already looking at usable cached tweets - but still clear the
    // indicator with a plain STATUS_OK.
    if (Date.now() - (cache.fetchedAt || 0) > CACHE_FRESH_MS) {
      sendStatus(STATUS_FETCHING);
      fetchTimeline(feed, function (err, tweets) {
        if (!err && tweets.length > 0) return sendTimeline(tweets);
        sendStatus(STATUS_OK);
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
  console.log('TweetFit pkjs ready, configured: ' + serverConfigured());
});

Pebble.addEventListener('appmessage', function (e) {
  var payload = e.payload;
  var feed = currentFeed(payload);
  if (payload.CMD !== undefined) {
    if (payload.CMD === CMD_FETCH || payload.CMD === CMD_REFRESH) {
      deliverTimeline(payload.CMD === CMD_REFRESH, feed);
    } else if (payload.CMD === CMD_IMAGE) {
      deliverImage(payload, feed);
    } else if (payload.CMD === CMD_COMMENTS) {
      deliverComments(payload, feed);
    }
  }
  if (payload.LIKE_INDEX !== undefined) {
    var index = payload.LIKE_INDEX;
    likeTweet(feed, index, function (err) {
      enqueue({ LIKE_RESULT: err ? -(index + 1) : index });
    });
  }
  if (payload.RETWEET_INDEX !== undefined) {
    var retweetIndex = payload.RETWEET_INDEX;
    retweetTweet(feed, retweetIndex, function (err) {
      enqueue({ RETWEET_RESULT: err ? -(retweetIndex + 1) : retweetIndex });
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
