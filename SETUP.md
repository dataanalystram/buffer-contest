# HuhQueue setup

HuhQueue signs in to each network through **your own developer app**. You create the app once,
paste its ID and secret into **Settings → App keys** (or the form that appears when you click
*Connect* on a network that has no keys yet), and click *Connect*. No terminal needed.

Hosted install (InstaBox): **https://8787-agqwhbc22xbwwr7lmn2ja5jobq.instabox.tech**

## Redirect URIs to register

Register these exactly (copy them from the app: they are shown next to every key form):

| Network   | Redirect URI |
|-----------|--------------|
| Instagram | `https://8787-agqwhbc22xbwwr7lmn2ja5jobq.instabox.tech/oauth/instagram/callback` |
| YouTube   | `https://8787-agqwhbc22xbwwr7lmn2ja5jobq.instabox.tech/oauth/youtube/callback` |
| X         | `https://8787-agqwhbc22xbwwr7lmn2ja5jobq.instabox.tech/oauth/x/callback` |

The callback base is chosen in this order: `HUHQ_PUBLIC_BASE_URL`, then Settings → Public media URL (or a running
tunnel), then **the URL you opened HuhQueue on**. So with nothing configured, a local install uses
`http://localhost:8787/oauth/<network>/callback` and a hosted install uses its own https URL, and that URL is kept
as the public media base when an owner or admin signs in over https. If the host URL changes, sign in on the new
URL and re-register the callbacks it shows. You can register the localhost callback and the hosted one in the
same developer app (a network that only accepts https needs the hosted URL or a tunnel).

Privacy policy and terms URLs (Meta and Google ask for them): `<public-url>/privacy` and `<public-url>/terms`.

Meta compliance callbacks (Instagram app → **App settings**; also shown under Settings → App keys → Instagram):

| Meta field | URL |
|---|---|
| Deauthorize callback URL | `https://8787-agqwhbc22xbwwr7lmn2ja5jobq.instabox.tech/oauth/instagram/deauthorize` |
| Data deletion request URL | `https://8787-agqwhbc22xbwwr7lmn2ja5jobq.instabox.tech/oauth/instagram/data-deletion` |

Both verify Meta's `signed_request` (HMAC-SHA256 with your Instagram app secret). Deauthorize deletes the
account's tokens and marks the channel disconnected (posts stay). Data deletion removes the channel, its
tokens, posts and comments and answers `{"url", "confirmation_code"}`; the status page is
`<public-url>/data-deletion/<code>`.

Background research on every network's free tier, scopes and limits: [docs/CONNECTORS_RESEARCH.md](docs/CONNECTORS_RESEARCH.md).

---

## 1. Instagram (Meta app, "Instagram API with Instagram Login")

Needs an Instagram **Business or Creator** account (huhbyte.ai). No Facebook Page needed.

1. Go to <https://developers.facebook.com/apps/> → **Create app**.
2. Use case: **Manage messaging & content on Instagram** (wording may vary: pick the Instagram one).
   App type *Business* if asked. Name it e.g. "HuhQueue".
3. In the app: **Instagram → API setup with Instagram login**.
4. Step "Generate access tokens": click **Add account** and log in as **huhbyte.ai**. While the app is in
   development mode, only accounts added here (or as *Instagram testers* under **App roles → Roles →
   Instagram testers**, then accepted in the Instagram app: *Settings → Website permissions → Apps and
   websites → Tester invites*) can connect.
5. Step "Set up Instagram business login": **Business login settings → OAuth redirect URIs** → paste the
   Instagram redirect URI above → **Save**.
6. Copy the **Instagram app ID** and **Instagram app secret** shown at the top of *API setup with Instagram
   login* (not the Facebook App ID on the Basic settings page; they are different numbers).
7. Open HuhQueue → **Channels → Instagram** (or **Settings → App keys**), paste both, **Save keys**.
8. Click **Continue to Instagram** → log in as huhbyte.ai → **Allow**. You land on the channel page with
   profile, followers, the **Sent / History** grid, **Community** and **Insights**.
9. App settings → **Basic**: add the privacy policy URL (`<public-url>/privacy`). Development mode is
   enough for your own account; App Review is only needed to let *other* people connect.
10. Instagram → **App settings**: paste the *Deauthorize callback URL* and *Data deletion request URL*
    from the table at the top (or copy them from Settings → App keys → Instagram).

Limits HuhQueue enforces: at most **50 API-published posts per channel per rolling 24 hours** (lower if
Meta's `content_publishing_limit` reports a smaller quota; Meta's own number wins when it is higher-used).
Over the cap a post waits and retries instead of failing. PNG and WebP images are converted to JPEG before
they go to Instagram (the API only accepts JPEG).

Scopes requested: `instagram_business_basic, instagram_business_content_publish,
instagram_business_manage_comments, instagram_business_manage_insights`.
Tokens: short-lived (1 h) → long-lived (60 days), refreshed automatically before they expire.

Fallback: on the Instagram connect screen, *Other ways to connect → Paste an access token* takes a token
from step 4's **Generate token** button.

## 2. YouTube (Google OAuth client)

