"""Read-only stdio MCP tools. Run: python -m equitylens.mcp_server."""

import asyncio
from contextlib import asynccontextmanager
from typing import Literal

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from .config import Settings
from .live.service import ResearchService
from .live.types import ResearchInput

service = ResearchService(Settings())


@asynccontextmanager
async def lifespan(_server):
    async def cleanup():
        while True:
            await asyncio.sleep(30)
            service.sessions.purge()

    task = asyncio.create_task(cleanup())
    yield {}
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    service.sessions.clear()


server = MCPServer(
    "EquityLens live research",
    log_level="WARNING",
    lifespan=lifespan,
    instructions="Research Indian and US companies using live sources. Always show coverage gaps and evidence limitations. Lookup returns a signed company_token; use it for research. Reports expire in memory. Do not claim headline evidence verifies article bodies.",
)
annotations = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=True)


@server.tool(annotations=annotations)
async def lookup_company(name: str, market: Literal["AUTO", "US", "IN"] = "AUTO") -> dict:
    """Find live company candidates. Ask the user to choose if the name is ambiguous."""
    if not 2 <= len(name) <= 100:
        raise ValueError("Use a company name between 2 and 100 characters.")
    return {"matches": await service.lookup(name, market)}


@server.tool(annotations=annotations)
async def research_company(company_token: str, news_days: int = 7) -> dict:
    """Fetch financial history, recent news, and reported developments now. Returns a temporary session."""
    return await service.research(ResearchInput(company_token=company_token, news_days=news_days))


@server.tool(annotations=annotations)
async def get_research(session_id: str, session_token: str) -> dict:
    """Read a non-expired research session. This does not refresh external sources."""
    return service.sessions.get(session_id, session_token)


@server.tool(annotations=annotations)
async def search_research(session_id: str, session_token: str, question: str) -> dict:
    """Retrieve relevant passages from this session's temporary semantic/BM25 index. Does not fetch fresh sources."""
    if not 3 <= len(question) <= 1000:
        raise ValueError("Use a question between 3 and 1000 characters.")
    return await service.retrieve(session_id, session_token, question)


if __name__ == "__main__":
    server.run(transport="stdio")
