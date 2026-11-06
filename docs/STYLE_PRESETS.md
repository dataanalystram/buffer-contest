# Style Ledger presets

Built-in starting rules for people whose AI (or whose own drafts) don't yet know hooks, retention and calls to action.
Pick one in **Brand > Style Ledger > Start from a preset** (preview and edit first), or over MCP with `list_style_presets` and
`apply_style_preset` (two steps: preview, then `confirm_token`). Code: `studio/style_presets.py`.

A preset writes ordinary ledger rules:

| Kind of rule | What it does |
|---|---|
| Guidance (`guideline`): hook formulas, first line, structure, on-screen text, CTA, video length | Goes into the AI drafter's prompt with the other ledger rules; shown under **Hooks and structure** and **Calls to action**. Never auto-applied to text. |
| Caption shape (`guideline`, area `style`) | The free built-in drafter (no model, no key) writes captions in the preset's shape: hook first line under 125 characters, one idea, the payoff, one CTA, hashtags within the cap. |
| Banned phrases (`banned_words`) | Removed from every caption and checked. |
| Hashtag cap per network (`max_hashtags`, scoped) | Extra hashtags removed and checked. Instagram is never above 5. |
| Caption length per network (`max_length`, scoped) | Captions are trimmed to it and checked. |
| News only: no exclamation marks, credit the source on the image | Applied and checked like any rule. |

Re-applying a preset replaces the rules it wrote before (never duplicates). Applying another preset switches the first
preset's rules off (kept, off). The person's own rules are left alone, except a single-valued rule of the same type on the
same networks, which the newer preset rule replaces (the ledger's usual "newest wins"; the reply lists every rule switched off).

## The five presets

| Preset | Shape | Hook formulas (paraphrased) | Hashtags IG / X / LinkedIn | Caption length IG / X / LinkedIn |
|---|---|---|---|---|
| Hook-first short-form | Hook in 1-2 s, pattern interrupt every 2-4 s, one idea, payoff, one CTA, abrupt end | surprising number first; result first; common belief then twist; the question the video answers; what the viewer loses | 5 / 2 / 3 | 300 / 260 / 900 |
| Educator / explainer | Problem, 3-5 numbered steps, recap, "save this" | the common mistake; "{topic} in {n} steps"; why it happens; "before you…" | 5 / 2 / 3 | 700 / 260 / 1300 |
| Founder build-in-public | Real number or date up front, what happened, one thing that went wrong, lesson, a question | "Day {n} of building…"; "we shipped X, here's what broke"; the honest numbers; the decision I'd change | 3 / 1 / 3 | 600 / 260 / 1300 |
| News, fast and accurate | Attributed fact first, why it matters, what's next, the source; no hype | who did what; the figure that matters; what changes for you | 3 / 1 / 2 | 600 / 260 / 900 |
| Podcast clip | Cold open on the strongest line, one moment per clip, speaker named, full-episode CTA | strongest sentence mid-thought; guest + claim; question then answer | 4 / 1 / 2 | 400 / 260 / 900 |

Lengths and hashtag counts other than the platform caps below are **house defaults**: starting points, editable before and
after applying.

## Where each principle comes from

Principles are paraphrased; no text is copied. Fetched 6 Nov 2026.

- **Instagram allows at most 5 hashtags per post or Reel**, and Instagram recommends fewer, targeted tags. Instagram @creators
  announcement (18 Dec 2025), reported by Social Media Today:
  https://www.socialmediatoday.com/news/instagram-implements-new-limits-on-hashtag-use/808309/
- **X recommends no more than 2 hashtags per post.** X Help Center, "How to use hashtags":
  https://help.x.com/en/using-x/how-to-use-hashtags
- **First line under 125 characters.** Meta recommends 125 characters of primary text for most placements and says longer
  text may be truncated; keep copy to 1-3 lines and say what you want people to do. Meta Business Help Centre, "Creative best
  practices for text in ads": https://www.facebook.com/business/help/223409425500940 (ads guidance, used here as the closest
  official figure for the visible part of a caption).
- **Hook / body / close; hook early; text overlays and captions; one strong CTA.** TikTok Creative Codes: three-part
  structure (hook, body, close), most ad-recall impact in the first six seconds, text overlays increase view time, end on a
  clear call to action, faster scene changes draw viewers in:
  https://www.tiktok.com/business/en-US/blog/creative-best-practices-top-performing-ads
- **The first seconds decide; the intro must deliver what the title promised.** YouTube Help, "Understand your content
  performance for YouTube's recommendation system" (engaging hooks, keep the intro concise, deliver the promise):
  https://support.google.com/youtube/answer/16559650
- **Shorts can run up to three minutes** (the preset video lengths are shorter house defaults). YouTube Help, "Understand
  three-minute YouTube Shorts": https://support.google.com/youtube/answer/15424877
- **Hook, retain, reward; one call to action at a time; "how I" rather than "how to"; make content for strangers.** Alex
  Hormozi, *$100M Leads*, "Post Free Content" chapters, read by the author on The Game podcast (19 Aug 2023):
  https://podscripts.co/podcasts/the-game-with-alex-hormozi/part-4-post-free-content-100m-leads-book
- **Match the title/thumbnail promise in the opening, front-load the best content, no dull moments, a satisfying payoff,
  end abruptly.** "How to Succeed in MrBeast Production" (the MrBeast production memo, public copy on archive.org):
  https://archive.org/details/how-to-succeed-in-mr-beast-production
- **News: accuracy before speed, verify, attribute, headlines and teasers must not misrepresent, label opinion.** SPJ Code of
  Ethics (revised 4 Oct 2026): https://www.spj.org/spj-code-of-ethics
- **Background research and constraints** (free-only, no paid models, QC ideas such as hook motion in the first 2 s):
  HuhQueue feature research of 5 Nov 2026 (private working note).

## Before and after (free built-in drafter, no model)

`test_free_drafter_writes_in_the_preset_shape` drafts three example topics with every preset and checks each caption passes
the preset's rules (first line under 125 characters, hashtags within the cap, length, banned phrases, CTA). Examples are in
the PR description.
