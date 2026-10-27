"""Check the real stdio MCP transport without saving research responses."""

import asyncio
import sys
from pathlib import Path

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


async def main():
    parameters = StdioServerParameters(
        command=sys.executable, args=["-m", "equitylens.mcp_server"], cwd=Path(__file__).resolve().parents[1]
    )
    async with stdio_client(parameters) as (read, write):
        async with ClientSession(read, write, read_timeout_seconds=45) as client:
            await client.initialize()
            tools = await client.list_tools()
            names = [t.name for t in tools.tools]
            assert set(names) == {"lookup_company", "research_company", "get_research", "search_research"}
            assert all(t.annotations.read_only_hint for t in tools.tools)
            result = await client.call_tool("lookup_company", {"name": "Tesla", "market": "US"})
            assert not result.is_error
            text = "\n".join(c.text for c in result.content if c.type == "text")
            assert "TSLA" in text
            print("MCP stdio: initialized, discovered four read-only tools, and resolved Tesla live.")


asyncio.run(main())
