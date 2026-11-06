# Make a post: intent first (6 Nov 2026)

Live QA found the in-app agent over-acting and Make a post reopening old threads. This note covers why it kept
happening and what now stops it.

## What broke

| Live symptom | Root cause |
|---|---|
| "test" / "hi" ran a news search, then ended on an error | Nothing read the message before the plan. Every first message went to the news plan, which searched `topic or prompt` (the raw prompt as a fallback). A search with no hits raised `ActionError("No recent stories found…")`, and `_go` ended the run as `failed` with that tool text and no Retry. |
| Ram's exact LinkedIn caption became an unrelated IEEE caption with hashtags | `intake.topic_of` only strips a fixed list of instruction words, so "exact only, do not change single character, no hashtags, no extra text" was left as the "topic" and searched. The quoted text was never read as the caption. Every caption also went through the writer (tidy, keyword-first, hashtags, brand rules, link). |
| "Replace / Edit the LinkedIn caption to …" → "I couldn't tell what to change" | `intake.refine` had no caption-edit action, so `_refine` fell through to `remember_rule` and then the generic hint. |
| B3: saved to Buffer by itself | The planner auto-ran `save_buffer_drafts` whenever the prompt contained the word "draft" (Ram's caption text said "text draft"). The in-app brain was also offered the tool. |
| B2: request text as the headline on an uploaded photo, over a gradient, and "every check passes" | `intake.describe` kept clauses like "use the attached uploaded image as the media" (one word, "media", survived `topic_of`). That line became the story title, and the legacy `brand_photo` burned the brief over a `_gradient`. The brand check only looked at ledger rules, so it said every check passes. |
| "Make post" from News / Discover opened the previous thread | `wireMake` called `showTab("agent")` while the old thread was still `mp-in-thread`. `urlFor` deliberately keeps `/threads/<id>` when a thread is open, so the old thread came back and the link went into a hidden composer. |

## Why it kept coming back

Each earlier fix patched one wording at a time (the `INSTRUCTION_RE` / `ORDERS` / `REQUEST_ONLY` lists), but every
path still assumed that a message means "research and post this". No step classified the message, and no test sent
small talk, exact text or an instruction-only prompt. On the UI side, each entry point opened Make a post its own way,
and only the in-tab New post button left the thread.

## Guards now in place

- `studio/intent.py` classifies every first message and thread reply before any tool runs: chat, ask, verbatim or post.
  It also decides the only search query allowed (`search_query`) and reads exact caption edits (`caption_edit`).
- Search is gated three ways: the planner only searches `intent.query` and asks a question when that is empty. The
  in-app brain only gets `search_news` with no link, no upload and a real topic (`Brain._specs_for`). `t_search_news`
  refuses instruction-only queries and linked or uploaded runs on the server, whoever the brain is.
- Exact captions go through `Tools.exact_caption`, which skips every writer step and covers only the named channels.
- Tool failures end in `_calm_fail`: one plain sentence, `result.error.retry_text`, and a Retry button. The next message
  (or Retry) restarts the post in the same thread.
- Nothing reaches Buffer from the agent: `save_buffer_drafts` is never offered to the in-app brain and the planner no
  longer calls it. Only the card's button saves, and the card then shows "Saved to Buffer as draft · <id>" once.
- An uploaded photo is posted as uploaded unless the person asks for an overlay, slides or a template. `brand.content_check`
  fails a post with instruction text or a gradient overlay.
- Every Make a post entry point outside the tab calls `window.hqNewPost(text)`. It aborts the open thread's loads and
  poll, leaves its address and opens a fresh composer.

## Tests

- `tests/studio/test_agent_intent.py` covers: "test", "hi", a plain caption request (no search), "what did OpenAI launch
  today" (search allowed), Ram's exact prompt and both follow-ups (caption equal byte for byte, no search, LinkedIn
  only), an unclear edit (a specific question), the calm search failure with Retry, the server-side search guard, "only
  X" / "no hashtags" / "don't add a link", "drafts" never saving to Buffer, an uploaded photo as uploaded, and the
  content check.
- `tests/studio/test_new_thread_entry_e2e.py` (Chromium, 1280 and 390 px) covers News "Make post" and the Inbox link
  starting a new thread, Back returning to News, and an in-flight old thread load never landing. It fails on main and
  passes on this branch.
