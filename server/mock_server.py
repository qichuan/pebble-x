"""Serves the real timeline/like handlers with a mocked twikit client so the
Pebble emulator has a live endpoint to hit. Run: python mock_server.py 9099"""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "api"))
os.environ["APP_TOKEN"] = "test-token"
os.environ["X_COOKIES"] = json.dumps({"auth_token": "x", "ct0": "y"})


class FakeUser:
    def __init__(self, h): self.name, self.screen_name = h.title(), h


class FakeTweet:
    def __init__(self, i, handle, media=False):
        self.id = str(1000 + i)
        self.user = FakeUser(handle)
        self.text = "Following tweet %d about pebble watches and 中文 text" % i if handle == "janedev" \
            else "For You recommended tweet %d with a longer body to exercise scrolling" % i
        self.created_at = "Mon Jul 06 08:%02d:00 +0000 2026" % (i % 60)
        self.favorited = False
        self.media = ["m"] if media else []


class FakeClient:
    def __init__(self, *a, **k): pass
    def set_cookies(self, c): pass
    async def get_latest_timeline(self, count=20):
        return [FakeTweet(i, "janedev", media=(i == 1)) for i in range(count)]
    async def get_timeline(self, count=20):
        return [FakeTweet(i + 50, "foryoubot") for i in range(count)]
    async def favorite_tweet(self, tid):
        print("LIKED tweet", tid); return True


port = int(sys.argv[1]) if len(sys.argv) > 1 else 9099
patch = mock.patch("_common.Client", FakeClient)
patch.start()

import timeline as timeline_mod  # noqa: E402
import like as like_mod  # noqa: E402


class Router(BaseHTTPRequestHandler):
    def _dispatch(self, method):
        cls = timeline_mod.handler if self.path.startswith("/api/timeline") else like_mod.handler
        # Reuse the endpoint handler logic by binding its do_* to this request.
        h = cls.__new__(cls)
        h.__dict__ = self.__dict__
        getattr(h, method)()
    def do_GET(self): self._dispatch("do_GET")
    def do_POST(self): self._dispatch("do_POST")
    def log_message(self, *a): pass


print("Mock Peep server on http://127.0.0.1:%d (token: test-token)" % port)
ThreadingHTTPServer(("127.0.0.1", port), Router).serve_forever()
