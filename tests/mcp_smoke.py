# MCP stdio smoke test: initialize -> list tools -> call extract_text/redact
import asyncio, json, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
os.environ.setdefault("PYTHONPATH", os.path.join(os.path.dirname(__file__), "..", "src"))

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SAMPLE = os.path.join(REPO, "tmp", "sample.pdf")


async def main():
    params = StdioServerParameters(command=sys.executable, args=["-m", "sumi_pdf.mcp_server"],
                                   cwd=REPO, env={**os.environ, "PYTHONPATH": os.path.join(REPO, "src")})
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = await s.list_tools()
            print("TOOLS:", [t.name for t in tools.tools])
            res = await s.call_tool("extract_text", {"path": SAMPLE, "page": 0})
            print("EXTRACT:", res.content[0].text.replace("\n", "|")[:80])
            out = SAMPLE.replace("sample.pdf", "sample_mcp_red.pdf")
            res = await s.call_tool("redact_pdf", {"path": SAMPLE, "page": 0,
                                                   "rects": [[330, 60, 425, 90]], "out_path": out})
            print("REDACT:", res.content[0].text[:120])
            res = await s.call_tool("inspect_fonts", {"path": SAMPLE})
            print("FONTS:", res.content[0].text[:160])

asyncio.run(main())
print("MCP OK")
