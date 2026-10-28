"""Connector registry: built-ins + plugins from the 'huhqueue.connectors' entry-point group."""
from __future__ import annotations

import logging
from importlib.metadata import entry_points

from .base import (AuthError, BrandRules, ChannelAuth, Connector, ConnectorError, Issue, MediaItem,
                   NotConfigured, PermanentError, Profile, PublishContext, PublishOutcome, RetryableError, Unconfirmed,
                   TokenBundle)
from .instagram import InstagramConnector
from .linkedin import LinkedInConnector, LinkedInPagesConnector
from .tiktok import TikTokConnector
from .x import XConnector
from .youtube import YouTubeConnector

log = logging.getLogger(__name__)

BUILTIN_CONNECTORS: dict[str, type[Connector]] = {
    "instagram": InstagramConnector,
    "tiktok": TikTokConnector,
    "youtube": YouTubeConnector,
    "x": XConnector,
    "linkedin": LinkedInConnector,
    "linkedin_page": LinkedInPagesConnector,
}

# Shown in "Connect channel" as coming soon so the plugin slots are visible.
PLANNED = [
    {"id": "facebook", "name": "Facebook Pages"},
    {"id": "threads", "name": "Threads"},
    {"id": "bluesky", "name": "Bluesky"},
]


def discover(enable_fake: bool = False) -> dict[str, type[Connector]]:
    found = dict(BUILTIN_CONNECTORS)
    try:
        for ep in entry_points(group="huhqueue.connectors"):
            try:
                found[ep.name] = ep.load()
            except Exception as e:  # pragma: no cover - plugin errors must not kill the app
                log.warning("could not load connector plugin %s: %s", ep.name, e)
    except Exception:  # pragma: no cover
        pass
    if enable_fake:
        from .fake import FakeConnector
        found["fake"] = FakeConnector
    return found


__all__ = ["AuthError", "BrandRules", "ChannelAuth", "Connector", "ConnectorError", "Issue", "MediaItem",
           "NotConfigured", "PermanentError", "Profile", "PublishContext", "PublishOutcome", "RetryableError", "Unconfirmed",
           "TokenBundle", "BUILTIN_CONNECTORS", "PLANNED", "discover"]
