"""Runs the real FastAPI app with a mocked twikit client so the Pebble emulator
has a live endpoint to hit. Run: python mock_server.py 9099"""
import json
import os
import sys
from unittest import mock

import uvicorn

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
        self.media = ["m"] if media else []


class FakeClient:
    def __init__(self, *a, **k):
        pass

    def set_cookies(self, c):
        pass

    async def get_latest_timeline(self, count=20):
        return [FakeTweet(i, "janedev", media=(i == 1)) for i in range(count)]

    async def get_timeline(self, count=20):
        return [FakeTweet(i + 50, "foryoubot") for i in range(count)]

    async def favorite_tweet(self, tid):
        print("LIKED tweet", tid, flush=True)
        return True


port = int(sys.argv[1]) if len(sys.argv) > 1 else 9099
mock.patch("_common.Client", FakeClient).start()

import index  # noqa: E402

print("Mock Peep server on http://127.0.0.1:%d (token: test-token)" % port, flush=True)
uvicorn.run(index.app, host="127.0.0.1", port=port, log_level="info")
