"""Start the supcom-events MCP server over stdio and call its tools (smoke test / example client).

  uv run python tools/mcp_smoke.py
"""
import asyncio
import json
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parent.parent


async def main() -> None:
    params = StdioServerParameters(command=sys.executable, args=["-m", "mcp_server"], cwd=str(ROOT), env=dict(os.environ))
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            print("tools:", [t.name for t in (await s.list_tools()).tools])
            for name, args in [("list_events", {"limit": 5}), ("stats", {})]:
                res = await s.call_tool(name, args)
                print(name, "->", json.dumps(res.structuredContent or res.content[0].text)[:1500])


asyncio.run(main())
