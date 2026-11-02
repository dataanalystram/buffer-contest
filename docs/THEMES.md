# Themes, templates and brand kits

How HuhQueue turns one brand kit into every video theme, carousel, thumbnail and end card, and how a new user's kit
restyles all of it. Code paths are given so this stays checkable.

## 1. The layers

```
brand kit (brand_kits.py, one ACTIVE per workspace)
   │  activate() ──► BrandStudio.save()  (brand_studio.py: the one place renderers read the brand from)
   ▼
┌──────────────────────────────┬──────────────────────────────────────────────┐
│ still templates              │ video looks                                  │
│ studio/templates.py          │ studio/media/styles.py                       │
│  front_card (carousel cover, │  4 base styles  (STYLE_PRESETS)              │
│  thumbnail), end_card,       │  17 themes on top (THEMES), each extends a   │
│  card STYLES (bold, glow,    │  base layout                                 │
│  grid, minimal ...)          │  apply_palette(brand, cfg, keep_brand)       │
│                              │  theme_cards.py (title + facts cards),       │
│                              │  captions.py (ASS caption styles)            │
└──────────────────────────────┴──────────────────────────────────────────────┘
```

### 1.1 The four base styles (`studio/media/styles.py`, `STYLE_PRESETS`)

Each base style is a complete edit: how the footage sits (`fill`: `fill`, `panel`, `blurpanel`), the caption engine
(`captions`), whether the best moment is auto-clipped (`autoclip`, `clip_range`), the cover and facts card lengths, the
music mood and the transition.

| id | gallery label | what it is |
|---|---|---|
| `opus` | Opus-style clip | best 20-45 s moment, full screen, big word-by-word captions |
| `huhbyte_news` | **Newsroom** | branded hook cover, the full video in a panel, sentence captions, facts card |
| `minimal` | Minimal explainer | blurred backdrop, framed video, boxed sentence captions, key-points card |
| `hype` | Hype | white flash in, one giant word at a time, 12-30 s |

The Newsroom style keeps the id `huhbyte_news` because stored runs, REST callers and MCP agents already send it.
`newsroom`, `news` and `huhbyte` all resolve to it (`styles.ALIASES`), and "newsroom look", "news layout" are
recognised in prompts (`styles.explicit`). Only the label and descriptions are neutral; nothing that callers send changed.

### 1.2 The 17 themes (`THEMES`)

A theme is a full look built **on top of** one base layout (`"base": "opus" | "huhbyte_news" | "minimal" | "hype"`). It
adds:

- `grade`: an FFmpeg filter graph applied to the footage only, never to type;
- `fx`: picture effects (`grain:N`, `vignette`, `flicker`, `weave`, `scanlines`, `curve`, `misreg`, `tiltshift`, `sweep`,
  `letterbox`, `clock`, `timecode`, `botfade`), built by `footage_chain()` and `compose._main_graph`;
- `palette`: the theme's own ground, text, accent, muted and on-accent colours (`None` = keep the brand's colours, e.g.
  `screencast`);
- `captions`: a caption style id from `studio/media/captions.py` (`slam`, `strap`, `serif`, `keynote`, `swiss`, `kino`,
  `riso`, `mono`, `doc`, `count`, `crt`, `sign`, `telop`, `film`, ...);
- `card` and `chrome`: the title/facts card layout and on-screen decorations in `studio/media/theme_cards.py`;
- `fonts`: optional font files (`head_font`, `bold_font`, `body_font`) that replace the brand's for this look;
- `transition` (`cut`, `flash`, `fade`, `glitch`), `music`, `cover_secs`, optional `fill`, `gate`, `tall_aspect`;
- `words` / `content`: regexes the planner uses to pick the theme from a prompt or from the story (`pick_theme`).

`styles.config(id)` merges a theme over its base preset, so the compositor always gets one flat dict. The themes are
`caption_news, news_package, newsreel, dark_keynote, glass_studio, swiss, kino, silent_film, risograph, hologram_hud,
screencast, investigation, countdown, crt, tilt_shift, game_show, cinematic` (16 adapted from lemomo-ai/lemo-opuscar,
MIT, see NOTICE; `cinematic` is our own). The gallery metadata (category, one-line description, typical length,
popularity) is `GALLERY`; `describe_themes()` serves it to the UI, REST and MCP.

### 1.3 `apply_palette` vs `keep_brand`

