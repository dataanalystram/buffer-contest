import asyncio, sys, json
from live import sess, _g
async def f(s, i):
    rr = await s.read_resource(sys.argv[1]); return _g(rr.contents[0], 'text')
print(asyncio.run(sess(f))[:int(sys.argv[2]) if len(sys.argv) > 2 else 600])
