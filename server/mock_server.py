"""Runs the real FastAPI app with a mocked twikit client so the Pebble emulator
has a live endpoint to hit. Run: python mock_server.py 9099"""
import base64
import io
import json
import os
import sys
from unittest import mock

import uvicorn
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "api"))
# MOCK_UNCLAIMED=1 starts without a token so the /setup claim flow can be
# tested in a browser; the wizard will mint and display one.
if os.environ.get("MOCK_UNCLAIMED") != "1":
    os.environ["APP_TOKEN"] = "test-token"
os.environ["X_COOKIES"] = json.dumps({"auth_token": "x", "ct0": "y"})


class FakeUser:
    def __init__(self, h):
        self.name, self.screen_name = h.title(), h


class FakeTweet:
    def __init__(self, i, handle, media=False, reply_count=0, replies=None, full_text=None):
        self.id = str(1000 + i)
        self.user = FakeUser(handle)
        self.text = (
            "Following tweet %d about pebble watches and 中文 text" % i
            if handle == "janedev"
            else "For You recommended tweet %d with a longer body to exercise scrolling" % i
        )
        self.full_text = full_text if full_text is not None else self.text
        self.created_at = "Mon Jul 06 08:%02d:00 +0000 2026" % (i % 60)
        self.favorited = False
        # Vary reply counts so some tweets show the "Press DOWN for comments"
        # hint and others don't.
        self.reply_count = reply_count if reply_count else (i % 4)
        self.replies = replies
        self.media = (
            [
                {"media_url_https": "https://pbs.twimg.com/media/mock-%d-0.png" % i},
                {"media_url_https": "https://pbs.twimg.com/media/mock-%d-1.png" % i},
                {"media_url_https": "https://pbs.twimg.com/media/mock-%d-2.png" % i},
            ]
            if media
            else []
        )


class FakeClient:
    def __init__(self, *a, **k):
        pass

    def set_cookies(self, c):
        pass

    async def get_latest_timeline(self, count=20):
        return [FakeTweet(i, "janedev", media=(i == 1)) for i in range(count)]

    async def get_timeline(self, count=20):
        return [FakeTweet(i + 50, "foryoubot") for i in range(count)]

    async def get_tweets_by_ids(self, ids):
        note = ("This is the full note-tweet body. " * 12).strip()
        return [FakeTweet(1, "janedev", media=True, full_text=note)]

    async def get_tweet_by_id(self, tid):
        handles = ["alice", "bob", "carol", "dave", "erin", "frank"]
        replies = []
        for j, h in enumerate(handles):
            r = FakeTweet(300 + j, h)
            r.text = "Reply %d from @%s — this is a sample comment 中文 body." % (j + 1, h)
            replies.append(r)
        note = ("This is the full note-tweet body. " * 12).strip()
        return FakeTweet(1, "janedev", media=True, reply_count=len(replies),
                         replies=replies, full_text=note)

    async def favorite_tweet(self, tid):
        print("LIKED tweet", tid, flush=True)
        return True

    async def retweet(self, tid):
        print("RETWEETED tweet", tid, flush=True)
        return True

    async def user(self):
        return FakeUser("janedev")


port = int(sys.argv[1]) if len(sys.argv) > 1 else 9099
mock.patch("_common.Client", FakeClient).start()

import index  # noqa: E402
import _storage  # noqa: E402

# In-memory KV so the /setup wizard, claim, and pairing flows are fully
# browser-testable without Upstash.
_kv = {}
_storage.storage_configured = lambda: True
_storage.kv_get = _kv.get
_storage.kv_set = lambda key, value, ex_seconds=None: _kv.update({key: value})
_storage.kv_del = lambda key: _kv.pop(key, None)


def fake_render_media_for_watch(media_url, width, height, color, heap=0):
    size = (max(64, min(int(width), 200)), max(64, min(int(height), 200)))
    image = Image.new("RGB", size, (255, 255, 255))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, size[0] - 1, size[1] - 1), outline=(0, 85, 170), width=4)
    draw.rectangle((12, 12, size[0] - 13, size[1] // 2), fill=(255, 85, 0))
    draw.ellipse((size[0] // 3, size[1] // 3, size[0] - 18, size[1] - 18), fill=(0, 170, 85))
    draw.text((18, size[1] - 34), "TweetFit photo", fill=(0, 0, 0))
    if color:
        output = image.quantize(colors=16, dither=Image.Dither.FLOYDSTEINBERG)
    else:
        output = image.convert("L").convert("1", dither=Image.Dither.FLOYDSTEINBERG)
    buf = io.BytesIO()
    output.save(buf, format="PNG", optimize=True)
    data = buf.getvalue()
    return {
        "width": output.width,
        "height": output.height,
        "byte_count": len(data),
        "image_base64": base64.b64encode(data).decode("ascii"),
    }


index.render_media_for_watch = fake_render_media_for_watch

print("Mock TweetFit server on http://127.0.0.1:%d (token: test-token)" % port, flush=True)
uvicorn.run(index.app, host="127.0.0.1", port=port, log_level="info")
