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
        # Oldest-first on purpose: the endpoint must sort newest-first by id.
        return [FakeTweet(i, media=(i == 17)) for i in range(count)]

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
    ids = [t["id"] for t in b["tweets"]]
    assert ids == sorted(ids, key=int, reverse=True) and ids[0] == "1019", ids
    assert b["tweets"][2]["has_media"] is True and b["tweets"][0]["has_media"] is False
    print("PASS  following -> 15 tweets newest-first, media flag, handle")

    r = client.get("/api/timeline?feed=foryou", headers=AUTH)
    b = r.json()
    assert r.status_code == 200 and b["feed"] == "foryou" and b["tweets"][0]["id"] == "1119", b
    print("PASS  foryou -> distinct feed, newest-first")

    r = client.post("/api/like", json={"tweet_id": "1000"}, headers=AUTH)
    assert r.status_code == 200 and r.json()["ok"] is True, r.json()
    print("PASS  like -> ok")

    r = client.post("/api/like", json={}, headers=AUTH)
    assert r.status_code == 422, r.status_code  # pydantic: missing tweet_id
    print("PASS  like no-id -> 422")

# Regression for the 2026-07 X payload change: real twikit (no network) must
# parse a user whose legacy.entities.description has no 'urls' key, and one
# whose fields moved out of `legacy` into the new `core`/`avatar` groups.
from twikit.user import User as TwikitUser

slim = {
    "rest_id": "42",
    "legacy": {
        "name": "Jane Dev",
        "screen_name": "janedev",
        "entities": {"description": {}},  # 'urls' omitted — used to KeyError
    },
}
u = TwikitUser(None, slim)
assert u.screen_name == "janedev" and u.description_urls == []
print("PASS  twikit user parse -> survives missing description.urls")

migrated = {
    "rest_id": "43",
    "legacy": {},
    "core": {"name": "New Layout", "screen_name": "newlayout", "created_at": "x"},
    "avatar": {"image_url": "https://example.com/a.jpg"},
}
u = TwikitUser(None, migrated)
assert u.name == "New Layout" and u.screen_name == "newlayout"
assert u.profile_image_url == "https://example.com/a.jpg"
print("PASS  twikit user parse -> backfills from core/avatar groups")

print("\nAll server smoke tests passed.")
