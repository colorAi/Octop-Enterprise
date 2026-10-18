"""tests/unit/test_ulid.py"""

from __future__ import annotations

import re
import time

from octop.infra.utils.ulid import new_short_id, new_ulid


def test_short_id_is_6_char_crockford():
    s = new_short_id()
    assert re.fullmatch(r"[0-9A-HJKMNP-TV-Z]{6}", s)


def test_short_id_custom_length():
    assert len(new_short_id(5)) == 5


def test_ulid_is_26_char_crockford():
    u = new_ulid()
    assert re.fullmatch(r"[0-9A-HJKMNP-TV-Z]{26}", u)


def test_ulids_are_monotonic_within_ms():
    a = new_ulid()
    b = new_ulid()
    assert a != b
    assert a <= b


def test_ulid_timestamp_prefix_recent():
    now_ms = int(time.time() * 1000)
    u = new_ulid()
    alphabet = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
    ts = 0
    for ch in u[:10]:
        ts = ts * 32 + alphabet.index(ch)
    assert abs(ts - now_ms) < 1000
