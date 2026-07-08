"""Runtime patches for twikit 2.3.3 (latest on PyPI, upstream is slow).

Patch 1 — transaction-key extraction (2026-03).
Around 2026-03-18 X changed the webpack layout of its home page: the
`ondemand.s` chunk hash is no longer embedded inline as `"ondemand.s":"<hash>"`.
It is now a numeric chunk index (`,<idx>:"ondemand.s"`) that must be resolved to
a hash via a second `,<idx>:"<hash>"` entry. twikit 2.3.3 (latest on PyPI) still
uses the old regex, so login fails with `Couldn't get KEY_BYTE indices`.

This module overrides `ClientTransaction.get_indices` with a version that handles
the new format and falls back to the old one. It patches the official PyPI package
in place, so we keep `twikit` in requirements.txt and own this ~1-file fix.

Fix adapted from d60/twikit PRs #410 / #411. Import this module once before using
twikit (both _common.py and login.py do).

If login breaks again with the same error, X likely changed the format once more —
re-check those upstream PRs/issues and update the regexes below.

Patch 2 — user payload backfill (2026-07).
X is migrating user fields out of `legacy` into new top-level groups (`core`,
`avatar`, `location`, `profile_bio`, `verification`, ...) and has started
omitting keys that twikit's `User.__init__` subscripts directly. First
casualty: `legacy.entities.description.urls`, which made every timeline fetch
fail with KeyError('urls') (surfaced as 502 {"detail":"'urls'"}). Before the
original `__init__` runs we backfill missing `legacy` keys from their new
locations when present, else with a benign default. If timelines 502 again
with a bare quoted key name as the detail, it's the same class of breakage —
add the key here.
"""
import re
from functools import partial as _partial

from twikit import Client as _Client
from twikit import user as _user
from twikit.errors import TweetNotAvailable as _TweetNotAvailable
from twikit.tweet import tweet_from_data as _tweet_from_data
from twikit.utils import Result as _Result, find_dict as _find_dict
from twikit.x_client_transaction import transaction as _t

# New format: ,1234:"ondemand.s"  with a separate  ,1234:"<hash>"  mapping.
_NEW_FILE_REGEX = re.compile(r',(\d+):["\']ondemand\.s["\']')
_NEW_HASH_PATTERN = r',{}:"([0-9a-f]+)"'
_NEW_INDICES_REGEX = re.compile(r"\[(\d+)\],\s*16")

# Old format (pre-2026-03-18): "ondemand.s":"<hash>"  and  (a[NN], 16)
_OLD_FILE_REGEX = _t.ON_DEMAND_FILE_REGEX
_OLD_INDICES_REGEX = _t.INDICES_REGEX


def _resolve_filename(response_str: str) -> str | None:
    """Return the ondemand.s file hash, trying the new layout then the old one."""
    m = _NEW_FILE_REGEX.search(response_str)
    if m:
        hm = re.search(_NEW_HASH_PATTERN.format(m.group(1)), response_str)
        if hm:
            return hm.group(1)
    m = _OLD_FILE_REGEX.search(response_str)
    if m:
        return m.group(1)
    return None


async def _get_indices(self, home_page_response, session, headers):
    response = self.validate_response(home_page_response) or self.home_page_response
    response_str = str(response)

    filename = _resolve_filename(response_str)
    key_byte_indices: list[str] = []
    if filename:
        url = (
            "https://abs.twimg.com/responsive-web/client-web/"
            f"ondemand.s.{filename}a.js"
        )
        file_response = await session.request(method="GET", url=url, headers=headers)
        text = str(file_response.text)
        # New indices pattern captures group(1); old one captures group(2).
        for item in _NEW_INDICES_REGEX.finditer(text):
            key_byte_indices.append(item.group(1))
        if not key_byte_indices:
            for item in _OLD_INDICES_REGEX.finditer(text):
                key_byte_indices.append(item.group(2))

    if not key_byte_indices:
        raise Exception("Couldn't get KEY_BYTE indices")
    values = list(map(int, key_byte_indices))
    return values[0], values[1:]


# legacy key -> (path to its new home in `data`, benign default)
_USER_LEGACY_BACKFILL = {
    "created_at": (("core", "created_at"), ""),
    "name": (("core", "name"), ""),
    "screen_name": (("core", "screen_name"), ""),
    "profile_image_url_https": (("avatar", "image_url"), ""),
    "location": (("location", "location"), ""),
    "description": (("profile_bio", "description"), ""),
    "pinned_tweet_ids_str": (None, []),
    "verified": (("verification", "verified"), False),
    "possibly_sensitive": (None, False),
    "can_dm": (("dm_permissions", "can_dm"), False),
    "can_media_tag": (("media_permissions", "can_media_tag"), False),
    "want_retweets": (None, False),
    "default_profile": (None, False),
    "default_profile_image": (None, False),
    "has_custom_timelines": (None, False),
    "followers_count": (None, 0),
    "fast_followers_count": (None, 0),
    "normal_followers_count": (None, 0),
    "friends_count": (None, 0),
    "favourites_count": (None, 0),
    "listed_count": (None, 0),
    "media_count": (None, 0),
    "statuses_count": (None, 0),
    "is_translator": (None, False),
    "translator_type": (None, "none"),
    "withheld_in_countries": (None, []),
}

