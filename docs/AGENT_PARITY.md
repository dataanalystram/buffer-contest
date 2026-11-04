# Agent parity: every capability, three ways in

The rule of the codebase (CLAUDE.md): what the in-app agent can do, a person's own assistant can do too, through the same
implementation. Each agent tool is ONE method in `studio/agent_tools.py` (editorial tools in `studio/editorial_tools.py`),
used by:

* **In-app**: the Make a post agent (`studio/agent_brain.py`) calls it as a step; the step label is what the run shows.
* **REST**: `POST /api/agent/runs/{id}/tools/{name}` with the tool's arguments as the JSON body (session + CSRF, or a
  bearer agent token). Start a run with `POST /api/agent/runs` (`"brain": "external"` to drive it yourself).
* **MCP**: `post_{name}` with `run_id` plus the same arguments (`studio/agent_brain.tool_specs_for_mcp`, listed by
  `tools/list` on `/mcp`). `make_post` with `brain: "external"` creates the run.

Every call goes through the same guard: the kill switch and the token's daily cap cover every write (SECURITY_RULES 4),
every fetch goes through `studio/netguard.py` (SECURITY_RULES 1), and the free sample is capped (`demo_guard`, rule 3).
Nothing is published: Buffer drafts at most, and only when asked.

## Tools

| In-app step | Tool | REST | MCP | Model use |
|---|---|---|---|---|
| Read your link | `read_url` | `POST /api/agent/runs/{id}/tools/read_url` | `post_read_url` |  |
| Read your upload | `read_upload` | `POST /api/agent/runs/{id}/tools/read_upload` | `post_read_upload` | vision on keyframes when a model is there |
| Read the X post | `x_oembed` | `POST /api/agent/runs/{id}/tools/x_oembed` | `post_x_oembed` |  |
| Search the news | `search_news` | `POST /api/agent/runs/{id}/tools/search_news` | `post_search_news` |  |
| Pull out the facts | `extract_facts` | `POST /api/agent/runs/{id}/tools/extract_facts` | `post_extract_facts` | HuhQueue server AI or your key; rule writer without |
| Render the cover | `render_cover` | `POST /api/agent/runs/{id}/tools/render_cover` | `post_render_cover` | no AI |
| Render the carousel | `render_carousel` | `POST /api/agent/runs/{id}/tools/render_carousel` | `post_render_carousel` | no AI |
| Render the Reel | `render_reel` | `POST /api/agent/runs/{id}/tools/render_reel` | `post_render_reel` | no AI (video worker) |
| Set the end card | `add_end_card` | `POST /api/agent/runs/{id}/tools/add_end_card` | `post_add_end_card` |  |
| Update your brand kit | `edit_brand` | `POST /api/agent/runs/{id}/tools/edit_brand` | `post_edit_brand` |  |
| Re-render one channel | `edit_channel_media` | `POST /api/agent/runs/{id}/tools/edit_channel_media` | `post_edit_channel_media` | no AI |
| Write captions | `write_captions` | `POST /api/agent/runs/{id}/tools/write_captions` | `post_write_captions` | HuhQueue server AI or your key; rule writer without |
| Save Buffer drafts | `save_buffer_drafts` | `POST /api/agent/runs/{id}/tools/save_buffer_drafts` | `post_save_buffer_drafts` | no AI |
| Remember a rule | `remember_rule` | `POST /api/agent/runs/{id}/tools/remember_rule` | `post_remember_rule` |  |
| Find media | `find_media` | `POST /api/agent/runs/{id}/tools/find_media` | `post_find_media` | no AI |
| Make images | `generate_images` | `POST /api/agent/runs/{id}/tools/generate_images` | `post_generate_images` | the configured image model, else brand cards |
| Look at the themes | `list_themes` | `POST /api/agent/runs/{id}/tools/list_themes` | `post_list_themes` | no AI |
| Set the video theme | `set_theme` | `POST /api/agent/runs/{id}/tools/set_theme` | `post_set_theme` | no AI |
| Look at the templates | `list_templates` | `POST /api/agent/runs/{id}/tools/list_templates` | `post_list_templates` | no AI |
| Render a template | `render_template` | `POST /api/agent/runs/{id}/tools/render_template` | `post_render_template` | no AI |
| Plan the weekly recap | `recap_plan` | `POST /api/agent/runs/{id}/tools/recap_plan` | `post_recap_plan` |  |
| Render the recap | `render_recap` | `POST /api/agent/runs/{id}/tools/render_recap` | `post_render_recap` |  |
| Research the company | `research_company` | `POST /api/agent/runs/{id}/tools/research_company` | `post_research_company` | no AI |
| Find the official images | `find_brand_assets` | `POST /api/agent/runs/{id}/tools/find_brand_assets` | `post_find_brand_assets` | no AI |
| Edit the hero image | `hero_edit` | `POST /api/agent/runs/{id}/tools/hero_edit` | `post_hero_edit` | no AI |
| Write the slides | `write_editorial` | `POST /api/agent/runs/{id}/tools/write_editorial` | `post_write_editorial` | **uses HuhQueue server AI** (Claude, budgeted) or your own key; rule writer without |
| Render the editorial carousel | `render_editorial` | `POST /api/agent/runs/{id}/tools/render_editorial` | `post_render_editorial` | no AI (pass `spec` to skip write_editorial) |
| Check everything | `check` | `POST /api/agent/runs/{id}/tools/check` | `post_check` | no AI |

