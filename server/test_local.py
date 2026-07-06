"""Smoke test for the FastAPI app with a mocked twikit client (no X creds)."""
import json
import os
import sys
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


with mock.patch("_common.Client", FakeClient):
    from fastapi.testclient import TestClient
    import index

    client = TestClient(index.app)
    AUTH = {"Authorization": "Bearer test-token"}

    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["ok"] is True
    print("PASS  health -> ok (no auth)")

    r = client.get("/api/timeline?feed=following")
    assert r.status_code == 401, r.status_code
    print("PASS  no-token -> 401")

    r = client.get("/api/timeline?feed=following", headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401
    print("PASS  bad-token -> 401")

    r = client.get("/api/timeline?feed=following", headers=AUTH)
    b = r.json()
    assert r.status_code == 200 and b["feed"] == "following" and len(b["tweets"]) == 15, b
    assert b["tweets"][0]["handle"] == "janedev"
    assert b["tweets"][2]["has_media"] is True and b["tweets"][0]["has_media"] is False
    print("PASS  following -> 15 tweets, media flag, handle")

    r = client.get("/api/timeline?feed=foryou", headers=AUTH)
    b = r.json()
    assert r.status_code == 200 and b["feed"] == "foryou" and b["tweets"][0]["id"] == "1100", b
    print("PASS  foryou -> distinct feed")

    r = client.post("/api/like", json={"tweet_id": "1000"}, headers=AUTH)
    assert r.status_code == 200 and r.json()["ok"] is True, r.json()
    print("PASS  like -> ok")

    r = client.post("/api/like", json={}, headers=AUTH)
    assert r.status_code == 422, r.status_code  # pydantic: missing tweet_id
    print("PASS  like no-id -> 422")

print("\nAll server smoke tests passed.")