_orig_user_init = _user.User.__init__


def _dig(data: dict, path: tuple):
    node = data
    for part in path:
        node = node.get(part) if isinstance(node, dict) else None
    return node


def _patched_user_init(self, client, data):
    if isinstance(data, dict):
        data.setdefault("is_blue_verified", False)
        legacy = data.setdefault("legacy", {})
        if isinstance(legacy, dict):
            entities = legacy.setdefault("entities", {})
            if isinstance(entities, dict):
                desc = entities.setdefault("description", {})
                if isinstance(desc, dict):
                    desc.setdefault("urls", [])
            for key, (path, default) in _USER_LEGACY_BACKFILL.items():
                if key not in legacy:
                    value = _dig(data, path) if path else None
                    legacy[key] = default if value is None else value
    _orig_user_init(self, client, data)


# Patch 3 — TweetDetail reply-cursor extraction (2026-07).
# twikit 2.3.3's get_tweet_by_id parses the reply list, then reads the
# "show more replies" cursor as `entry['content']['itemContent']['value']`
# (and per-thread `reply['item']['itemContent']['value']`). X moved that
# `value` out from under `itemContent`, so both raise KeyError('itemContent')
# *after* the replies are built but *before* they're assigned — every
# get_tweet_by_id call throws and the reply list is lost (surfaced as an empty
# comments response). This override mirrors twikit's parsing exactly but reads
# the cursor defensively and guards the empty-entries / missing-focal-tweet
# edge cases. If replies break again, re-diff against twikit's get_tweet_by_id.


def _cursor_value(node):
    """Cursor value under the new shape (direct) or the old one (itemContent)."""
    if not isinstance(node, dict):
        return None
    inner = node.get("itemContent")
    if isinstance(inner, dict) and "value" in inner:
        return inner["value"]
    return node.get("value")


async def _get_tweet_by_id(self, tweet_id, cursor=None):
    response, _ = await self.gql.tweet_detail(tweet_id, cursor)

    if "errors" in response:
        raise _TweetNotAvailable(response["errors"][0]["message"])

    found = _find_dict(response, "entries", find_one=True)
    entries = found[0] if found else []
    reply_to = []
    replies_list = []
    related_tweets = []
    tweet = None

    for entry in entries:
        entry_id = entry.get("entryId", "")
        if entry_id.startswith("cursor"):
            continue
        tweet_object = _tweet_from_data(self, entry)
        if tweet_object is None:
            continue

        if entry_id.startswith("tweetdetailrelatedtweets"):
            related_tweets.append(tweet_object)
            continue

        if entry_id == f"tweet-{tweet_id}":
            tweet = tweet_object
        elif tweet is None:
            reply_to.append(tweet_object)
        else:
            replies = []
            sr_cursor = None
            show_replies = None
            items = (entry.get("content") or {}).get("items") or []
            for reply in items[1:]:
                reply_entry_id = reply.get("entryId", "")
                if "tweetcomposer" in reply_entry_id:
                    continue
                if "tweet" in reply_entry_id:
                    rpl = _tweet_from_data(self, reply)
                    if rpl is not None:
                        replies.append(rpl)
                if "cursor" in reply_entry_id:
                    sr_cursor = _cursor_value(reply.get("item"))
                    show_replies = _partial(self._show_more_replies, tweet_id, sr_cursor)
            tweet_object.replies = _Result(replies, show_replies, sr_cursor)
            replies_list.append(tweet_object)

            display_type = _find_dict(entry, "tweetDisplayType", True)
            if display_type and display_type[0] == "SelfThread":
                tweet.thread = [tweet_object, *replies]

    if tweet is None:
        # No focal tweet in the conversation (e.g. a retweet id resolves to the
        # original). Let the caller degrade to full text with no comments.
        raise _TweetNotAvailable("focal tweet not found in conversation")

    if entries and entries[-1].get("entryId", "").startswith("cursor"):
        reply_next_cursor = _cursor_value(entries[-1].get("content"))
        _fetch_more_replies = _partial(self._get_more_replies, tweet_id, reply_next_cursor)
    else:
        reply_next_cursor = None
        _fetch_more_replies = None

    tweet.replies = _Result(replies_list, _fetch_more_replies, reply_next_cursor)
    tweet.reply_to = reply_to
    tweet.related_tweets = related_tweets
    return tweet


def apply() -> None:
    _t.ClientTransaction.get_indices = _get_indices
    _user.User.__init__ = _patched_user_init
    _Client.get_tweet_by_id = _get_tweet_by_id


apply()