```python
styles.apply_palette(brand, cfg, keep_brand=False)
```

- Always kept from the brand: logo, name, @handle (a theme never removes who posted it).
- Taken from the theme: `bg`, `text`, `muted`, `on_accent`, and the theme's `fonts` when it has them.
- `accent`: the theme's accent when `keep_brand=False` (the theme's look wins, e.g. the honey yellow of Caption News);
  the **brand's** accent when `keep_brand=True` (the theme's ground and type, the brand's colour).

The base styles are not themes (`cfg["theme"] is None`), so they use the brand dict as is: brand ground, brand text,
brand accent, brand fonts.

Where each is used:

| caller | keep_brand |
|---|---|
| gallery preview (`brand_kits.theme_cards_for`) | `True` |
| video render (`compose.compose(..., brand_colors=spec.brand_colors)`) | `False` unless the job asks for `brand_colors` |

(See "Known gaps": the preview and the default render disagree on the accent inside a theme.)

### 1.4 Still templates (`studio/templates.py`)

`templates.Brand(kit, logo)` resolves a kit once: colours to RGB, `HEAD_FONTS` / `BODY_FONTS` keys to font files,
the logo to an image. `front_card(b, ratio, kicker, title, sub)` draws the carousel cover (4:5) and the thumbnail
(16:9); `end_card(b, ratio)` draws the end card. Both use the kit's card `STYLES` (`bold`, `glow`, `grid`, `minimal`,
...; `front_card.style`, `end_card.style`) unless an image was uploaded for that card. Headlines follow
`headline_case` (`upper` for Anton and Doto, `title` otherwise).

## 2. Brand kits (`studio/brand_kits.py`)

A workspace keeps up to 12 kits in `brand_kit_library`; one is active (`extras.active_kit`). The builder's field names
map to the stored kit like this:

| builder | stored kit | renderers |
|---|---|---|
| Background | `colors.primary` | `bg` (the ground) |
| Primary | `colors.accent` | `accent` |
| Accent | `colors.secondary` | `extras.secondary` |
| Text | `colors.text` | `text` (raised to contrast >= 3 on the ground by `normalize`) |
| Heading font | `font` | `head_font` file via `templates.HEAD_FONTS` |
| Body font | `body_font` | `body_font` file via `templates.BODY_FONTS`; `bold_font` derived |
| Caption style | `caption_style` (`opus`, `news`, `minimal`, `hype`) | default look of captioned video |
| End card text / CTA | `end_card.line` / `end_card.cta` | `end_line`, end card CTA |

- `normalize(body, base)` validates builder or renderer-shaped fields into the stored kit and fills defaults (ground
  `#111114`, Inter, `opus` captions, `glow` cards on a dark ground, `minimal` on a light one).
- `compose_brand(kit, logo_path)` builds the video compositor's brand dict with every key set (name, @handle, accent,
  ground, text, a muted tone mixed 40 % from text to ground, logo, head/body/bold font files, end line, on-accent), so
  nothing falls back to another brand's defaults in the gallery.
- `activate(kit_id)` writes the kit through **`BrandStudio.save`** (name, handle, website, voice, colours, fonts,
  headline case, front/end card styles, hashtags, label, logo), clears the old logo when the kit has none, and stores
  `active_kit` and `caption_style` in the brand extras. Every renderer (carousels, thumbnails, end cards, editorial
  stills, video jobs) reads the brand from there, so nothing else needs to change when a kit is switched.
- `caption_style_for(house, actor)` is the default look for `edit_video` (MCP) when no theme is named.
  `active_caption_style(house, actor)` is the same but `None` until a kit was ever activated; the agent's
  `render_reel(captions=True)` uses it when no style or theme was named, so a captioned Reel follows the kit (a named
  look still wins, and no captions means the plain Reel as before).
- Sample brands (`nothing`, `linear`, `notion`; `studio/sample_brands/*.json`) carry the badge
  "Sample brand, not affiliated". The badge stays only while the kit is still that brand: renaming a sample-derived kit
  (PATCH with a new name, or POST `{sample, name}` with another name) drops it and records `source.derived_from`.

### 2.1 How a new user's kit restyles everything

1. The user fills the builder (or POST `/api/brand-kits` with fields, `{url}` to read their site, or `{sample}`).
2. As they edit, the builder asks `POST /api/brand-kits/preview` for the kit cards, then the theme cards on screen,
   then the rest (section 3). Nothing is stored.
