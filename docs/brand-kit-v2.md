# Brand kit v2: a website's real style, not just its colours (2 Nov 2026)

**Problem (owner):** pasting ycombinator.com, wisprflow.ai or youtube.com got the logo and colours right, but every kit was
drawn with the same light "minimal" card in Inter, so the site's style never showed. Separately, typed text like "the
Notion template was so good" came out as "the Nothing template".

## What v2 does

1. **URL → design tokens** (`studio/design_system.py`). Engine 1 is the context.dev Styleguide API (a real browser renders
   the page and returns computed styles: mode, colours, h1..p typography, button/card CSS, spacing, font links); it runs
   when `CONTEXT_DEV_API_KEY` is set on the server. Engine 2 (always available, no key) is HuhQueue's CSS reader: linked
   stylesheets with CSS variables resolved, the font-family on h1/h2/heading/hero selectors, Google Fonts links and
   preloaded font files, button border-radius, h1 size and text-transform, framework default colours ignored.
2. **Tokens → DESIGN.md + a generated theme** stored on the kit (`kit.design`, `kit.theme`): layout (bold_news, editorial,
   minimal, playful), light/dark, surface colour, corner radius, headline and body fonts (nearest bundled Google Font:
   EB Garamond, Source Serif 4 and Roboto were added), case, headline scale and accent usage. `GET /api/brand-kits/{id}/design.md`.
3. **Renderers follow the theme** (`studio/layouts.py`): the carousel cover, thumbnail, Reel cover and end card are four
   different compositions. Kits without a theme draw exactly as before; kits saved before v2 get a theme derived from
   their own fonts and colours the first time the kit list is read.
4. **Templates tab**: every kit (the samples, HuhByte AI and your own) is a selectable card; the active one is marked; a
   click activates it and every template preview redraws in it. Saving a kit in the builder updates the list.
5. **Name swap fix**: `captions.fix_names` no longer turns one real name into another ("Notion" → "Nothing"); typed or
   human captions are never "repaired".

## Evaluated, not wired

- **dembrandt** (MIT, Playwright, DTCG tokens + `--design-md`) and **design-md-extractor**: comparable output to context.dev
  but need a headless browser per extraction; would run as a job on the InstaBox worker. Kept as the next step if the
  context.dev credits become a concern.

## Polish (2 Nov 2026, evening)

- **Two sites in one layout family still look different.** Each family has two compositions picked from more signals
  (`design_system.pick_variant`, `theme.variant`): editorial `rules` (header rule + tracked kicker) vs `masthead`
  (centred masthead, the headline on the brand's tinted surface block); minimal `line` (short accent line) vs `bar` (a solid
  accent bar across the top, plain sans: YC / Hacker News). A sans headline over a serif body is minimal, not playful.
  A pale tinted CTA button (Wispr Flow's lavender `#F0D7FF`) becomes the surface; a colour used only on CTA buttons or
  error / strikethrough styles (Wispr's coral) loses to the colour that fills blocks and cards (Wispr's green `#034F46`).
  YouTube's link-blue "text" from context.dev no longer becomes the ink.
- **Logos are sized by their visual bounds** (`templates.logo_box`): transparent or plain-colour padding is trimmed, every
  corner logo gets about the same visual area, long wordmarks may run wider, never under a minimum height.
- **Square post**: laid out bottom up (footer, speaker block, then the quote fitted to the room left), in the theme's
  layout and variant (`layouts.quote`), so a long name never runs into the handle.
- **Pulled quotes**: `templates.fix_spacing` puts back the space lost across tags ("type,just" -> "type, just"); words,
  links, domains and numbers are untouched. Applied at extraction (kit voice) and at render.
- **Templates tab**: "Every template in <kit>" asks `POST /api/brand-kits/preview {kit_id: <active>, part: "templates",
  ids: [one]}` per template, all at once; each card is the template's first slide in that kit, cached server-side (memory
  + disk, like the kit cards) and per kit version in the tab, with placeholders tinted by the kit's ground until each
  arrives. The sample kits' template cards are drawn by the start-up warm-up.
- **Library deletes go to a 7-day Trash** (`library.Library._trash / restore / purge_trash`, `GET /api/library/trash`,
  `POST /api/library/{id}/restore`, Undo in the toast). See the missing-media note below.

### Missing media on 2 Nov (`/healthz` missing_files 9 -> 42)

Not the deploys: media lives in Postgres (Supabase, `media` table, 205 rows), not on Render's ephemeral disk, and the
one-time Supabase copy only runs into an empty store. The audit log shows nine `delete_library_item` calls from
a signed-in human session on the owner's workspace (audit actor "you": the Library UI or a browser session signed in as the owner) between 20:50:54 and 20:51:37 GMT (five forced past the "used in a post that hasn't gone
out" warning) and four more at 12:32 GMT. Each removed the media rows behind ready posts (runs 4, 5, 6, 9, 10 and 20;
42 references). Those bytes are gone (hard delete, no backup on the free plan); from now on a delete is restorable for 7
days. `/healthz` also reports `unique_missing_files` next to `missing_files` (which counts references).
