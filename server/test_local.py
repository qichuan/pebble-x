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
        self.media = (
            [
                {"media_url_https": f"https://pbs.twimg.com/media/fake-{i}-0.jpg"},
                {"media_url_https": f"https://pbs.twimg.com/media/fake-{i}-1.jpg"},
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
        # Oldest-first on purpose: the endpoint must sort newest-first by id.
        return [FakeTweet(i, media=(i == 17)) for i in range(count)]

    async def get_timeline(self, count=20):
        return [FakeTweet(i + 100) for i in range(count)]

    async def get_tweet_by_id(self, tid):
        return FakeTweet(77, media=True)

    async def favorite_tweet(self, tid):
        assert tid == "1000"
        return True

    async def retweet(self, tid):
        assert tid == "1000"
        return True

    async def user(self):
        return FakeUser()


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
    assert b["tweets"][2]["media_url"] == "https://pbs.twimg.com/media/fake-17-0.jpg"
    assert b["tweets"][2]["media_urls"] == [
        "https://pbs.twimg.com/media/fake-17-0.jpg",
        "https://pbs.twimg.com/media/fake-17-1.jpg",
    ]
    print("PASS  following -> 15 tweets newest-first, media URLs, handle")

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

    r = client.post("/api/retweet", json={"tweet_id": "1000"}, headers=AUTH)
    assert r.status_code == 200 and r.json()["ok"] is True, r.json()
    print("PASS  retweet -> ok")

    with mock.patch(
        "index.render_media_for_watch",
        return_value={
            "width": 144,
            "height": 120,
            "byte_count": 7,
            "image_base64": "iVBORw0=",
        },
    ):
        r = client.post(
            "/api/media",
            json={
                "media_url": "https://pbs.twimg.com/media/fake-17-0.jpg",
                "tweet_id": "1017",
                "width": 144,
                "height": 168,
                "color": True,
                "heap": 50000,
            },
            headers=AUTH,
        )
        b = r.json()
        assert r.status_code == 200 and b["width"] == 144 and b["image_base64"], b
        print("PASS  media -> watch PNG payload")

    with mock.patch(
        "index.render_media_for_watch",
        return_value={
            "width": 144,
            "height": 120,
            "byte_count": 7,
            "image_base64": "iVBORw0=",
        },
    ) as render_mock:
        r = client.post(
            "/api/media",
            json={
                "tweet_id": "1077",
                "image_index": 1,
                "width": 144,
                "height": 168,
                "color": True,
                "heap": 50000,
            },
            headers=AUTH,
        )
        assert r.status_code == 200, r.json()
        assert render_mock.call_args.args[0] == "https://pbs.twimg.com/media/fake-77-1.jpg"
        print("PASS  media fallback -> resolves indexed URL from tweet id")

    # ---- Setup wizard & cookie config ----
    r = client.get("/setup")
    assert r.status_code == 200 and "auth_token" in r.text and "Copy as cURL" in r.text
    print("PASS  setup page -> served without auth")

    good_cookies = {"auth_token": "a" * 40, "ct0": "b" * 160}

    r = client.post("/api/config", json=good_cookies)
    assert r.status_code == 401, r.status_code
    print("PASS  config no-token -> 401")

    r = client.post("/api/config", json={"auth_token": "not hex!", "ct0": "b" * 160}, headers=AUTH)
    assert r.status_code == 422, r.status_code
    print("PASS  config non-hex values -> 422")

    r = client.post("/api/config", json=good_cookies, headers=AUTH)
    assert r.status_code == 503 and "Upstash" in r.json()["detail"], r.json()
    print("PASS  config without storage -> 503 with Upstash hint")

    fake_store = {}
    with mock.patch("_storage.storage_configured", return_value=True), mock.patch(
        "_storage.store_cookies_raw", side_effect=lambda raw: fake_store.update(v=raw)
    ), mock.patch("_storage.load_cookies_raw", side_effect=lambda: fake_store.get("v")):
        r = client.post("/api/config", json=good_cookies, headers=AUTH)
        b = r.json()
        assert r.status_code == 200 and b["ok"] and b["verified"], b
        assert b["screen_name"] == "janedev"
        assert json.loads(fake_store["v"]) == good_cookies
        print("PASS  config -> stored raw X_COOKIES shape, verified as @janedev")

        r = client.get("/api/config/status", headers=AUTH)
        assert r.json() == {"configured": True, "source": "redis"}, r.json()
        print("PASS  config status -> redis source")

    r = client.get("/api/config/status", headers=AUTH)
    assert r.json() == {"configured": True, "source": "env"}, r.json()
    print("PASS  config status -> env fallback source")

    with mock.patch.dict(os.environ):
        del os.environ["X_COOKIES"]
        r = client.get("/api/timeline?feed=following", headers=AUTH)
        assert r.status_code == 502 and "/setup" in r.json()["detail"], r.json()
        print("PASS  timeline unconfigured -> 502 pointing at /setup")

        r = client.get("/api/config/status", headers=AUTH)
        assert r.json() == {"configured": False, "source": None}, r.json()
        print("PASS  config status unconfigured -> false")

# Regression for the 2026-07 X payload change: real twikit (no network) must
# parse a user whose legacy.entities.description has no 'urls' key, and one
# whose fields moved out of `legacy` into the new `core`/`avatar` groups.
from twikit.user import User as TwikitUser
from twikit.media import Photo as TwikitPhoto

import _common

photo = TwikitPhoto(None, {"type": "photo", "media_url_https": "https://pbs.twimg.com/media/p.jpg"})
assert _common.media_url(photo) == "https://pbs.twimg.com/media/p.jpg"
print("PASS  twikit media parse -> extracts Photo.media_url")

photo2 = TwikitPhoto(None, {"type": "photo", "media_url_https": "https://pbs.twimg.com/media/q.jpg"})
tweet_with_photos = type("TweetWithPhotos", (), {"media": [photo, photo2]})()
assert _common.media_urls(tweet_with_photos) == [
    "https://pbs.twimg.com/media/p.jpg",
    "https://pbs.twimg.com/media/q.jpg",
]
print("PASS  twikit media parse -> extracts multiple photos")

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
