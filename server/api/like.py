import asyncio
import json
import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(__file__))
from _common import authorized, make_client, send_json


async def _like(tweet_id: str):
    client = make_client()
    await client.favorite_tweet(tweet_id)


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if not authorized(self.headers):
            return send_json(self, 401, {"error": "unauthorized"})
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
            tweet_id = str(body["tweet_id"])
        except (ValueError, KeyError):
            return send_json(self, 400, {"error": "tweet_id required"})
        try:
            asyncio.run(_like(tweet_id))
        except Exception as e:
            return send_json(self, 502, {"error": str(e)})
        return send_json(self, 200, {"ok": True})

    def log_message(self, *args):
        pass
