import json, sys, urllib.request
q = """query($i: PostsInput!, $n: Int) { posts(first: $n, input: $i) { edges { node { id status channelId createdAt text } } } }"""
v = {"i": {"organizationId": "6ad9d04ef3e1acb6edfdc762", "filter": {"status": ["draft"]}}, "n": 100}
req = urllib.request.Request("https://api.buffer.com", json.dumps({"query": q, "variables": v}).encode(), {"Content-Type": "application/json"})
d = json.load(urllib.request.urlopen(req, timeout=30))
if "errors" in d: print(d["errors"]); sys.exit(1)
rows = [e["node"] for e in d["data"]["posts"]["edges"]]
for r in rows: print(r["id"], r["channelId"], r["createdAt"], (r["text"] or "")[:50].replace("\n"," "))
print("COUNT", len(rows))
