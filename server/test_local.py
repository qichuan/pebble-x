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
    def __init__(self, i, media=False, reply_count=0, replies=None, full_text=None):
        self.id = str(1000 + i)
        self.user = FakeUser()
        self.text = f"Tweet number {i} 中文"
        self.full_text = full_text if full_text is not None else self.text
        self.created_at = "Mon Jul 06 09:00:00 +0000 2026"
        self.favorited = False
        self.reply_count = reply_count
        self.replies = replies
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
        # Deliberately NOT newest-first: the endpoint must preserve X's own
        # ordering (pinned/promoted placement) rather than re-sort.
        return [FakeTweet(i, media=(i == 2), reply_count=(3 if i == 0 else 0))
                for i in range(count)]

    async def get_timeline(self, count=20):
        return [FakeTweet(i + 100) for i in range(count)]

    async def get_tweets_by_ids(self, ids):
        # Stable full-text path.
        return [FakeTweet(
            77, media=True,
            full_text="A very long note tweet body that exceeds the legacy 280",
        )]

    async def get_tweet_by_id(self, tid):
        if tid == "9999":
            raise Exception("TweetDetail exploded")  # brittle replies path
        replies = [FakeTweet(200 + j) for j in range(20)]  # server should cap
        return FakeTweet(
            77, media=True, reply_count=20, replies=replies,
            full_text="A very long note tweet body that exceeds the legacy 280",
        )

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
    assert ids == [str(1000 + i) for i in range(15)], ids  # X's order, untouched
    assert b["tweets"][2]["has_media"] is True and b["tweets"][0]["has_media"] is False
    assert b["tweets"][2]["media_url"] == "https://pbs.twimg.com/media/fake-2-0.jpg"
    assert b["tweets"][2]["media_urls"] == [
        "https://pbs.twimg.com/media/fake-2-0.jpg",
        "https://pbs.twimg.com/media/fake-2-1.jpg",
    ]
    print("PASS  following -> 15 tweets in X's own order, media URLs, handle")

    r = client.get("/api/timeline?feed=foryou", headers=AUTH)
    b = r.json()
    assert r.status_code == 200 and b["feed"] == "foryou" and b["tweets"][0]["id"] == "1100", b
    print("PASS  foryou -> distinct feed, X's order preserved")

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

    # timeline carries reply_count so the watch knows which tweets have comments
    r = client.get("/api/timeline?feed=following", headers=AUTH)
    b = r.json()
    assert b["tweets"][0]["reply_count"] == 3 and b["tweets"][1]["reply_count"] == 0, b
    print("PASS  timeline -> reply_count per tweet")

    r = client.get("/api/tweet?id=1077", headers=AUTH)
    b = r.json()
    assert r.status_code == 200, b
    assert b["full_text"].startswith("A very long note tweet"), b
    assert len(b["replies"]) == 12, len(b["replies"])  # capped at MAX_REPLIES
    assert b["replies"][0]["handle"] == "janedev", b["replies"][0]
    print("PASS  tweet detail -> full_text + capped replies")

    # Brittle replies path fails -> still serve full text with no comments (no 502)
    r = client.get("/api/tweet?id=9999", headers=AUTH)
    b = r.json()
    assert r.status_code == 200, b
    assert b["full_text"].startswith("A very long note tweet") and b["replies"] == [], b
    print("PASS  tweet detail -> degrades to full_text when replies fetch fails")

    r = client.get("/api/tweet?id=1077")
    assert r.status_code == 401
    print("PASS  tweet detail -> Bearer required")

    from _common import strip_trailing_tco as _strip
    assert _strip("hello https://t.co/abc123") == "hello"
    assert _strip("photo https://t.co/a https://t.co/b") == "photo"  # trailing run
    assert _strip("mid https://t.co/x more") == "mid https://t.co/x more"  # not trailing
    assert _strip("see https://t.co/a and https://t.co/b") == "see https://t.co/a and"
    print("PASS  strip trailing t.co links only")

    # ---- Setup wizard, claim, cookie config & pairing ----
    import _storage

    r = client.get("/setup")
    assert r.status_code == 200 and "auth_token" in r.text and "Copy as cURL" in r.text
    r = client.get("/")
    assert r.status_code == 200 and "Copy as cURL" in r.text
    print("PASS  setup wizard -> served at / and /setup without auth")

    good_cookies = {"auth_token": "a" * 40, "ct0": "b" * 160}

    r = client.post("/api/config", json=good_cookies, headers=AUTH)
    assert r.status_code == 503 and "Redis" in r.json()["detail"], r.json()
    print("PASS  config without storage -> 503 with Redis hint")

    r = client.get("/api/config/status")
    assert r.json() == {
        "claimed": True, "storage": False, "cookies": True,
        "source": "env", "token_source": "env",
    }, r.json()
    print("PASS  status (no auth) -> claimed via env, env cookies")

    kv = {}
    with mock.patch("_storage.storage_configured", return_value=True), mock.patch(
        "_storage.kv_get", side_effect=kv.get
    ), mock.patch(
        "_storage.kv_set", side_effect=lambda k, v, ex_seconds=None: kv.update({k: v})
    ), mock.patch("_storage.kv_del", side_effect=lambda k: kv.pop(k, None)):
        # Claimed (via env APP_TOKEN): config requires Bearer
        r = client.post("/api/config", json=good_cookies)
        assert r.status_code == 401, r.status_code
        r = client.post("/api/config", json=good_cookies, headers={"Authorization": "Bearer no"})
        assert r.status_code == 401
        print("PASS  config on claimed server -> 401 without valid token")

        r = client.post(
            "/api/config", json={"auth_token": "not hex!", "ct0": "b" * 160}, headers=AUTH
        )
        assert r.status_code == 422, r.status_code
        print("PASS  config non-hex values -> 422")

        r = client.post("/api/config", json=good_cookies, headers=AUTH)
        b = r.json()
        assert r.status_code == 200 and b["ok"] and b["verified"], b
        assert b["screen_name"] == "janedev" and b["claimed"] is False and "app_token" not in b
        assert json.loads(kv[_storage.COOKIES_KEY]) == good_cookies
        code = b["pair_code"]
        assert len(code) == 6 and code.isdigit(), code
        assert json.loads(kv[_storage.PAIR_KEY]) == {"code": code, "tries": 0}
        print("PASS  config -> stored cookies, verified @janedev, 6-digit code minted")

        r = client.get("/api/config/status")
        assert r.json() == {
            "claimed": True, "storage": True, "cookies": True,
            "source": "redis", "token_source": "env",
        }, r.json()
        print("PASS  status -> redis cookies, env token")

        wrong = "000000" if code != "000000" else "111111"
        r = client.post("/api/pair", json={"code": wrong})
        assert r.status_code == 404
        r = client.post("/api/pair", json={"code": code[:3] + " " + code[3:]})
        assert r.status_code == 200 and r.json()["app_token"] == "test-token", r.json()
        r = client.post("/api/pair", json={"code": code})
        assert r.status_code == 404  # single-use
        print("PASS  pair -> ignores spacing, returns token once, then 404")

        # On-demand code minting (wizard's "Get pairing code" button)
        r = client.post("/api/pair/new")
        assert r.status_code == 401
        r = client.post("/api/pair/new", headers=AUTH)
        b = r.json()
        assert r.status_code == 200 and len(b["pair_code"]) == 6, b
        r = client.post("/api/pair", json={"code": b["pair_code"]})
        assert r.status_code == 200 and r.json()["app_token"] == "test-token"
        print("PASS  pair/new -> Bearer-gated, mints an exchangeable code")

        # Brute-force guard: 10 wrong guesses burn the active code for good
        code = client.post("/api/pair/new", headers=AUTH).json()["pair_code"]
        wrong = "000000" if code != "000000" else "111111"
        for _ in range(10):
            assert client.post("/api/pair", json={"code": wrong}).status_code == 404
        r = client.post("/api/pair", json={"code": code})
        assert r.status_code == 404, "code should be burned after 10 bad tries"
        print("PASS  pair -> 10 wrong guesses invalidate the code")

        # Unclaimed server (no Redis token, no env): first save claims it
        kv.clear()
        with mock.patch.dict(os.environ):
            del os.environ["APP_TOKEN"]
            r = client.get("/api/config/status")
            assert r.json()["claimed"] is False and r.json()["token_source"] is None
            r = client.get("/api/timeline?feed=following", headers=AUTH)
            assert r.status_code == 401  # nothing to authenticate against
            r = client.post("/api/config", json=good_cookies)  # no auth header
            b = r.json()
            assert r.status_code == 200 and b["claimed"] is True and b["verified"], b
            minted = b["app_token"]
            assert minted and kv[_storage.TOKEN_KEY] == minted
            r = client.get("/api/config/status")
            assert r.json()["claimed"] is True and r.json()["token_source"] == "redis"
            print("PASS  unclaimed server -> first save claims, mints token (redis)")

            r = client.post("/api/config", json=good_cookies)
            assert r.status_code == 401
            print("PASS  claimed server -> unauthenticated re-save rejected")

            minted_auth = {"Authorization": "Bearer " + minted}
            r = client.get("/api/timeline?feed=following", headers=minted_auth)
            assert r.status_code == 200
            r = client.post("/api/pair", json={"code": b["pair_code"]})
            assert r.json()["app_token"] == minted
            print("PASS  minted token -> works for data endpoints and pairing")

    # TCP transport wiring: a bare REDIS_URL (Marketplace Redis Cloud etc.,
    # no REST API) must count as configured and route kv ops through redis-py.
    class FakeRedisClient:
        store = {}

        def get(self, key):
            return self.store.get(key)

        def set(self, key, value, ex=None):
            self.store[key] = (value, ex)

        def delete(self, key):
            self.store.pop(key, None)

        def close(self):
            pass

    fake_redis_mod = type(sys)("redis")
    fake_redis_mod.Redis = type("Redis", (), {
        "from_url": staticmethod(lambda url, **k: FakeRedisClient())
    })
    with mock.patch.dict(os.environ, {"REDIS_URL": "rediss://default:pw@host:6379"}), \
            mock.patch.dict(sys.modules, {"redis": fake_redis_mod}):
        import _storage as st
        assert st.storage_configured()
        st.kv_set("k", "v", ex_seconds=60)
        assert FakeRedisClient.store["k"] == ("v", 60)
        assert st.kv_get("k") == ("v", 60)
        st.kv_del("k")
        assert "k" not in FakeRedisClient.store
        print("PASS  storage -> bare REDIS_URL routes through redis-py TCP client")

    with mock.patch.dict(os.environ):
        del os.environ["X_COOKIES"]
        r = client.get("/api/timeline?feed=following", headers=AUTH)
        assert r.status_code == 502 and "setup page" in r.json()["detail"], r.json()
        print("PASS  timeline unconfigured -> 502 pointing at the setup page")

        r = client.get("/api/config/status")
        assert r.json() == {
            "claimed": True, "storage": False, "cookies": False,
            "source": None, "token_source": "env",
        }, r.json()
        print("PASS  status without cookies -> cookies false")

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
