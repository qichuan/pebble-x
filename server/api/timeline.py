import asyncio
import os
import sys
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(__file__))
from _common import authorized, make_client, tweet_to_dict, send_json

MAX_TWEETS = 15


async def _fetch(feed: str):
    client = make_client()
    if feed == "foryou":
        result = await client.get_timeline(count=20)
    else:
        result = await client.get_latest_timeline(count=20)
    return [tweet_to_dict(t) for t in list(result)[:MAX_TWEETS]]


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if not authorized(self.headers):
            return send_json(self, 401, {"error": "unauthorized"})
        params = parse_qs(urlparse(self.path).query)
        feed = (params.get("feed") or ["following"])[0]
        try:
            tweets = asyncio.run(_fetch(feed))
        except Exception as e:  # twikit breakage, blocked IP, bad cookies, etc.
            return send_json(self, 502, {"error": str(e)})
        return send_json(self, 200, {"feed": feed, "tweets": tweets})

    def log_message(self, *args):
        pass
