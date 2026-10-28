"""Routes for the insights extras (services/audience.py): demographics, saved story insights and takeaways across all
channels. Each route has a registry action below, so it also works from MCP, the CLI and the ⌘K bar."""
from __future__ import annotations

from fastapi import Body, FastAPI, HTTPException

from .. import actions as reg
from ..actions import B, S, Action
from ..services import audience


def add_audience_routes(app: FastAPI, core) -> None:
    @app.get("/api/insights/demographics", tags=["insights"])
    def demographics(channel_id: str = "", refresh: bool = False, timeframe: str = "this_month"):
        """Age, gender, country and city of followers and of engaged accounts (Instagram; 100+ followers)."""
        return audience.demographics(core, channel_id or None, refresh=refresh, timeframe=timeframe)

    @app.get("/api/insights/stories", tags=["insights"])
    def stories(channel_id: str = "", days: int = 30):
        """Stories with the metrics saved while they were live (Instagram drops them after 24 h)."""
        return audience.stories(core, channel_id or None, days)

    @app.post("/api/insights/stories/sync", tags=["insights"])
    def stories_sync(payload: dict = Body(default={})):
        return audience.sync_stories(core, channel_id=(payload or {}).get("channel_id") or None)

    @app.get("/api/insights/takeaways", tags=["insights"])
    def takeaways(range: str = "30d"):  # noqa: A002 - query name matches the channel insights route
        try:
            return audience.takeaways(core, range)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    @app.post("/api/insights/takeaways/feedback", tags=["insights"])
    def takeaway_feedback(payload: dict = Body(...)):
        try:
            return audience.save_feedback(core, payload.get("key", ""), payload.get("helpful"))
        except ValueError as e:
            raise HTTPException(400, str(e)) from e


RANGE = {"type": "string", "enum": ["7d", "30d", "90d", "mtd"]}
for _a in (
    Action("insights.demographics", "Audience demographics: age, gender, country, city (followers and engaged)", "GET",
           "/api/insights/demographics", {"channel_id": S, "refresh": B,
                                          "timeframe": {"type": "string", "enum": list(audience.ENGAGED_TIMEFRAMES)}},
           examples=("where are my instagram followers from?", "age split of my audience"), ui="Insights › Audience"),
    Action("insights.stories", "Story insights saved before they expired", "GET", "/api/insights/stories",
           {"channel_id": S, "days": {"type": "integer"}}, examples=("how did my stories do this week?",), ui="Insights › Stories"),
    Action("insights.stories_sync", "Save live stories' insights now", "POST", "/api/insights/stories/sync", {"channel_id": S},
           scope="write", risk="write", ui="Insights › Stories › Refresh"),
    Action("insights.takeaways", "Takeaways across all channels vs the previous period", "GET", "/api/insights/takeaways",
           {"range": RANGE}, examples=("what should I know about this month?",), ui="Insights › Overview"),
    Action("insights.takeaway_feedback", "Mark a takeaway helpful or not", "POST", "/api/insights/takeaways/feedback",
           {"key": S, "helpful": {"type": ["boolean", "null"], "description": "true, false, or null to clear"}}, ("key",),
           "write", "write", ui="Insights › Overview › takeaway"),
):
    reg.register(_a)
