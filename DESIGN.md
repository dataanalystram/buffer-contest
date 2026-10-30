# HuhQueue design rules

Read this before changing anything in `studio/static/`. The interface follows the **Taste** design system
(canvas: https://claude.ai/artifact/Uz9BgwqHswGA8Api1HZJm4). All values live as CSS variables at the top of
`studio/static/style.css`; use them, never raw hex.

## In one line

One type family on a real scale, one neutral ladder, **one accent with one job**, flat surfaces with hairlines,
every state designed, and motion only when it answers something the person did.

## Tokens

| Role | Variable | Light | Dark |
|---|---|---|---|
| Page | `--paper` | #ffffff | #121314 |
| Cards, inputs, menus | `--raised` | #ffffff | #1b1c1e |
| Bands, table heads, code wells | `--sunken` | #f1f2f2 | #0b0c0d |
| Text | `--ink` / `--muted` | #1a1c1e / #585d62 | #eceeef / #a3a8ad |
| Placeholders, disabled only | `--subtle` | #868b90 | #7b8086 |
| Dividers / control borders | `--line` / `--line-strong` | #e2e4e5 / #7e8388 | #2a2c2f / #6d7278 |
| Accent (rubine) | `--accent`, `--accent-soft` | #c2005b, #fbe7f0 | #ff6aa8, #3d1529 |
| Focus ring | `--focus` | #1f5bd6 | #8ab0ff |
| Status | `--ok` `--warn` `--bad` | teal, amber, red | lighter versions |

**The accent means "a person needs to look at this".** Use it for: the Needs-you badge, the current nav item
(`--accent-soft` behind `--ink` text), agent posts waiting for approval, the single Approve button, links. Never for
decoration, section backgrounds or errors. At most one accent-filled button per view.

Status colour always comes with a word (`.st.ok` prints "Done.", `.st.err` prints "Error.").

## Type

Atkinson Hyperlegible Next (300 / 400 / 600 only), Atkinson Hyperlegible Mono for code and URLs.

| Use | Size / weight |
|---|---|
| Landing headline | 64px / 300, line-height 1.0, tight tracking |
| Section openers, page titles | 48px or 36px / 300 |
| Section headings (`.sec`) | 20px / 600 |
| Card titles | 18px / 600 |
| Body | 16px / 400, line-height 1.5 |
| Dense UI, table cells, buttons | 14px (buttons 600) |
| Captions, timestamps, counters | 12px, `--muted`, `tabular-nums` |

Sentence case everywhere. No all-caps labels.

## Space, radius, depth

- 4px base: 8 / 12 / 16 inside a group, 24 / 32 between groups, 48 between sections in the app, 96 on the landing.
- Radius: inputs 4px, buttons 6px, cards and panels 10px, pills and badges 999px. Containers are rounder than what
  they hold.
- Flat at rest: cards use a 1px `--line` border, no shadow. `--float` shadow only for menus and the toast.

## Components

- **Buttons**: `.btn` (ink fill, the main action), `.btn.accent` (approve only), `.btn2` (outlined secondary),
  `.link` (inline text action). Sizes: default 40px, `.sm` 36px, `.big` 48px. Labels say what happens:
  "Send all to Buffer", "Approve and send to Buffer".
- **Cards**: `.panel`, `.draft`, `.appr`. Header strip, body, then an action row separated by a hairline.
- **Drafts** look like the network they go to: avatar, name, handle, network name as plain text (no brand colours),
  a 2px length bar that turns amber near the limit and red over it, with "Too long:" in words.
- **Empty states** (`.empty`): a bold line saying what will appear, then one sentence on how to get there.
- **Tables** (`table.data`) inside a `.wrap-x` box so they scroll on phones.

## Icons

One outline set, 16px, stroke 1.75, `currentColor` (`.ic`). Icons sit beside labels; icon-only buttons need an
`aria-label`. No emoji, no icons in tinted squares, no Sparkles / Zap / Rocket.

## Playful, not noisy

Play comes from motion that answers an action, and from plain, warm copy. All of it is `transform` / `opacity`,
ease-out, at most 300ms, and switched off under `prefers-reduced-motion`.

- New drafts deal in one after another (`.deal`, staggered by `--i`).
- Buttons and chips press down slightly on click.
- A sent draft settles and fades back; an approved post slides out of the list.
- The Needs-you badge pops in when something arrives.

Do not add scroll-triggered fades, gradients, glows, confetti or bouncing.

## Accessibility (non-negotiable)

- Text contrast 4.5:1, controls 3:1. Visible 2px `--focus` ring with 2px offset on every focusable element.
- Real `<button>`, `<a href>`, `<label for>` on every input; `aria-current="page"` on the active tab.
- Every control has a `data-testid` (`tests/studio/test_studio.py` enforces it).
- Works at 375px wide: the rail becomes a scrolling row, forms stack, tables scroll in their box.

## Never

Cream backgrounds, gradient washes, coloured left-border cards, emoji as icons, more than one accent-filled button
per view, Buffer's logo or colours (the app must not look official; "built on the Buffer API" is the allowed
wording), invented numbers in marketing copy.
