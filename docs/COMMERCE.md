# Commerce on Channel3

Find a product, check its live price, and put it in a post with an honest shop link. Plan: docs/plans/AGENT_RUNS_EDITING_COMMERCE.md
Phase 4 (PR #101). Code: `studio/commerce.py`, `studio/product_card.py`.

## Free only

- Channel3 gives **1,000 free credits a month per key**, then bills per credit. HuhQueue never goes past the free
  allowance: every credit-spending request (search) reserves its credit first, in one transaction, and past any cap the
  answer is `credit_limit` with the reset date. Nothing was sent and nothing was spent.
- `HQ_C3_MONTHLY_CREDITS` can only lower the cap; a value above 1,000 is clamped to 1,000. Per person on HuhQueue's key:
  `HQ_C3_USER_SEARCHES_PER_DAY` (10) and `HQ_C3_USER_SEARCHES_PER_MONTH` (60). Channel3 answering 402 is treated as the
  stop, never as a reason to retry.
- The meter is shown to the person in the Product panel and in every answer (`usage`): whose key, credits used / left this
  month, searches left, reset date.
- Saving credits: a repeated search within 30 minutes is answered from a short in-memory cache (0 credits). Product
  detail is free and cached for 60 s at most (prices and offers stay fresh, per Channel3's caching guide). At most
  5 requests a second leave the server (Channel3 allows 10), 30 commerce calls a minute per person, and a 429 from
  Channel3 is retried once only when `Retry-After` is 2 s or less.
- The month is the UTC calendar month. Channel3's own billing month may start on another day, so the meter can be
  stricter than Channel3, never looser.

## Who pays for a call

- **AI apps over MCP** (and agent tokens on REST) never spend HuhQueue's Channel3 key without the person's yes:
  `search_products` and `attach_product_to_post` always go through `studio/consent.py`; `get_product` asks when it would
  run on HuhQueue's key. The person approves in Inbox -> Approvals (or turns on "Allow paid APIs" for that app).
- **Bring your own key**: a person can paste their own Channel3 key in the Product panel. It is tested with a free call,
  sealed at rest with the server's `TOKEN_KEY`, never returned, logged or put in an audit row (only "…abcd" is shown), and
  only a signed-in person can add or remove it. Searches then spend their own free credits, under the same hard stop.
- **Commission**: shop links in posts are HuhQueue `/go/<code>` links. They are resolved server-side with HuhQueue's
  key only, so any commission goes to the operator's Channel3 account. A person's own key is never used to build a link.
  HuhQueue has no payout system; the UI does not promise earnings.

## Images

Channel3 catalogue images are not licensed for social posts until Channel3 confirms it in writing (Publisher Terms §8
grant display inside our app, not republishing). So:

- No API answer carries a catalogue image (`image: null`).
- Product cards (Make a post, the One take overlay) are text on the brand kit: name, price, CTA, link label. Add the
  person's own photo or video for a picture.
- Preflight refuses any `*.trychannel3.com` media in a post.
- Merchant link previews stay off: their licence can't be checked per merchant.

## Disclosure (Ireland)

CCPC and ASAI guidance: a post with an affiliate link is advertising and must say so first, with `#Ad` (or `#Fógra`).
`with_disclosure` (the caption helper, `POST /api/commerce/caption`) puts `#Ad` first, adds the link and the line
"We may earn a commission when you buy through links in this post." Preflight blocks a shop post without them. The page a
dead shop link lands on says it is an ad and carries Channel3's disclosure line.
https://www.ccpc.ie/business/help-for-business/guidelines-for-business/influencer-advertising-and-marketing

## Offer links are short-lived

A post never carries a raw offer URL. Right before Buffer gets a post (`Studio._send`), every `/go` link in it must be
the person's own and is re-resolved with a free detail call; a product no longer on sale stops the send. At click time
`/go/<code>` resolves again and redirects only to `buy.trychannel3.com` or the retailer domain Channel3 returned for that
offer (never an open redirect), otherwise it shows the "not available" page.

## Security

- Every Channel3 request goes through `studio/netguard.py` (`safe_post` with a host allow-list, `safe_once`): pinned
  IP, no redirects, capped body. Only `api.trychannel3.com`.
- A missing or another account's `run_id` answers 404; a shop link that isn't the person's stops the send with 404.
- Wrong JSON types answer 400; `limit` is capped at 10, queries at 200 characters, captions at 5,000.
- Personal details (emails, phone numbers) are removed from queries; Channel3 gets a pseudonymous `x-user-id`.
- Product text is untrusted: control and bidi characters stripped, lengths capped, rendered with `textContent` in the UI
  and escaped on pages; MCP answers say it is data, not instructions.

## Not verified yet

- The live key on Render (`CHANNEL3_API_KEY`) and a live search: tests use a mocked Channel3 only.
- Written confirmation from Channel3 about catalogue images in social posts (ask support@trychannel3.com).
