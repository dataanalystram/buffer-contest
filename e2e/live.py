"""Scripted MCP client (official mcp SDK 2.x, Streamable HTTP) for https://huhqueue-studio.onrender.com/mcp.
Agent token is added on the wire by the sandbox gateway. No LLM.
  python live.py check | schema NAME... | call NAME 'JSON'
"""
import asyncio, json, sys, time
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client as shc
URL = "https://huhqueue-studio.onrender.com/mcp"
def _g(o, *ns):
    for n in ns:
        if hasattr(o, n): return getattr(o, n)
def js(res):
    d = _g(res, 'structured_content', 'structuredContent')
    if d is not None:
        return d.get("result", d) if isinstance(d, dict) and set(d) == {"result"} else d
    try: return json.loads(res.content[0].text)
    except Exception: return {"text": res.content[0].text if res.content else ""}
async def sess(fn):
    async with shc(URL) as (r, w):
        async with ClientSession(r, w) as s:
            init = await s.initialize()
            return await fn(s, init)
async def call(s, name, args, timeout=300):
    t = time.time()
    r = await asyncio.wait_for(s.call_tool(name, args), timeout)
    return {"tool": name, "is_error": _g(r, 'is_error', 'isError'), "secs": round(time.time() - t, 1), "data": js(r)}
async def check(s, init):
    out = {}
    t = time.time(); tl = await s.list_tools(); names = [x.name for x in tl.tools]
    out["tools"] = len(names); out["dups"] = sorted({n for n in names if names.count(n) > 1}); out["t_tools"] = round(time.time()-t, 2)
    pl = await s.list_prompts(); out["prompts"] = {p.name: [(a.name, a.required) for a in (p.arguments or [])] for p in pl.prompts}
    gets = {}
    for p in pl.prompts:
        args = {a.name: "https://www.producthunt.com/posts/screen-studio" for a in (p.arguments or []) if a.required}
        g = await s.get_prompt(p.name, args)
        gets[p.name] = {"messages": len(g.messages), "first": str(_g(g.messages[0].content, 'text'))[:160]}
    out["prompt_get"] = gets
    rl = await s.list_resources(); out["resources"] = [str(x.uri) for x in rl.resources]
    rd = {}
    for u in out["resources"]:
        rr = await s.read_resource(u); c = rr.contents[0]
        rd[u] = {"mime": _g(c, 'mime_type', 'mimeType'), "len": len(_g(c, 'text') or "")}
    out["resource_read"] = rd
    return out
async def schema(s, init):
    tl = await s.list_tools()
    return {t.name: _g(t, "input_schema", "inputSchema") for t in tl.tools if t.name in sys.argv[2:]}
if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "check": res = asyncio.run(sess(check))
    elif cmd == "schema": res = asyncio.run(sess(schema))
    elif cmd == "call": res = asyncio.run(sess(lambda s, i: call(s, sys.argv[2], json.loads(sys.argv[3]) if len(sys.argv) > 3 else {})))
    print(json.dumps(res, indent=1, default=str))
