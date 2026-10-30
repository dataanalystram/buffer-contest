"""Record the live HuhQueue demo with Playwright. Usage: python3 record.py <device_link_url>"""
import json, os, sys, time
sys.path.insert(0, "/tmp")
from pwh import proxy
from playwright.sync_api import sync_playwright

BASE = "https://huhqueue-studio.onrender.com"
LINK = sys.argv[1]
ARTICLE = "https://en.wikipedia.org/wiki/Model_Context_Protocol"
OUT = os.path.abspath("raw")
os.makedirs(OUT, exist_ok=True)
marks = {}

def mark(name):
    marks[name] = round(time.time() - T0, 2); print(name, marks[name], flush=True)

with sync_playwright() as p:
    b = p.chromium.launch(proxy=proxy())
    vp = {"width": 1920, "height": 1080}
    # clip 1: landing + real redirect to Buffer's OAuth page
    c1 = b.new_context(viewport=vp, ignore_https_errors=True, bypass_csp=True, record_video_dir=OUT + "/c1", record_video_size=vp, device_scale_factor=1)
    ZOOM = "document.addEventListener('DOMContentLoaded',()=>{if(location.host.includes('huhqueue'))document.documentElement.style.zoom='1.25'})"
    c1.add_init_script(ZOOM)
    pg = c1.new_page(); T0 = time.time()
    pg.goto(BASE); pg.get_by_test_id("connect-buffer").wait_for(); mark("c1_landing"); time.sleep(4)
    pg.get_by_test_id("connect-buffer").hover(); time.sleep(0.8)
    pg.get_by_test_id("connect-buffer").click(); [time.sleep(0.25) for _ in range(120) if "auth.buffer.com" not in pg.url]; time.sleep(3)
    mark("c1_buffer_auth"); time.sleep(4); mark("c1_end")
    c1.close()
    json.dump(marks, open(OUT + "/c1_marks.json", "w")); marks.clear()

    # setup (not recorded): open the one-time device link, clear stray approvals except the oldest agent post
    c0 = b.new_context(ignore_https_errors=True, bypass_csp=True)
    p0 = c0.new_page(); p0.goto(LINK); p0.get_by_test_id("channel-chip").first.wait_for(timeout=60000)
    sess = p0.request.get(BASE + "/api/session").json()
    print("actor", sess["user"]["actor"])
    assert sess["user"]["actor"] == "you", "recorder is not acting as the human"
    pend = p0.request.get(BASE + "/api/approvals").json()
    for a in sorted(pend, key=lambda a: a["id"])[1:]:
        p0.request.post(f"{BASE}/api/approvals/{a['id']}/reject", headers={"X-HQ-CSRF": sess["csrf"]})
    state = c0.storage_state(); c0.close()

    # clip 2: signed-in studio (device link = same Buffer account Ram connected via OAuth)
    c2 = b.new_context(viewport=vp, ignore_https_errors=True, bypass_csp=True, record_video_dir=OUT + "/c2", record_video_size=vp, device_scale_factor=1, storage_state=state)
    c2.add_init_script(ZOOM)
    pg = c2.new_page(); T0 = time.time()
    pg.goto(BASE); pg.get_by_test_id("channel-chip").first.wait_for(timeout=60000)
    time.sleep(1.5); mark("connected"); time.sleep(3)
    for chip in pg.get_by_test_id("channel-chip").all():
        if "youtube" in chip.inner_text().lower():
            chip.click(); time.sleep(0.6)
    mark("type_link")
    pg.get_by_test_id("url-input").click(); pg.get_by_test_id("url-input").type(ARTICLE, delay=35); time.sleep(0.8)
    pg.get_by_test_id("draft-btn").click(); mark("drafting")
    pg.get_by_test_id("draft-card").first.wait_for(timeout=90000); mark("drafts_ready"); time.sleep(2.5)
    engines = pg.locator("[data-testid=draft-card] .meter span:last-child").all_inner_texts()
    print("engines", engines)
    pg.mouse.wheel(0, 380); time.sleep(3); mark("drafts_view"); pg.screenshot(path=OUT + "/studio-drafts.png")
    li = pg.locator("[data-testid=draft-card][data-service=linkedin]")
    if li.count():
        ta = li.get_by_test_id("draft-text"); ta.click(); pg.keyboard.press("Control+End"); pg.keyboard.type("\n\nWhat would you build with it?", delay=40); time.sleep(1.5)
    mark("edited")
    pg.mouse.wheel(0, -380); time.sleep(1)
    pg.get_by_test_id("bulk-mode").select_option("draft"); time.sleep(1)
    pg.get_by_test_id("send-all").click(); mark("send_all")
    for _ in range(240):
        if pg.locator("[data-testid=draft-ok],[data-testid=draft-err]").count() >= pg.get_by_test_id("draft-card").count(): break
        time.sleep(0.25)
    time.sleep(1); pg.mouse.wheel(0, 380); time.sleep(3.5); mark("sent")
    print("statuses", pg.locator("[data-testid=draft-status]").all_inner_texts())
    pg.mouse.wheel(0, -1000); time.sleep(0.5)
    pg.get_by_test_id("tab-queue").click(); pg.get_by_test_id("queue-status").select_option("draft")
    pg.get_by_test_id("queue-item").first.wait_for(timeout=30000); mark("queue"); pg.screenshot(path=OUT + "/queue.png"); time.sleep(4)
    pg.get_by_test_id("tab-approvals").click(); time.sleep(1.2); mark("approvals")
    if pg.get_by_test_id("approval-item").count():
        time.sleep(1); pg.screenshot(path=OUT + "/approvals.png"); time.sleep(2)
        it = pg.get_by_test_id("approval-item").first
        it.get_by_test_id("approval-mode").select_option("draft"); time.sleep(0.8)
        it.get_by_test_id("approve").click(); pg.get_by_text("Sent to Buffer").first.wait_for(timeout=30000); mark("approved"); time.sleep(2.5)
    pg.get_by_test_id("tab-agents").click(); time.sleep(2); mark("agents"); pg.screenshot(path=OUT + "/agents.png")
    pg.locator("label.switch").click(); pg.get_by_text("Agents paused").first.wait_for(); mark("kill_on"); time.sleep(2.5)
    pg.locator("label.switch").click(); time.sleep(1.5)
    pg.mouse.wheel(0, 500); time.sleep(3); mark("snippets")
    pg.mouse.wheel(0, -1000)
    pg.get_by_test_id("tab-activity").click(); pg.get_by_test_id("audit-row").first.wait_for(); mark("activity"); time.sleep(4.5); mark("c2_end")
    pg.screenshot(path=OUT + "/activity.png")
    c2.close(); b.close()
    json.dump(marks, open(OUT + "/c2_marks.json", "w"))
