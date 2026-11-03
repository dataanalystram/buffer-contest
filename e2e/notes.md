# PR #73 live E2E notes (sandbox resets: restore from branch notes/e2e-mcp-pr73)
- Merge: main->branch edf8c7c (CI green test+smoke 23:46-00:04 UTC), PR merged e47d8083beb99974032aaf6efd5293b467e880c0 (00:05 UTC). main also has #75 a9974cb.
- Deploy dep-dbkib4oae00c73fnv960 live for e47d808 (00:06 UTC), /healthz 200.
- MCP check PASS: 130 tools, no dups; 5 prompts list+get OK; 6 resources list+read OK.
- Buffer drafts baseline: COUNT 9 (newest 6ae79277b7d22b897a0b22dd).
- Step3 edit_video job 35: queued 5.2s, done ~50s (worker 47s). clip clamped to 6.3s (source is only 6.3s long), output 1080x1920 9.9s (cover 0-0.6, content 0.6-6.9, end card 6.9-9.9). Audio 9.8s. Credit + hook + HuhByte logo + Follow @huhbyte.ai end card OK. Captions auto-off (source has burned captions). No freeze in content (freeze only on static end card).
- BUG: cover title "Screen Studio: polished demos in one click" rendered "SCREEN STUDIO POLISHED DEMOS ONE": covers.hook_words caps at 5 words + drops stopwords even for a client-written cover.title (schema says max ~10 words). Fix loop 1 in progress.
  media: out https://huhqueue-studio.onrender.com/m/eBZf4VBpCnT7pyaxCQi8zj4p.mp4 cover https://huhqueue-studio.onrender.com/m/LHU9Iwclj3Zpx_YbVDsDXIvZ.jpg