3. **Save** creates or patches the kit with `activate: true` → `activate()` → `BrandStudio.save`.
4. From then on: still templates draw from `templates.Brand(active kit)`; video jobs get `brand_from_kit(kit)` from the
   agent's spec; base styles show the brand's ground, text, accent and fonts; themes show the theme's ground and type
   with the brand's logo, name and handle (and the brand accent where `keep_brand` is on); captioned edits default to
   the kit's caption style.

### 2.2 Example: the Nothing sample kit

Stored (from `studio/sample_brands/nothing.json`, extracted from nothing.tech):

```json
{"name": "Nothing", "handle": "nothing",
 "colors": {"primary": "#000000", "accent": "#D61821", "secondary": "#006479", "text": "#FFFFFF"},
 "font": "dot", "body_font": "inter", "headline_case": "upper",
 "front_card": {"style": "grid"}, "end_card": {"style": "grid", "cta": "Follow Nothing", "line": ""},
 "caption_style": "opus", "label": "Sample brand, not affiliated"}
```

`compose_brand` turns it into:

```text
name "Nothing", handle "@nothing", bg #000000, text #FFFFFF, accent #D61821, muted #999999,
head_font Doto-Black.ttf, body_font Inter-Regular.ttf, bold_font Inter-Bold.ttf, on_accent #FFFFFF
```

What that gives:

- **Opus / Newsroom / Minimal / Hype**: black ground, white Doto (dot-matrix) uppercase hook, Nothing red `#D61821`
  for the spoken word, the pill and the bars, the Nothing logo and @nothing.
- **Swiss** (`keep_brand=True` in the gallery): the theme's light `#EAE9E5` page and ink type with Inter, but the
  signal square in Nothing red instead of Swiss red, the Nothing logo and @nothing.
- **CRT**: amber `#FFB000` phosphor type in VT323 on `#0A0702` (the theme's fonts replace Doto); the accent is Nothing
  red in the gallery, the theme's pale amber in a default render.
- **Carousel cover / thumbnail / end card**: the `grid` card style on black, Doto headline in caps, the red accent,
  "Follow Nothing" on the end card.

Switching the active kit to HuhByte AI (`#0E0E10` ground, lavender `#C9ACFA`, Anton, `news` captions) redraws every one
of these in HuhByte's look with no other change; a user's own kit does the same.

## 3. The live preview (speed)

- `POST /api/brand-kits/preview` takes `part` (`kit` | `themes` | all), optional `ids` (theme ids), `width`, and
  either `kit_id` (a saved, unedited kit) or `kit` + `logo` (the form as edited).
- Every card is cached on its own: key = sha256 of the kit's render fields (`RENDER_FIELDS`: name, handle, colours,
  fonts, headline case, card styles, end card), the logo bytes, the card id, the width, the format and
  `PREVIEW_VERSION`. Memory LRU (400) plus disk (`HUHQ_PREVIEW_CACHE`, default `$TMPDIR/huhq-kit-previews`, 3000 files).
  Bump `PREVIEW_VERSION` when a renderer changes.
- Responses carry URLs (`/api/brand-kits/preview-img/<key>.webp`, WebP q78, `Cache-Control: private, immutable`), not
  base64. `inline: true` returns data URLs; MCP `preview_brand_kit` still stores JPEGs as media.
- Theme cards are drawn at the size shown (240 x 427), not drawn big and shrunk.
- At start-up a background thread draws the two sample kits (Nothing, HuhByte AI), kit cards first
  (`BrandKits.warm`; `HUHQ_PREVIEW_WARM=0` turns it off; tests do).
- The builder shows skeletons at once, asks for the kit cards first, then the theme cards scrolled into view (an
  IntersectionObserver, batched), then the rest in batches of six; edits are debounced 450 ms; only cache misses count
  against the 40 redraws/minute limit.

## 4. Known gaps

- Video jobs build their brand with `studio/media/worker.brand_from_kit`, which maps name, handle, colours, end line
  and logo but **not the kit's fonts**, so a real render of a base style uses the compositor defaults (Anton / Poppins)
  while the gallery shows the kit's fonts.
- The gallery draws themes with `keep_brand=True` (brand accent); a default render uses the theme's accent
  (`brand_colors` false). Pick one rule (or expose it as a kit setting) so the preview is exactly what renders.
- The builder's "Accent" (`colors.secondary`) is stored but no video theme uses it yet.
