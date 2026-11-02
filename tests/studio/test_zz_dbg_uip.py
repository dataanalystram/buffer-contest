from .test_e2e_studio import NO_TOUR, live  # noqa
def test_dbg(live, chromium):
    base, fake, app = live
    ctx = chromium.new_context(viewport={"width": 1440, "height": 900})
    ctx.add_init_script(NO_TOUR); ctx.add_cookies([{"name": "hq_sid", "value": "sid-e2e", "url": base}])
    ctx.route(lambda u: not u.startswith(base), lambda r: r.abort())
    page = ctx.new_page(); errs = []
    page.on("pageerror", lambda e: errs.append("PE " + str(e)))
    page.on("console", lambda m: errs.append(m.type + " " + m.text))
    page.goto(base + "/?tab=settings"); page.wait_for_timeout(2500)
    print("ERRS", errs[:15])
    print("JS", page.evaluate("[typeof window.HQ_AI_ENGINE, String((window.HS_TABS||{}).ai).slice(0,120), typeof brandMark]"))
    page.evaluate("window.HQ_AI_ENGINE && window.HQ_AI_ENGINE.refresh()"); page.wait_for_timeout(1500)
    print("ENG", page.inner_html("#aiEngines")[:300])
    print("TILES", page.inner_html("#integTiles")[:200])
