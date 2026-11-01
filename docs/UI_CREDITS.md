# UI credits and licences (premium UX)

Rule for the premium UX (studio/static/landing.*, premium.css, shell.js, connect.js, docs.*): **patterns only.** No code, video, image, brand asset or prompt text was copied from any source below. Every v2 file is written from scratch in vanilla HTML/CSS/JS on the Taste tokens. Studied 31 Oct 2026.

## Our own material (reused)
| What | Source | Licence |
|---|---|---|
| Atkinson Hyperlegible Next + Mono fonts | `../assets/fonts/` (Braille Institute) | SIL OFL 1.1 |
| Taste design tokens | `buffer-contest/DESIGN.md`, `studio/static/style.css` | HuhQueue, MIT |
| App captures: `assets/take*.webp`, `assets/preflight*.webp` | `../assets/product/` (captured from the app) | HuhQueue, MIT |
| `land/inbox-*.webp` | cropped from `buffer-contest/docs/screenshots/inbox-*.png` | HuhQueue, MIT. (The earlier `land/clip1-3.webp` crops were unused and removed on 31 Oct 2026.) |
| `land/planner-*.webp` | re-captured from the real app (sample workspace) by `scripts/premium_shots.py` on 31 Oct 2026 | HuhQueue, MIT |
| Radar stories on the landing | `../radar_data.js` (titles + links from The Verge, Hugging Face blog, Simon Willison), linked to the originals | headlines quoted with links; no article text or images reused |

## motionsites.ai (pattern study only)
Terms: https://motionsites.ai/terms (updated 30 Sep 2026). Prompt text and code are licensed only to paying users and may not be redistributed; the site's videos, backgrounds and branding are its own. We used **none** of them. Every prompt we opened was locked ("Unlock prompt / Go Unlimited"), so no prompt text was read or copied. We looked at the public preview GIFs on the index page.

| Entry | URL | Pattern noted | Used in |
|---|---|---|---|
| EMBER.dsgn | https://motionsites.ai/?prompt=ember-dsgn-hero | one huge heavy word as the visual, split light/dark | hero C (type as the visual) |
| Aetheris Voyage | index card, https://motionsites.ai/ | centred object + small data readouts | not used |
| RIVR | index card | rounded framed inset hero, stats bottom-left | hero A stage frame |
| Impressive Hero | https://motionsites.ai/?prompt=impressive-hero | single light line on dark, headline + chip row | hero C thread (one 2px line) |
| Nexus | index card | ring/portal motif, logo row | not used (no logo wall: Hallmark 46a) |
| Shamoni | index card | small type, floating objects orbiting | not used |
| Digital Epoch | index card | isometric tile field with one accent tile | not used |
| Finlytic AI Agent | index card | product UI rising over a horizon | hero B (sheet settles into the week) |
| AKOR Security | index card | thin accent laser line sweeping the frame | hero C thread |
| NOVA Space Systems | index card | edge-to-edge display type, offset editorial split | hero C type scale |

Browser note: the motionsites detail overlays timed out three times in a row, so per-entry scroll motion was read from the index preview GIFs, not from live pages.

## 21st.dev (pattern study; Google sign-in did not complete)
Sign-in stopped at Google's 2-step "match the number" prompt on the phone (numbers 30, then 78); it did not advance twice, so per the brief we stopped and browsed signed out. Component code pages ("1 free view") were not unlocked. Licences below are as shown on each page.

| Category | Component | URL | Licence | Pattern we took |
|---|---|---|---|---|
| Glass/card | Glow Effect (Motion Primitives) | https://21st.dev/@ibelick/components/glow-effect | MIT | considered, not used (Hallmark 29: no glow) |
| Glass/card | Card Spotlight (Aceternity UI) | https://21st.dev/@manuarora700/components/card-spotlight | MIT (on page) | not used |
| Glass/card | Shine Border (Magic UI) | https://21st.dev/@dillionverma/components/shine-border | MIT | not used; noted as an option for one CTA |
| Glass/card | CardStack (Ruixen UI) | https://21st.dev/@ruixen.ui/components/card-stack | not stated | fanned stack idea only (our carousel fan predates it) |
| Calendar | Fullscreen Calendar | https://21st.dev/@ahmedmayara/components/fullscreen-calendar | MIT | Month view: tall hairline cells, quiet hover |
| Calendar | Event Manager | https://21st.dev/@vaib215/components/event-manager | not stated | Month/Week/Day/List switch in one toolbar (pattern) |
| Calendar | Glass Calendar | https://21st.dev/@ravikatiyar162/components/glass-calendar | not stated | translucent toolbar idea (pattern) |
| Calendar | Apple Calendar Picker | https://21st.dev/@pulseawan/components/apple-calendar-picker | not stated | not used |
| Tabs/segmented | Segmented Control (Klyv-UI) | https://21st.dev/@klyv-ui/components/segmented-control | MIT | radiogroup with roving arrow keys, tight pill, `shell.js` `seg()` |
| Tabs | Tabs (shadcn/ui) | https://21st.dev/@shadcn/components/tabs | MIT | reference for states |
| Tabs | Tabs (Origin UI) | https://21st.dev/@originui/components/tabs | MIT | "floating active" chip used for Inbox filters |
| Data table | Interactive Logs Table | https://21st.dev/@moumensoliman/components/interactive-logs-table-shadcnui | MIT | dense rows, inline status |
| Data table | Data Table (shadcn/ui) | https://21st.dev/@shadcn/components/data-table | MIT | sortable headers with `aria-sort`, row checkboxes, bulk bar (List view) |
| Data table | RuixenContributorsTable | https://21st.dev/@ruixen.ui/components/ruixen-contributors-table | not stated | not used |
| Command palette | Action Search Bar (Kokonut UI) | https://21st.dev/@kokonutd/components/action-search-bar | MIT | row = icon, label, hint, keycap (⌘K in `shell.js`) |
| Command palette | Filters (Andrew Luo) | https://21st.dev/@andrewlu0/components/filters | MIT | two-level filter drill-in (planned for Planner filters) |
| Command palette | Apple Spotlight | https://21st.dev/@samitkapoor/components/apple-spotlight | not stated | not used |
| Pricing | Pricing (Prism UI / Codehagen) | https://21st.dev/@Codehagen/components/pricing | MIT | not used (no pricing on this page) |
| Pricing | Pricing (Ravi Katiyar) | https://21st.dev/@ravikatiyar162/components/pricing | not stated | not used |
| Hero, background | (none recorded) | | | The browser run returned no hero/background picks; Velaris (WebGL background) shows no licence and was not used |


