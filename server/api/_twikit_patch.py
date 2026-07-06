"""Runtime patch for twikit's transaction-key extraction.

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
"""
import re

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


def apply() -> None:
    _t.ClientTransaction.get_indices = _get_indices


apply()
