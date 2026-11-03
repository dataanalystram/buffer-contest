import asyncio, json, time
from live import sess, call
ROUNDS = 3
def mk(name, writes):
    async def run(s, init):
        log = []
        for i in range(ROUNDS):
            w = writes(i)
            r = await call(s, "update_brand_kit", w); log.append({"op": "update", "args": w, "err": r["is_error"], "secs": r["secs"], "msg": str(r["data"])[:160] if r["is_error"] else ""})
            ra = {"template": "breaking", "services": ["instagram"], "data": {"title": f"E2E parallel {name}{i}", "source": "e2e"}}
            if w.get("kit_id"): ra["kit_id"] = w["kit_id"]
            r = await call(s, "render_template", ra)
            d = r["data"]; log.append({"op": "render", "err": r["is_error"], "secs": r["secs"], "kit": d.get("kit") if isinstance(d, dict) else None,
                                       "url": d["channels"][0]["slides"][0] if not r["is_error"] else str(d)[:200]})
            if name == "C" and i == 1:
                r = await call(s, "list_brand_kits", {}); log.append({"op": "list", "secs": r["secs"], "kits": [(k["id"], k["name"]) for k in r["data"]["kits"]]})
        return log
    return run
A = mk("A", lambda i: {"kit_id": 6, "voice": f"E2E-A voice {i}"})
B = mk("B", lambda i: {"kit_id": 6, "website": f"https://linear.app/?e2e=B{i}"})
C = mk("C", lambda i: {"voice": f"E2E-C active voice {i}"})
async def main():
    t = time.time()
    res = await asyncio.gather(sess(A), sess(B), sess(C), return_exceptions=True)
    return {"wall_s": round(time.time() - t, 1), "A": res[0], "B": res[1], "C": res[2]}
out = asyncio.run(main()); json.dump(out, open("par.json", "w"), indent=1, default=str)
print("wall", out["wall_s"])
for k in "ABC":
    v = out[k]
    if not isinstance(v, list): print(k, "EXC", str(v)[:300]); continue
    print(k, [(e["op"][0], e.get("err"), e["secs"], (e.get("kit") or {}).get("id") if isinstance(e.get("kit"), dict) else e.get("kits")) for e in v], [e["msg"] for e in v if e.get("msg")])