## Other libraries named in APP_UX_PLAN.md (patterns to port, not code)
shadcn/ui (MIT), Radix Primitives (MIT), Origin UI (MIT), Magic UI (MIT), Motion Primitives (MIT), Kokonut UI (MIT), cmdk (MIT), Sonner (MIT), Vaul (MIT). Aceternity UI: per component; check its site licence before copying any code.

## Tools
Hallmark (MIT, Together AI) `work/oss/hallmark`: rules and `sloplint.mjs`. axe-core 4.10.2 (MPL-2.0) for checks. Playwright for screenshots.

## UX v3 (1 Nov 2026): AI marks, Inbox dashboard, landing
Patterns only, again: the Inbox dashboard (stat tiles, a today list, cards with media) and the sub-tab layout follow
patterns seen on linear.app, vercel.com, arc.net, raycast.com and buffer.com's own publish app. No code, image or
asset was copied from them.

| Mark | Source | Terms |
|---|---|---|
| Claude, Claude Code, Anthropic, Cursor, Gemini, Mistral, Meta, Perplexity, OpenRouter, DeepSeek, Qwen, Hugging Face, Ollama, GitHub Copilot | simple-icons 16.36.0 | CC0 for the set; each brand's trademark terms apply |
| OpenAI Blossom (ChatGPT, Codex, OpenAI models) | https://openai.com/brand (file via Wikimedia Commons "OpenAI logo 2025 (symbol)") | OpenAI Marks usage terms: refer to OpenAI services only, unmodified, never more prominent than ours |
| Grok, xAI | xAI press kit https://data.x.ai/logos/xAI_Grok_Assets.zip | https://x.ai/legal/brand-guidelines |
| Groq, unknown apps | monogram fallback in `ai-mark.js` | none used |

Rebuild: `python scripts/build_ai_logos.py <simple-icons package> <openai symbol svg> <xAI assets dir>`.
Landing images: `studio/static/land/made/*.webp`, downsized from our own `studio/static/examples/` renders.

## Make a post v2 (1 Nov 2026): agent workspace, Discover, Recent content, Story scan cards
Still vanilla JS and our own CSS (`studio/static/makepost.js`, `makepost.css`, `pager.js`, `keyframes.js`); no React, no
component code copied. The layout follows the owner's mock; these open-source projects shaped the patterns:

| Pattern | From | Licence |
|---|---|---|
| Chat thread with a user bubble, an agent message, a sticky composer that grows and sends on Enter | assistant-ui (`Thread`, `Composer`) https://github.com/assistant-ui/assistant-ui | MIT |
| Tool-activity trace (collapsible), "Sources" with favicons, streaming status line, plan list | shadcn AI Elements (`Tool`, `Sources`, `Reasoning`, `Loader`) https://github.com/vercel/ai-elements | MIT |
| Prompt input with tool row (attach, model / format select, primary submit), suggestion chips, "Try an example" | prompt-kit (`PromptInput`, `PromptSuggestion`) https://github.com/ibelick/prompt-kit | MIT |
| Stepper, mode cards with a check, segmented filters, news cards with a thumbnail and an open action | 21st.dev community components (MIT-licensed entries only; patterns, no code) https://21st.dev | per component, MIT entries only |

Story pictures come from each story's own og:image, read and re-encoded by our server (`studio/thumbs.py`); when a story
has none, the site's own favicon (or a monogram) is shown. No third-party favicon or image service is used.