1. <https://console.cloud.google.com/> → create a project (e.g. "HuhQueue").
2. **APIs & Services → Library** → enable **YouTube Data API v3**.
3. **OAuth consent screen** (Google Auth Platform): User type **External**, app name, your email.
   Scopes: `youtube.upload`, `youtube.readonly`, `youtube.force-ssl`.
   **Audience → Test users** → add the Google account that owns the YouTube channel.
4. **Credentials → Create credentials → OAuth client ID** → *Web application* →
   **Authorized redirect URIs** → paste the YouTube redirect URI above → Create.
5. Copy **Client ID** and **Client secret** → HuhQueue **Settings → App keys → YouTube** → Save.
6. **Channels → YouTube → Continue to YouTube** → pick the channel → Allow.

While the project is unverified, Google keeps uploads **private**; testing-mode refresh tokens also expire
after 7 days (reconnect, or publish the consent screen to "In production").

## 3. X (Twitter) developer app

1. <https://developer.x.com/en/portal/dashboard> → sign in as the X account → create a Project and an App
   (the Free tier allows a small number of posts per month; check your plan's limits).
2. App → **User authentication settings → Set up**:
   - App permissions: **Read and write**
   - Type of App: **Web App, Automated App or Bot** (confidential client)
   - Callback URI / Redirect URL: paste the X redirect URI above
   - Website URL: the public URL
3. **Keys and tokens → OAuth 2.0 Client ID and Client Secret** → copy both (the secret is shown once).
4. HuhQueue **Settings → App keys → X** → paste → Save.
5. **Channels → X → Continue to X** → **Authorize app**.

Scopes: `tweet.read tweet.write users.read media.write offline.access` (PKCE S256). `media.write` is what
X's v2 media upload needs; `offline.access` gives a refresh token (tokens last 2 h and are refreshed
automatically; X rotates the refresh token every time).

---

## Running it yourself

```bash
pip install -e .
cp .env.example .env      # set HUHQ_ADMIN_PASSWORD (8+ chars) at least
huhqueue serve            # UI + API on :8787, edge (media + OAuth callbacks) on :8788, worker in-process
```

Hosted on one public https origin (InstaBox, a VPS behind a proxy): set
`HUHQ_HOST=0.0.0.0`, `HUHQ_PUBLIC_BASE_URL=https://<public-url>` and `HUHQ_COOKIE_SECURE=1`; the UI app
also answers `/oauth/<network>/callback` and `/m/...`, so only port 8787 needs to be public.

### Docker (any always-on host)

```bash
docker compose up -d      # builds the image, UI + API on :8787, data in the huhqueue-data volume
docker compose logs -f    # watch the worker
```

- One container runs the UI, REST API, edge and worker. `/data` holds the SQLite database, media and the generated
  token key (`secret.key`): back up the volume, and keep it when you upgrade (`docker compose up -d --build`).
- Settings go in `.env` next to `docker-compose.yml` (see `.env.example`); developer-app keys can also be pasted in
  Settings › App keys. ffmpeg is included, so video end cards work.
- Public URL: put a reverse proxy (Caddy, nginx, Cloudflare Tunnel) with https in front of port 8787, set
  `HUHQ_COOKIE_SECURE=1`, and sign in once on that URL; OAuth callbacks and media links follow it (or pin it with
  `HUHQ_PUBLIC_BASE_URL`). Port 8788 is bound to localhost only; it is not needed when 8787 is public.
- The container has a health check on `/api/health`. Any host that keeps the container running all the time (a small
  VPS, a home server, a PaaS with a persistent volume) avoids InstaBox's 30-minute sleep, so posts go out on time.

### Your own AI model (optional)

Settings › **AI model**: pick a provider, paste a key, **Test connection**. Free options: an NVIDIA key from
build.nvidia.com (`nim`), a Gemini key from aistudio.google.com (`gemini`), Groq, OpenRouter's `:free` models, or a local
Ollama (no key). Thinking models (e.g. `nvidia/nemotron-3-ultra-550b-a55b`) need a higher **Max output tokens** (8192).
Without the UI:

```bash
# .env (used only while nothing is saved in Settings)
HUHQ_AI_PROVIDER=nim
HUHQ_AI_MODEL=nvidia/nemotron-3-ultra-550b-a55b
HUHQ_AI_API_KEY=nvapi-...
HUHQ_AI_MAX_TOKENS=8192

# or, on the machine that runs HuhQueue (asks for the key; or --key-env VAR)
huhqueue ai set --provider gemini --model gemini-2.5-flash
huhqueue ai test
```

### InstaBox notes

- Machine `huhqueue` (`m_agqwhbc22xbwwr7lmn2ja5jobq`), service `huhqueue` (systemd, restarts always),
  code in `/data/home/bufferopensource`, venv `/data/home/venv`, config `/data/home/.huhqueue/huhqueue.env`
  (mode 600), database + media in `/data/home/.huhqueue/data` (the persistent `/data` disk).
- InstaBox pauses a machine after 30 idle minutes (not configurable on this plan). Opening the URL wakes
  it, and the worker publishes anything that came due while it slept, so a scheduled post can go out
  late, not lost.
- Redeploy after a change: `INSTABOX_API_KEY=... python scripts/deploy_instabox.py` (uploads the committed
  `HEAD`, reinstalls, restarts; the database, media and keys on `/data` are kept).
