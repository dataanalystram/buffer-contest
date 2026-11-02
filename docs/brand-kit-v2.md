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
