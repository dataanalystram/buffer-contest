"""The product search feature (6 Nov 2026) is gone: no code, UI, route, MCP tool, admin meter or kill switch is left.
The banned words are built at run time so this file doesn't match its own search."""
import re
import subprocess
from pathlib import Path

from studio import mcp, opsctl

ROOT = Path(__file__).parents[2]
WORDS = ["chan" + "nel3", "com" + "merce", "trychan" + "nel3", "shop" + "-ready"]
TOOLS = ["search_" + "products", "get_" + "product", "attach_product_" + "to_post"]


def test_no_trace_in_tracked_text_files():
    files = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
    pat = re.compile("|".join(map(re.escape, WORDS)), re.I)
    hits = []
    for f in files:
        if f == "CHANGELOG.md" or Path(f).suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".mp4", ".gif", ".ico", ".woff2",
                                                               ".ttf", ".otf", ".pdf", ".zip", ".gz", ".deb", ".wav", ".mp3"):
            continue
        try:
            text = (ROOT / f).read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
            continue
        if f == "studio/store.py":   # account erase still clears the three retired tables' old rows (privacy); only those names
            text = text.replace(WORDS[1] + "_links", "").replace(WORDS[1] + "_user_day", "").replace(WORDS[1] + "_credits", "")
        hits += [f"{f}: {m.group(0)}" for m in pat.finditer(text) or [] if m.group(0)]
        hits += [f"{f}: {pat.pattern}" for _ in [0] if pat.search(f)]
    assert not hits, hits[:20]


def test_no_routes_and_no_mcp_tools(client, human):
    for path in ("/api/" + WORDS[1] + "/search", "/api/" + WORDS[1] + "/usage", "/api/" + WORDS[1] + "/key"):
        assert client.post(path, json={"query": "kettle"}).status_code in (404, 405)
        assert human.get(path).status_code in (404, 405)
    names = {t["name"] for t in mcp.TOOLS}
    assert not names & set(TOOLS)
    assert WORDS[0] not in opsctl.SWITCHES


def test_homepage_and_make_a_post_carry_none_of_it(client, human):
    home = client.get("/").text.lower()
    assert "channel" + "3" not in home and "commission" not in home and 'id="shop"' not in home
    app_html = human.get("/").text
    idx = (ROOT / "studio/static/index.html").read_text()
    assert "product-panel" not in idx and "/static/" + WORDS[1] not in idx and "product-panel" not in app_html
    house = (ROOT / "studio/static/house.js").read_text()
    assert "hqProduct" not in house
