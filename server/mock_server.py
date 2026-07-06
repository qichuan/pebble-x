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
os.environ["APP_TOKEN"] = "test-token"
os.environ["X_COOKIES"] = json.dumps({"auth_token": "x", "ct0": "y"})


class FakeUser:
    def __init__(self, h):
        self.name, self.screen_name = h.title(), h


class FakeTweet:
    def __init__(self, i, handle, media=False):
        self.id = str(1000 + i)
        self.user = FakeUser(handle)
        self.text = (
            "Following tweet %d about pebble watches and 中文 text" % i
            if handle == "janedev"
            else "For You recommended tweet %d with a longer body to exercise scrolling" % i
        )
        self.created_at = "Mon Jul 06 08:%02d:00 +0000 2026" % (i % 60)
        self.favorited = False
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

    async def get_tweet_by_id(self, tid):
        return FakeTweet(1, "janedev", media=True)

    async def favorite_tweet(self, tid):
        print("LIKED tweet", tid, flush=True)
        return True

    async def retweet(self, tid):
        print("RETWEETED tweet", tid, flush=True)
        return True


port = int(sys.argv[1]) if len(sys.argv) > 1 else 9099
mock.patch("_common.Client", FakeClient).start()

import index  # noqa: E402


def fake_render_media_for_watch(media_url, width, height, color, heap=0):
    size = (max(64, min(int(width), 200)), max(64, min(int(height), 200)))
    image = Image.new("RGB", size, (255, 255, 255))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, size[0] - 1, size[1] - 1), outline=(0, 85, 170), width=4)
    draw.rectangle((12, 12, size[0] - 13, size[1] // 2), fill=(255, 85, 0))
    draw.ellipse((size[0] // 3, size[1] // 3, size[0] - 18, size[1] - 18), fill=(0, 170, 85))
    draw.text((18, size[1] - 34), "Peep photo", fill=(0, 0, 0))
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

print("Mock Peep server on http://127.0.0.1:%d (token: test-token)" % port, flush=True)
uvicorn.run(index.app, host="127.0.0.1", port=port, log_level="info")
