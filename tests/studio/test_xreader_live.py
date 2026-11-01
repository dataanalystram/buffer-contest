"""Opt-in live smoke of each free X reader layer on one real public post (HQ_LIVE_X=1). Skipped in CI: these are
unofficial public endpoints and CI must never depend on them. Asserts status only; prints nothing from the post."""
import os

import pytest

from studio import xreader

pytestmark = pytest.mark.skipif(os.environ.get("HQ_LIVE_X") != "1", reason="live X smoke: set HQ_LIVE_X=1")
SID, HANDLE = "2116198463847408066", "AarushYadav47"


def test_live_oembed():
    assert xreader.layer_oembed(HANDLE, SID)["text"]


def test_live_syndication():
    got = xreader.layer_syndication(SID)
    assert got["text"] and got["videos"]


def test_live_fxtwitter():
    got = xreader.layer_fxtwitter(SID)
    assert got["text"] and got["videos"]


def test_live_cascade():
    got = xreader.read(HANDLE, SID)
    assert got["text"] and got["videos"]
