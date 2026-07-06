"""Local smoke test: drives the real handler classes over a real socket with
a mocked twikit Client (no X credentials needed)."""
import json
import os
import sys
import threading
import urllib.request
import urllib.error
from http.server import HTTPServer
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "api"))

os.environ["APP_TOKEN"] = "test-token"
os.environ["X_COOKIES"] = json.dumps({"auth_token": "x", "ct0": "y"})


class FakeUser:
    name = "Jane Dev"
    screen_name = "janedev"


class FakeTweet:
    def __init__(self, i, media=False):
        self.id = str(1000 + i)
        self.user = FakeUser()
        self.text = f"Tweet number {i} 中文"
        self.created_at = "Mon Jul 06 09:00:00 +0000 2026"
        self.favorited = False
        self.media = ["m"] if media else []


class FakeClient:
    def __init__(self, *a, **k):
        pass

    def set_cookies(self, c):
        pass

    async def get_latest_timeline(self, count=20):
        return [FakeTweet(i, media=(i == 2)) for i in range(count)]

    async def get_timeline(self, count=20):
        return [FakeTweet(i + 100) for i in range(count)]

    async def favorite_tweet(self, tid):
        assert tid == "1000"
        return True


def serve(module_name, port):
    mod = __import__(module_name)
    srv = HTTPServer(("127.0.0.1", port), mod.handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def req(url, method="GET", data=None, token="test-token"):
    headers = {}
    if token:
        headers["Authorization"] = "Bearer " + token
    body = json.dumps(data).encode() if data is not None else None
    r = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


with mock.patch("twikit.Client", FakeClient), mock.patch("_common.Client", FakeClient):
    serve("timeline", 8811)
    serve("like", 8812)

    # auth rejection
    s, b = req("http://127.0.0.1:8811/api/timeline?feed=following", token=None)
    assert s == 401, (s, b)
    print("PASS  no-token -> 401")

    s, b = req("http://127.0.0.1:8811/api/timeline?feed=following", token="wrong")
    assert s == 401
    print("PASS  bad-token -> 401")

    # following feed
    s, b = req("http://127.0.0.1:8811/api/timeline?feed=following")
    assert s == 200 and b["feed"] == "following" and len(b["tweets"]) == 15, (s, b)
    assert b["tweets"][0]["handle"] == "janedev"
    assert b["tweets"][2]["has_media"] is True
    assert b["tweets"][0]["has_media"] is False
    print("PASS  following -> 15 tweets, media flag, handle")

    # foryou feed
    s, b = req("http://127.0.0.1:8811/api/timeline?feed=foryou")
    assert s == 200 and b["feed"] == "foryou" and b["tweets"][0]["id"] == "1100", (s, b)
    print("PASS  foryou -> distinct feed")

    # like
    s, b = req("http://127.0.0.1:8812/api/like", method="POST", data={"tweet_id": "1000"})
    assert s == 200 and b["ok"] is True, (s, b)
    print("PASS  like -> ok")

    # like missing id
    s, b = req("http://127.0.0.1:8812/api/like", method="POST", data={})
    assert s == 400, (s, b)
    print("PASS  like no-id -> 400")

print("\nAll server smoke tests passed.")
