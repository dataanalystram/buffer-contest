"""The action registry: ONE list of everything a person can do in HuhQueue.

Every entry names the REST route that implements it, its JSON-Schema input, the token scope it needs and its risk.
From this list HuhQueue generates, at startup and with no codegen files to keep in sync:

  * `GET /api/actions` (catalog, filtered for the caller) and `POST /api/actions/{id}:run` (generic runner),
  * OpenAPI extensions (`x-huhq-action`, `x-huhq-risk`, `x-huhq-scope`) on the existing operations,
  * MCP tools (`mcp_server.py` builds tools/list from the catalog; tool name = id with '.' -> '_'),
  * ⌘K palette entries (the UI fetches the catalog with ?palette=1 and renders a form from the schema),
  * the CLI (`huhqueue actions` / `huhqueue run <id> key=value ...`),
  * `/llms.txt`, `/llms-full.txt` and `/agents`.

Risk classes (AI_NATIVE_PLAN §2.4):
  safe        read only
  write       creates or edits private data (drafts, ideas, tags)
  destructive deletes or disconnects; the command bar always asks for confirmation
  public      visible to the world (schedule, publish, reply); the command bar always asks for confirmation
  human       must be done by a person in the UI (approve / reject); never offered to agents or the LLM
  secret      passwords, app keys, tokens, LLM keys: UI only, never in MCP, palette forms or the LLM catalog
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

RISKS = ("safe", "write", "destructive", "public", "human", "secret")
CONFIRM_RISKS = {"destructive", "public"}
AGENT_HIDDEN = {"human", "secret"}

S = {"type": "string"}
I = {"type": "integer"}
B = {"type": "boolean"}
O = {"type": "object"}
SA = {"type": "array", "items": {"type": "string"}}
WHEN = {"type": "string", "enum": ["queue", "prioritize", "now", "custom", "draft"],
        "description": "queue = next free slot, prioritize = top of queue, now, custom (needs scheduled_at), draft"}


@dataclass
class Action:
    id: str
    title: str
    method: str = ""            # REST method ("" for client-only nav/ui actions)
    path: str = ""              # REST path with {placeholders}; client-only actions use a '#/...' hash in `nav`
    params: dict = field(default_factory=dict)
    required: tuple = ()
    scope: str = "read"
    risk: str = "safe"
    summary: str = ""
    examples: tuple = ()
    mcp: bool = True
    palette: bool = True
    nav: str = ""               # client-only: hash route to open
    fixed: dict = field(default_factory=dict)   # body/query values always sent (e.g. when=draft)
    body: str = ""              # name of a param whose value IS the JSON body (instead of an object of params)
    group: str = ""
    ui: str = ""                # where the UI exposes it (for the inventory / docs)

    @property
    def tool(self) -> str:
        return self.id.replace(".", "_")

    @property
    def path_params(self) -> list[str]:
        return re.findall(r"{(\w+)}", self.path)

    def schema(self) -> dict:
        return {"type": "object", "properties": self.params, "required": list(self.required)}

    def agent_visible(self) -> bool:
        return self.risk not in AGENT_HIDDEN

    def to_dict(self) -> dict:
        return {"id": self.id, "title": self.title, "summary": self.summary or self.title, "group": self.group or self.id.split(".")[0],
                "method": self.method, "path": self.path, "nav": self.nav, "scope": self.scope, "risk": self.risk,
                "confirm": self.risk in CONFIRM_RISKS, "mcp": self.mcp and self.agent_visible(), "palette": self.palette,
                "tool": self.tool, "input_schema": self.schema(), "examples": list(self.examples), "ui": self.ui}

    def request(self, inp: dict) -> tuple[str, str, dict | None, dict | None]:
        """(method, path, query, json) for this input."""
        inp = {**(inp or {})}
        path = self.path
        for p in self.path_params:
            if p not in inp or inp[p] in (None, ""):
                raise ValueError(f"'{p}' is required")
            path = path.replace("{" + p + "}", str(inp.pop(p)))
        rest = {**{k: v for k, v in inp.items() if k in self.params}, **self.fixed}
        if self.body:
            return self.method, path, None, rest.get(self.body) or {}
        if self.method in ("GET", "DELETE"):
            q = {k: (",".join(v) if isinstance(v, list) else v) for k, v in rest.items() if v not in (None, "")}
            return self.method, path, q, None
        return self.method, path, None, rest


def A(*args, **kw) -> Action:
    return Action(*args, **kw)


# ----------------------------------------------------------------------------------------------- the registry
POST_FILTERS = {"status": {**S, "description": "comma-separated: draft,pending_approval,scheduled,processing,needs_action,sent,failed"},
                "channel_id": {**S, "description": "comma-separated channel ids"}, "provider": S,
                "tag": {**S, "description": "comma-separated tags (OR)"}, "q": {**S, "description": "text in caption or note"},
                "post_type": {**S, "description": "post,carousel,reel,story,short,video"}, "campaign": S,
                "start": {**S, "description": "ISO date/time"}, "end": S, "undated": B, "limit": I}
POST_BODY = {"caption": S, "channel_ids": {**SA, "description": "ids, or ['all']"}, "media_ids": SA, "tags": SA,
             "options": {**O, "description": "network options, e.g. {ig_type:'reel', first_comment:'…'}; "
                                             "X threads: {thread: ['post 2', 'post 3']} (or [{text, media_ids}])"},
             "topic_key": {**S, "description": "stable id of the story; never posted twice to one channel"},
             "campaign": S, "note": {**S, "description": "internal note (never published)"}}

ACTIONS: list[Action] = [
    # --- auth & session (human only)
    A("auth.setup", "Set the first password", "POST", "/api/auth/setup", {"password": S}, ("password",), "admin", "secret", ui="Login"),
    A("auth.login", "Log in", "POST", "/api/auth/login", {"password": S}, ("password",), "admin", "secret", ui="Login"),
    A("auth.logout", "Log out", "POST", "/api/auth/logout", scope="admin", risk="secret", ui="Header menu / ⌘K"),
    A("auth.password", "Change password", "POST", "/api/auth/password", {"current": S, "new": S}, ("current", "new"), "admin", "secret", ui="Settings › Account"),
    A("me.get", "Who am I (scopes, role)", "GET", "/api/me", ui="Settings › API"),
    # --- home & notifications
    A("home.get", "Home scorecard, up next and recent comments", "GET", "/api/home", ui="Home"),
    A("notifications.list", "List notifications", "GET", "/api/notifications", {"unread": B}, ui="Header bell"),
    A("notifications.read_all", "Mark all notifications read", "POST", "/api/notifications/read", scope="write", risk="write", ui="Header bell"),
    # --- posts
    A("post.list", "List posts (queue, drafts, sent, calendar range)", "GET", "/api/posts", POST_FILTERS,
      examples=("show my drafts", "list scheduled posts on instagram"), ui="Publish list / Calendar / Sent"),
    A("post.get", "Get one post with issues", "GET", "/api/targets/{id}", {"id": S}, ("id",), ui="Composer (edit)"),
    A("post.events", "Post timeline (sending, sent, failed…)", "GET", "/api/targets/{id}/events", {"id": S}, ("id",), ui="PostCard › Timeline"),
    A("post.attempts", "Publish attempts log (outcome, error, HTTP status, retry-after)", "GET", "/api/targets/{id}/attempts",
      {"id": S}, ("id",), ui="PostCard › Timeline"),
    A("post.create_draft", "Save a draft", "POST", "/api/posts", POST_BODY, ("channel_ids",), "write", "write", fixed={"when": "draft"},
      examples=("draft 'GPT-6 is here' for x",), ui="Composer › Save draft"),
    A("post.schedule", "Schedule or publish a post", "POST", "/api/posts",
      {**POST_BODY, "when": WHEN, "scheduled_at": {**S, "description": "ISO time; channel timezone if no offset"},
       "timezone": S, "override_rate_limit": B}, ("channel_ids",), "publish", "public",
      examples=("schedule the Orgo reel for Friday 7pm on Instagram",), ui="Composer › Add to queue / Schedule / Share now"),
    A("post.update", "Edit or move a post", "PATCH", "/api/targets/{id}",
      {"id": S, **{k: v for k, v in POST_BODY.items() if k != "channel_ids"}, "when": WHEN, "scheduled_at": S, "timezone": S},
      ("id",), "publish", "public", ui="Composer (edit) / Calendar drag"),
    A("post.note", "Edit a post's internal note", "PATCH", "/api/targets/{id}", {"id": S, "note": S}, ("id", "note"), "write", "write",
      ui="Composer › Note / PostCard"),
    A("post.publish_now", "Publish now", "POST", "/api/targets/{id}/publish-now", {"id": S, "override_rate_limit": B}, ("id",), "publish", "public",
      ui="PostCard / Home up next"),
    A("post.delete", "Delete a post", "DELETE", "/api/targets/{id}", {"id": S}, ("id",), "write", "destructive", ui="PostCard › Delete"),
    A("post.duplicate", "Duplicate a post as a draft", "POST", "/api/targets/{id}/duplicate", {"id": S, "channel_ids": SA}, ("id",), "write", "write",
      ui="PostCard › Duplicate"),
    A("post.retry", "Retry a failed post", "POST", "/api/targets/{id}/retry", {"id": S}, ("id",), "publish", "public", ui="PostCard › Retry"),
    A("post.mark_posted", "Mark as posted (notify mode)", "POST", "/api/targets/{id}/mark-posted", {"id": S, "permalink": S}, ("id",), "write", "write",
      ui="PostCard › Mark posted"),
    A("post.refresh_insights", "Refresh a post's insights", "POST", "/api/targets/{id}/refresh-insights", {"id": S}, ("id",), "write", "write",
      ui="PostCard › Refresh"),
    A("post.approve", "Approve a post waiting for approval", "POST", "/api/targets/{id}/approve", {"id": S, "override_rate_limit": B, "note": S}, ("id",),
      "admin", "human", ui="Approvals tab"),
    A("post.reject", "Reject a post waiting for approval", "POST", "/api/targets/{id}/reject", {"id": S, "note": S}, ("id",), "admin", "human",
      ui="Approvals tab"),
    A("post.request_approval", "Submit a draft for approval", "POST", "/api/targets/{id}/request-approval", {"id": S, "when": WHEN, "scheduled_at": S, "note": S},
      ("id",), "write", "write", ui="Composer › Request approval"),
    A("post.bulk", "Bulk delete / move to drafts / requeue / tag", "POST", "/api/bulk",
      {"action": {"type": "string", "enum": ["delete", "draft", "queue", "tag"]}, "ids": SA, "tag": S}, ("action", "ids"), "write", "destructive",
      examples=("delete all failed posts",), ui="Publish list › select"),
    A("post.validate", "Check a post against each network's rules", "POST", "/api/validate", POST_BODY, ("channel_ids",), ui="Composer (live)"),
    A("post.suggest_format", "Suggest the best format per channel", "POST", "/api/suggest-format",
      {"channel_ids": SA, "caption": S, "media_ids": SA}, ("channel_ids",), ui="Composer"),
    A("queue.reorder", "Reorder a channel's queue", "POST", "/api/queue/{channel_id}/order", {"channel_id": S, "ids": SA}, ("channel_id", "ids"),
      "write", "write", ui="Publish list › drag"),
    A("post.revisions", "Version history of a post", "GET", "/api/posts/{post_id}/revisions", {"post_id": S}, ("post_id",), ui="Composer › History"),
    A("post.revision_restore", "Restore an earlier version", "POST", "/api/posts/{post_id}/revisions/{rev}/restore", {"post_id": S, "rev": I},
      ("post_id", "rev"), "write", "write", ui="Composer › History"),
    A("post.comments", "Internal notes thread on a post", "GET", "/api/posts/{post_id}/notes", {"post_id": S}, ("post_id",), ui="Composer › Notes"),
    A("post.comment_add", "Add an internal note (with @mentions)", "POST", "/api/posts/{post_id}/notes", {"post_id": S, "text": S},
      ("post_id", "text"), "write", "write", ui="Composer › Notes"),
    A("campaign.list", "List campaigns", "GET", "/api/campaigns", ui="Filter bar / Campaigns"),
    A("views.list", "List saved views", "GET", "/api/views", ui="Filter bar"),
    A("views.save", "Save the list of saved views", "PUT", "/api/views", {"items": {"type": "array", "items": O}}, ("items",), "write", "write", ui="Filter bar › Save view"),
    # --- media
    A("media.list", "List media", "GET", "/api/media", {"q": S, "folder": S, "tag": S, "kind": S, "unused": B, "duplicates": B}, ui="Library"),
    A("media.upload", "Upload media from an https URL", "POST", "/api/media/from-url", {"url": S, "filename": S}, ("url",), "write", "write",
      ui="Composer / Library › From URL"),
    A("media.upload_files", "Upload media files or a .zip (multipart 'files')", "POST", "/api/media", {"files": {**SA, "description": "multipart file parts"}, "alt_text": S, "crop": {**S, "description": "square = centre-crop images to 400x400 (avatars, thumbnails)"}},
      ("files",), "write", "write", mcp=False, palette=False, ui="Composer / Library › Upload (agents: MCP upload_media or CLI upload)"),
    A("media.update", "Edit media (alt text, title, tags, folder)", "PATCH", "/api/media/{id}",
      {"id": S, "alt_text": S, "title": S, "tags": SA, "folder": S}, ("id",), "write", "write", ui="Library › asset drawer"),
    A("media.edit", "Edit an image (crop, rotate, flip, resize, colours, text) into a new copy", "POST", "/api/media/{id}/edit",
      {"id": S, "rotate": {"type": "number", "description": "degrees clockwise"}, "flip_h": B, "flip_v": B,
       "crop": {**O, "description": "{x, y, width, height} in pixels of the flipped + rotated image"},
       "aspect": {**S, "description": "centre-crop to a ratio when no crop is given: 1:1, 4:5, 9:16, 16:9, 1.91:1, 3:4, 2:3 or W:H"},
       "resize": {**O, "description": "{width, height}; give one to keep the ratio"},
       "brightness": {"type": "number", "description": "1 = unchanged (0-3)"}, "contrast": {"type": "number"}, "saturation": {"type": "number"},
       "texts": {"type": "array", "items": O, "description": "[{text, x, y (0-1, centre), size (0-0.5 of height), color, background, outline}]"},
       "alt_text": S, "preview": {**B, "description": "return a JPEG data URL instead of saving"}},
      ("id",), "write", "write", examples=("crop md_123 to 4:5 for Instagram", "add the text 'GPT-6 is here' to the top of md_123"),
      ui="Composer media › Edit / Library › Edit"),
    A("media.unsplash_search", "Search free Unsplash photos", "GET", "/api/media/unsplash/search",
      {"q": S, "page": I, "orientation": {"type": "string", "enum": ["", "landscape", "portrait", "squarish"]}}, ("q",),
      examples=("find an unsplash photo of a data center",), ui="Library / Composer › Unsplash"),
    A("media.unsplash_import", "Add an Unsplash photo to the library (with photographer credit)", "POST", "/api/media/unsplash/import",
      {"id": {**S, "description": "photo id from media.unsplash_search"}}, ("id",), "write", "write", ui="Library / Composer › Unsplash"),
    A("media.delete", "Delete media", "DELETE", "/api/media/{id}", {"id": S, "force": B}, ("id",), "write", "destructive", ui="Library"),
    A("media.usage", "Where a media item is used", "GET", "/api/media/{id}/usage", {"id": S}, ("id",), ui="Library › asset drawer"),
    A("media.folders", "List media folders", "GET", "/api/media-folders", ui="Library"),
    # --- channels
    A("channel.list", "List connected channels", "GET", "/api/channels", ui="Left rail / Channels"),
    A("channel.get", "Get one channel", "GET", "/api/channels/{id}", {"id": S}, ("id",), ui="Channel page"),
    A("channel.account_info", "Channel account info (collaborators etc.)", "GET", "/api/channels/{id}/account-info", {"id": S}, ("id",), ui="Composer"),
    A("channel.connect", "Start connecting a network (OAuth)", "POST", "/api/connect/{provider}", {"provider": S}, ("provider",), "admin", "write",
      mcp=False, ui="Channels › Connect"),
    A("channel.connect_token", "Connect with a pasted access token", "POST", "/api/connect/{provider}/token", {"provider": S, "access_token": S},
      ("provider", "access_token"), "admin", "secret", ui="Channels"),
    A("oauth.complete", "Finish OAuth from a pasted callback URL", "POST", "/api/oauth/complete", {"url": S}, ("url",), "admin", "secret", ui="Channels"),
    A("channel.update", "Edit channel (timezone, daily limit, pause, name)", "PATCH", "/api/channels/{id}",
      {"id": S, "timezone": S, "daily_limit": I, "paused": B, "display_name": S}, ("id",), "admin", "write",
      examples=("pause instagram",), ui="Channel page › Settings"),
    A("channel.refresh_profile", "Refresh channel profile", "POST", "/api/channels/{id}/refresh-profile", {"id": S}, ("id",), "admin", "write", ui="Channel page"),
    A("channel.refresh_token", "Refresh channel token", "POST", "/api/channels/{id}/refresh-token", {"id": S}, ("id",), "admin", "write", ui="Channel page"),
    A("channel.disconnect", "Disconnect a channel", "DELETE", "/api/channels/{id}", {"id": S}, ("id",), "admin", "destructive", ui="Channel page"),
    A("channel.slots_set", "Set posting times", "PUT", "/api/channels/{id}/slots",
      {"id": S, "slots": {"type": "array", "items": {"type": "object", "properties": {"weekday": I, "time": S}}}}, ("id", "slots"), "admin", "write",
      ui="Channel page › Schedule"),
    A("channel.slots_preset", "Apply a posting-times preset", "POST", "/api/channels/{id}/slots/preset", {"id": S, "preset": S}, ("id", "preset"),
      "admin", "write", ui="Channel page › Schedule"),
    A("channel.slots_get", "Read posting times", "GET", "/api/channels/{id}/slots", {"id": S}, ("id",), ui="Channel page › Schedule"),
    A("channel.history", "Past posts on the network", "GET", "/api/channels/{id}/history", {"id": S, "limit": I, "after": S}, ("id",), ui="Channel › Sent"),
    A("channel.insights", "Account insights", "GET", "/api/channels/{id}/insights", {"id": S, "days": I}, ("id",), ui="Channel › Insights"),
    A("channel.best_times", "Best time to post from your own data", "GET", "/api/insights/best-times", {"channel_id": S, "days": I},
      examples=("when should I post?",), ui="Insights › Best times"),
    # --- channel pages (Publish header, channel settings, queue actions, community by post, channel insights)
    A("channel.settings_get", "Channel settings (posting goal, notifications, link shortening, UTM, days off)", "GET",
      "/api/channels/{id}/settings", {"id": S}, ("id",), ui="Channel › Settings"),
    A("channel.settings_update", "Change channel settings (posting goal, notification default, link shortening, UTM, days off)", "PATCH",
      "/api/channels/{id}/settings", {"id": S, "posting_goal": {**I, "description": "posts per week"}, "notify_default": B,
                                     "link_shortening": B, "utm_enabled": B, "utm_source": S, "utm_medium": S, "utm_campaign": S,
                                     "disabled_days": {"type": "array", "items": I, "description": "weekdays switched off, 0=Mon"}},
      ("id",), "admin", "write", examples=("set my youtube posting goal to 5 a week", "turn on UTM tracking for instagram"),
      ui="Channel › Posting schedule / Settings"),
    A("channel.publish_summary", "Channel publish header: posts sent this week vs goal, timezone, tab counts, empty slots", "GET",
      "/api/channels/{id}/publish-summary", {"id": S, "days": I}, ("id",), examples=("how many posts did I send this week?",),
      ui="Channel › Publish"),
    A("channel.slots_generate", "Generate posting slots (meet my goal / recommended / copy from channel)", "POST",
      "/api/channels/{id}/slots/generate", {"id": S, "mode": {"type": "string", "enum": ["goal", "recommended", "copy"]},
                                           "goal": I, "source_channel_id": S}, ("id", "mode"), "admin", "write",
      examples=("add posting times to meet my goal on tiktok",), ui="Channel › Posting schedule › Generate slots"),
    A("channel.queue_shuffle", "Shuffle a channel's queue", "POST", "/api/channels/{id}/queue/shuffle", {"id": S}, ("id",), "write", "write",
      ui="Channel › Settings › Queue actions"),
    A("channel.queue_clean", "Remove failed posts from a channel", "POST", "/api/channels/{id}/queue/clean-failed", {"id": S}, ("id",),
      "write", "destructive", ui="Channel › Settings › Queue actions"),
    A("channel.drafts_delete", "Delete all drafts on a channel", "POST", "/api/channels/{id}/queue/delete-drafts", {"id": S}, ("id",),
      "write", "destructive", ui="Channel › Settings › Queue actions"),
    A("channel.queue_empty", "Empty a channel's queue", "POST", "/api/channels/{id}/queue/empty", {"id": S}, ("id",), "write", "destructive",
      ui="Channel › Settings › Queue actions"),
    A("channel.community", "Channel comments by post or as a list", "GET", "/api/channels/{id}/community",
      {"id": S, "view": {"type": "string", "enum": ["posts", "list"]}, "sort": {"type": "string", "enum": ["newest", "unanswered"]},
       "status": S, "show_all": B, "q": S, "kind": {"type": "string", "enum": ["comment", "mention", "all"]}}, ("id",), ui="Channel › Community"),
    A("channel.comments_bulk_resolve", "Resolve many comments (ids, one post, or all)", "POST", "/api/channels/{id}/comments/bulk-resolve",
      {"id": S, "ids": SA, "media_external_id": S, "all": B, "only_status": S}, ("id",), "comments", "write",
      examples=("resolve every comment on instagram",), ui="Channel › Community › Bulk resolve"),
    A("channel.mentions", "Mentions of a channel: posts that tagged it (where the network supports it)", "GET", "/api/channels/{id}/mentions",
      {"id": S, "status": S}, ("id",), examples=("who tagged us on instagram?",), ui="Channel › Community › Mentions"),
    A("channel.mentions_sync", "Pull new mentions (posts that tagged the account)", "POST", "/api/channels/{id}/mentions/sync", {"id": S},
      ("id",), "comments", "write", ui="Channel › Community › Mentions › Refresh"),
    A("channel.grid", "Instagram planned grid: queued posts above the live profile grid", "GET", "/api/channels/{id}/grid",
      {"id": S, "live": I}, ("id",), examples=("show my planned instagram grid",), ui="Channel › Publish › Grid"),
    A("comment.bulk", "Bulk resolve, reopen or reply (text or a saved reply) to many comments", "POST", "/api/comments/bulk",
      {"ids": SA, "action": {"type": "string", "enum": ["resolve", "reopen", "reply"]}, "text": S,
       "saved_reply_id": {**S, "description": "id from the saved replies library; {author} is filled in"}},
      ("ids", "action"), "comments", "public", examples=("reply thanks to all open comments",), ui="Community › select › Bulk actions"),
    A("comment.suggest_reply", "AI reply suggestions for a comment (your AI provider, or quick replies)", "POST", "/api/comments/suggest-reply",
      {"comment_id": S, "tone": {"type": "string", "enum": ["friendly", "professional", "playful", "short"]}}, ("comment_id",),
      "comments", "safe", ui="Community › AI reply"),
    A("comment.start_post", "Turn a comment into a post (quote + credit; optional draft)", "POST", "/api/comments/start-post",
      {"comment_id": S, "create_draft": B, "channel_ids": SA}, ("comment_id",), "write", "write",
      examples=("turn that comment into a post",), ui="Community › Start a post"),
    A("channel.insights_summary", "Channel insights vs the previous period (7d / 30d / mtd / custom), takeaways, per-post",
      "GET", "/api/channels/{id}/insights/summary",
      {"id": S, "range": {"type": "string", "enum": ["7d", "30d", "mtd", "custom"]}, "start": S, "end": S}, ("id",),
      examples=("how did youtube do this month?",), ui="Channel › Insights"),
    A("channel.insights_export", "Channel insights CSV (posts or daily comparison)", "GET", "/api/channels/{id}/insights/export.csv",
      {"id": S, "range": S, "start": S, "end": S, "kind": {"type": "string", "enum": ["posts", "daily"]}}, ("id",), ui="Channel › Insights › Export"),
    A("channel.insights_share", "Turn channel insights into a post (caption, optional draft)", "POST", "/api/channels/{id}/insights/share",
      {"id": S, "range": S, "start": S, "end": S, "create_draft": B}, ("id",), "write", "write", ui="Channel › Insights › Share as post"),
    # --- community
    A("comment.sync", "Pull new comments", "POST", "/api/comments/sync", {"channel_id": S}, scope="comments", risk="write",
      examples=("sync comments",), ui="Community › Sync now"),
    A("comment.list", "List comments", "GET", "/api/comments",
      {"status": S, "channel_id": S, "q": S, "author": S, "flagged": B, "assignee": S,
       "kind": {"type": "string", "enum": ["comment", "mention", "all"]}}, ui="Community"),
    A("comment.reply", "Reply to a comment", "POST", "/api/comments/{id}/reply", {"id": S, "text": S}, ("id", "text"), "comments", "public",
      ui="Community › Reply"),
    A("comment.resolve", "Mark a comment done", "POST", "/api/comments/{id}/resolve", {"id": S}, ("id",), "comments", "write", ui="Community › Done"),
    A("comment.reopen", "Reopen a comment", "POST", "/api/comments/{id}/reopen", {"id": S}, ("id",), "comments", "write", ui="Community"),
    A("comment.update", "Flag / assign a comment", "PATCH", "/api/comments/{id}", {"id": S, "flagged": B, "assignee": S, "status": S}, ("id",),
      "comments", "write", ui="Community › Flag / Assign"),
    # --- contacts
    A("contact.list", "List contacts (commenters)", "GET", "/api/contacts", {"q": S, "segment": S, "tag": S, "assignee": S}, ui="Contacts"),
    A("contact.get", "Contact profile with history", "GET", "/api/contacts/{id}", {"id": S}, ("id",), ui="Contacts › profile"),
    A("contact.update", "Edit contact (notes, tags, assignee)", "PATCH", "/api/contacts/{id}",
      {"id": S, "notes": S, "tags": SA, "assignee": S, "name": S}, ("id",), "comments", "write", ui="Contacts › profile"),
    A("contact.segments", "List contact segments", "GET", "/api/contact-segments", ui="Contacts"),
    # --- ideas
    A("idea.list", "List ideas", "GET", "/api/ideas", ui="Ideas"),
    A("idea.create", "Create an idea", "POST", "/api/ideas", {"title": S, "body": S, "column": S, "tags": SA, "media_ids": SA}, ("title",),
      "write", "write", examples=("new idea: explain MCP in 30 seconds",), ui="Ideas › New"),
    A("idea.update", "Edit or move an idea", "PATCH", "/api/ideas/{id}", {"id": S, "title": S, "body": S, "column": S, "tags": SA, "position": I},
      ("id",), "write", "write", ui="Ideas"),
    A("idea.delete", "Delete an idea", "DELETE", "/api/ideas/{id}", {"id": S}, ("id",), "write", "destructive", ui="Ideas"),
    A("idea.columns", "Rename / add idea columns", "PUT", "/api/idea-columns",
      {"columns": {"type": "array", "items": {"type": "object", "properties": {"id": S, "label": S}}}}, ("columns",), "admin", "write", ui="Ideas"),
    # --- insights & reports
    A("insights.get", "KPIs and top posts", "GET", "/api/insights", {"days": S, "start": S, "end": S, "channel_id": S, "compare": B},
      examples=("insights for the last 7 days",), ui="Insights"),
    A("insights.export_csv", "Export insights CSV (every post in range)", "GET", "/api/insights/export.csv",
      {"days": S, "start": S, "end": S, "channel_id": S}, ui="Insights › Export CSV"),
    A("report.markdown", "Report as markdown (with period comparison)", "GET", "/api/reports/report.md",
      {"days": S, "start": S, "end": S, "channel_id": S}, ui="Reports › Markdown"),
    # --- settings, library, developer
    A("settings.get", "Read settings", "GET", "/api/settings", ui="Settings"),
    A("settings.update", "Change settings (timezone, hashtag limit, CTA, webhook, public URL, theme)", "PUT", "/api/settings",
      {"timezone": S, "hashtag_limit": I, "default_when": S, "default_daily_limit": I, "notify_webhook_url": S, "public_base_url": S,
       "cta_handle": S, "cta_text": S, "theme": S}, (), "admin", "write", ui="Settings › General"),
    A("library.get", "Read a library (hashtag groups, templates, saved replies, tags)", "GET", "/api/library/{kind}",
      {"kind": {"type": "string", "enum": ["hashtag_groups", "templates", "saved_replies", "tags"]}}, ("kind",), ui="Composer / Settings › Library"),
    A("library.set", "Replace a library", "PUT", "/api/library/{kind}", {"kind": S, "items": {"type": "array", "items": O}}, ("kind", "items"),
      "admin", "write", ui="Settings › Library"),
    A("appkeys.get", "Read developer-app keys", "GET", "/api/app-keys", scope="admin", risk="secret", ui="Settings › App keys"),
    A("appkeys.set", "Save developer-app keys", "PUT", "/api/app-keys/{provider}", {"provider": S}, ("provider",), "admin", "secret", ui="Settings › App keys"),
    A("ai.settings_get", "Read AI provider settings", "GET", "/api/ai/settings", scope="admin", risk="secret", ui="Settings › AI"),
    A("ai.settings_set", "Save AI provider settings and key", "PUT", "/api/ai/settings", scope="admin", risk="secret", ui="Settings › AI"),
    A("ai.status", "Which AI provider and model are set up (never the key)", "GET", "/api/ai/status",
      examples=("is AI set up?", "which model do you use?"), ui="Settings › AI"),
    A("ai.test", "Test the AI provider: one tiny prompt, answer and latency", "POST", "/api/ai/test", scope="admin", risk="write",
      ui="Settings › AI › Test connection"),
    A("ai.models", "List the models your AI provider offers", "GET", "/api/ai/models", scope="admin", ui="Settings › AI › Model"),
    A("ai.write", "AI caption writer: write, rephrase, shorten, expand, hook, hashtags or fix (returns options)", "POST", "/api/ai/write",
      {"text": {**S, "description": "the brief (mode=write) or the draft to change"},
       "mode": {**S, "enum": ["write", "rephrase", "shorten", "expand", "hook", "hashtags", "fix"]},
       "tone": {**S, "enum": ["friendly", "professional", "playful", "bold", "informative"]},
       "providers": {**SA, "description": "networks the post is for, e.g. ['x','instagram'] (sets the length limit)"},
       "instructions": S, "count": I}, ("text",), "write", "write",
      examples=("write a post about our launch", "shorten this caption"), ui="Composer › AI writer"),
    A("token.list", "List API tokens", "GET", "/api/tokens", scope="admin", risk="secret", ui="Settings › API"),
    A("token.create", "Create an API token", "POST", "/api/tokens", scope="admin", risk="secret", ui="Settings › API"),
    A("token.update", "Edit an API token", "PATCH", "/api/tokens/{id}", {"id": S}, ("id",), "admin", "secret", ui="Settings › API"),
    A("token.delete", "Revoke an API token", "DELETE", "/api/tokens/{id}", {"id": S}, ("id",), "admin", "secret", ui="Settings › API"),
    A("audit.list", "Audit log", "GET", "/api/audit", {"limit": I}, scope="admin", ui="Settings › Audit"),
    A("tunnel.status", "Public tunnel status", "GET", "/api/tunnel", ui="Settings › Public URL"),
    A("tunnel.start", "Start the public tunnel", "POST", "/api/tunnel/start", {"kind": S, "domain": S}, (), "admin", "write", mcp=False, ui="Settings › Public URL"),
    A("tunnel.stop", "Stop the public tunnel", "POST", "/api/tunnel/stop", scope="admin", risk="write", mcp=False, ui="Settings › Public URL"),
    A("worker.tick", "Run the publisher now", "POST", "/api/worker/tick", scope="publish", risk="write", ui="Composer › Share now"),
    A("worker.status", "Publisher status", "GET", "/api/worker/status", ui="Settings"),
    A("providers.list", "Networks and their capabilities", "GET", "/api/providers", ui="Channels / Composer"),
    A("actions.list", "List every action", "GET", "/api/actions", palette=False, ui="⌘K / agents"),
    # --- client-only (⌘K navigation and UI toggles)
    A("nav.home", "Go to Home", nav="#/home", mcp=False, ui="⌘K"),
    A("nav.queue", "Go to Publish", nav="#/queue", mcp=False, ui="⌘K"),
    A("nav.calendar", "Go to Calendar", nav="#/calendar", mcp=False, ui="⌘K"),
    A("nav.ideas", "Go to Ideas", nav="#/ideas", mcp=False, ui="⌘K"),
    A("nav.community", "Go to Community", nav="#/community", mcp=False, ui="⌘K"),
    A("nav.insights", "Go to Insights", nav="#/insights", mcp=False, ui="⌘K"),
    A("nav.library", "Go to Media library", nav="#/library", mcp=False, ui="⌘K"),
    A("nav.contacts", "Go to Contacts", nav="#/contacts", mcp=False, ui="⌘K"),
    A("nav.settings", "Go to Settings", nav="#/settings", mcp=False, ui="⌘K"),
    A("nav.compose", "New post", nav="#/home?compose=new", mcp=False, ui="⌘K / N"),
    A("ui.theme", "Toggle dark / light theme", nav="theme", mcp=False, ui="⌘K / header"),
    A("ui.template", "Insert a template into the composer", nav="#/home?compose=new&template=1", mcp=False, ui="Composer › Templates"),
]

BY_ID: dict[str, Action] = {}


def register(a: Action) -> Action:
    """Add an action (used by features that live in their own module)."""
    if a.risk not in RISKS:
        raise ValueError(f"bad risk {a.risk}")
    BY_ID[a.id] = a
    if a not in ACTIONS:
        ACTIONS.append(a)
    return a


for _a in list(ACTIONS):
    register(_a)

# Old MCP tool names stay as aliases (mcp_server.py keeps their richer handlers: local file upload, etc.).
LEGACY_TOOLS = {"list_channels": "channel.list", "create_post": "post.create_draft", "schedule_post": "post.schedule",
                "publish_now": "post.publish_now", "upload_media": "media.upload", "list_queue": "post.list",
                "reschedule": "post.update", "get_analytics": "insights.get", "list_comments": "comment.list",
                "reply_comment": "comment.reply", "get_post": "post.get", "suggest_format": "post.suggest_format"}


def get(aid: str) -> Action | None:
    aid = LEGACY_TOOLS.get(aid, aid)
    return BY_ID.get(aid) or next((a for a in ACTIONS if a.tool == aid), None)


def rest_actions() -> list[Action]:
    return [a for a in ACTIONS if a.method]


def mcp_tool_names() -> list[str]:
    return list(LEGACY_TOOLS) + [a.tool for a in ACTIONS if a.method and a.mcp and a.agent_visible()]


def catalog(scopes: list[str] | None = None, palette: bool = False, agent: bool = False) -> list[dict]:
    out = []
    for a in ACTIONS:
        if palette and not a.palette:
            continue
        if agent and not (a.agent_visible() and a.method):
            continue
        if scopes is not None and a.method and not ("admin" in scopes or a.scope in scopes):
            continue
        out.append(a.to_dict())
    return out


def find_route_action(method: str, path: str) -> Action | None:
    for a in ACTIONS:
        if a.method == method and a.path and re.fullmatch(re.sub(r"{\w+}", "[^/]+", a.path), path):
            return a
    return None
