"""Aggregate reads narrowed to the channels the caller may see (4 Nov 2026 audit).

The guard middleware sets request.state.visible_channels for a channel-scoped API token and for a teammate whose channel
matrix hides some channels (None = every channel). List routes in teams.FILTERED_LISTS are filtered after the fact, but
aggregate reads (insights and its CSV, reports and their CSV / PDF / Markdown, best times, demographics, stories,
takeaways, comment insights, hashtags, the home summary, agent page context, contacts) total across channels, so they
filter at the query instead: otherwise another channel's captions, usernames, follower counts and totals leak.

    channel_id = narrow(request, channel_id)   # a comma list the route's own query already understands
    qry = only(qry, PostTarget.channel_id, visible(request))
"""
from __future__ import annotations

NO_CHANNEL = "__no_visible_channel__"   # matches no channel: a caller who may see none gets empty aggregates, never all


def visible(request) -> set[str] | None:
    """Channel ids this request may read, or None for every channel."""
    vis = getattr(getattr(request, "state", None), "visible_channels", None)
    return None if vis is None else set(vis)


def narrow(request, channel_id: str | None) -> str | None:
    """The channel_id filter to use: unchanged for an unrestricted caller; otherwise the asked channels it may see (all
    of its visible channels when none were asked), as a comma list. Never empty for a restricted caller."""
    vis = visible(request)
    if vis is None:
        return channel_id
    asked = [c.strip() for c in str(channel_id or "").split(",") if c.strip()]
    keep = [c for c in asked if c in vis] if asked else sorted(vis)
    return ",".join(keep) or NO_CHANNEL


def ids(channel_id: str | None) -> list[str] | None:
    """A comma list as ids (None when there is no filter)."""
    return [c for c in str(channel_id).split(",") if c] if channel_id else None


def only(qry, column, vis):
    """qry limited to rows whose channel column is visible (unchanged when vis is None)."""
    return qry if vis is None else qry.where(column.in_(sorted(vis) or [NO_CHANNEL]))