## A post about one company (the ernd carousel)

`research_company` -> `find_brand_assets` -> `hero_edit` -> `write_editorial` -> `render_editorial` -> `write_captions` ->
`check`. Four of the five editorial tools use no model at all. `write_editorial` is the one that writes (HuhQueue's server
AI, budgeted); an MCP client that wants to write the copy itself skips it and passes `spec` to `post_render_editorial`
(the editorial_story fields: `eyebrow, headline, accent[], dek, slides[{eyebrow, blocks[{head, body}], accent[], note,
image}], founders{eyebrow, blocks, accent[], note, names[]}, follow_line`). The spec is checked against the minimum-content
rule (`studio/editorial.check_spec`: 5-7 slides, one idea per slide); the result reports `problems` and `fit_problems`
(text that would clip), so the client rewrites instead of shipping it.

The same carousel is the **editorial_story** template in Brand -> Templates (`GET /api/templates`, `POST
/api/templates/render`, MCP `list_templates` / `render_template`), filled with sample copy until a run gives it real copy.

## No video, no Reel

A Reel is made only from a real video: the person's upload, the source's own clip, or one `find_media` can fetch. Without
one, `render_reel` and `edit_channel_media` never make a slideshow/text-on-gradient video: YouTube and TikTok are
**skipped** with a note (`channel_media[...].format == "skipped"`; `write_captions` leaves them out; `check` lists them under
`skipped`, not as problems, and makes no package), every other channel gets the carousel (the editorial one when it was
rendered), and the plan says "No video found, carousel only".

## Captions

The first line is keyword-first (the company / product people search for, when the story has one) and a caption carries
at most 5 hashtags (`agent_tools.keyword_first`, `agent_tools.cap_hashtags`), whichever writer wrote it.

Tests: `tests/studio/test_editorial_parity.py`.

## Video links (One take, MCP)

A video link the person gives (YouTube, Vimeo, X, or a direct MP4) is fetched by the **same** code everywhere:
`studio.media.download.download_video` on the video worker (InstaBox first, GitHub Actions as its fallback). Make a post's
Reels (`render_reel` → job `media_render`), the weekly recap and One take (`take_analyze`) all call it; One take adds its
own caps (4 GB, 3 h) and keeps the platform's licence flag.

| Way in | Link | Rights |
|---|---|---|
| One take (UI) | the link field takes YouTube / Vimeo / X posts and MP4 links | a checkbox with the rights statement; the request carries `rights_attested: true` |
| REST | `POST /api/takes` with `url` | `rights_attested: true` from a signed-in session; 403 without it |
| MCP | `ingest_video_link` (or `clips_from_video` with `url`) | `needs_user_approval`: the person ticks the statement in Inbox → Approvals; the app calls again with `approval_id` |

Each confirmation is a `link_consents` row (person, link, hash, time, statement version, licence hint). An agent token never
writes one. A platform refusal names its reason (age-restricted, private, members-only, unavailable, bot check) and only
then asks for the file. Tests: `tests/studio/test_link_ingest.py`.
