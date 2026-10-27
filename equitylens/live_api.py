import asyncio
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .config import Settings
from .live.sec import configured as sec_configured
from .live.service import ResearchService
from .live.types import FollowUpInput, ResearchInput, RetrievalInput


def create_app(settings=None, service=None):
    settings = settings or Settings()
    service = service or ResearchService(settings)

    @asynccontextmanager
    async def lifespan(_app):
        async def cleanup():
            while True:
                await asyncio.sleep(30)
                service.sessions.purge()

        task = asyncio.create_task(cleanup())
        yield
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        service.sessions.clear()

    app = FastAPI(
        title="EquityLens — On-demand research with session RAG", version="0.5.0", lifespan=lifespan
    )
    app.state.research_service = service
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver", "api"])

    @app.middleware("http")
    async def boundary(request: Request, call_next):
        origin = request.headers.get("origin")
        if origin and origin not in {
            "http://127.0.0.1:8501",
            "http://localhost:8501",
            "http://127.0.0.1:8001",
            "http://localhost:8001",
        }:
            return JSONResponse({"detail": "Cross-origin requests are not enabled."}, status_code=403)
        length = request.headers.get("content-length", "0")
        if not length.isdigit() or int(length) > 16000:
            return JSONResponse({"detail": "Request exceeds the supported size."}, status_code=413)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.exception_handler(ValueError)
    async def invalid(_request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    @app.exception_handler(httpx.HTTPError)
    async def upstream(_request, _exc):
        return JSONResponse(
            {
                "detail": "Live company search is unavailable. Retry later; no stored company records were substituted."
            },
            status_code=502,
        )

    @app.exception_handler(TimeoutError)
    async def timeout(_request, _exc):
        return JSONResponse(
            {"detail": "Research exceeded its time limit. Try quick research or a shorter date range."},
            status_code=504,
        )

    @app.get("/health")
    def health():
        return {
            "status": "ok",
            "mode": "on-demand",
            "company_database_required": False,
            "retention_seconds": settings.research_ttl_seconds,
            "rag_embedding_model": settings.rag_embedding_model,
            "rag_storage": "session memory only",
        }

    @app.get("/live/capabilities")
    def capabilities():
        return {
            "lookup": "Public Yahoo Finance search (unofficial)",
            "financials": "Public Yahoo Finance financial history (unofficial)",
            "stock": "Dated quotes, daily history and calculated valuations from public Yahoo Finance endpoints (unofficial)",
            "news": "Google News RSS headlines (unofficial)",
            "nse_announcements": "Official NSE rolling announcements RSS for exact .NS ticker matches",
            "web_research_configured": bool(settings.tavily_api_key),
            "profile_api_configured": bool(settings.eodhd_api_key),
            "sec_filings_configured": sec_configured(settings.sec_user_agent),
            "generation_configured": settings.generation_configured(),
            "generation_api_key_configured": bool(settings.llm_api_key.get_secret_value().strip()),
            "generation_provider": "Frontier-model API",
            "generation_api_mode": settings.llm_api_mode,
            "generation_api_base_url": settings.llm_api_base_url,
            "generation_model": settings.llm_model or None,
            "retention_seconds": settings.research_ttl_seconds,
        }

    @app.get("/live/model")
    async def model_status(
        model: str = Query("", max_length=150, pattern=r"^(?:[A-Za-z0-9][A-Za-z0-9_./:\-]{0,149})?$"),
        check: bool = False,
    ):
        checker = getattr(service.provider, "model_status", None)
        if checker is None:
            return {"state": "disabled", "model": None, "message": "No generation connector configured."}
        return await checker(model, check=check)

    @app.get("/live/companies")
    async def lookup(
        q: str = Query(..., min_length=2, max_length=100),
        market: str = Query("AUTO", pattern="^(AUTO|US|IN)$"),
    ):
        return await service.lookup(q.strip(), market)

    @app.post("/live/research")
    async def research(body: ResearchInput):
        return await service.research(body)

    def session_token(authorization):
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(401, "Research session token required.")
        return authorization.removeprefix("Bearer ")

    @app.get("/live/sessions/{session_id}")
    def get_session(session_id: str, authorization: str | None = Header(None)):
        try:
            return service.sessions.get(session_id, session_token(authorization))
        except KeyError as exc:
            raise HTTPException(
                404, "Research session expired or is inaccessible. Run fresh research."
            ) from exc

    @app.delete("/live/sessions/{session_id}")
    def clear_session(session_id: str, authorization: str | None = Header(None)):
        try:
            service.sessions.delete(session_id, session_token(authorization))
        except KeyError as exc:
            raise HTTPException(404, "Research session not found.") from exc
        return {"status": "cleared"}

    @app.post("/live/sessions/{session_id}/follow-up")
    async def follow_up(session_id: str, body: FollowUpInput, authorization: str | None = Header(None)):
        try:
            return await service.follow_up(
                session_id, session_token(authorization), body.question, body.llm_model
            )
        except KeyError as exc:
            raise HTTPException(404, "Research session expired or is inaccessible.") from exc

    @app.post("/live/sessions/{session_id}/retrieve")
    async def retrieve(session_id: str, body: RetrievalInput, authorization: str | None = Header(None)):
        try:
            return await service.retrieve(session_id, session_token(authorization), body.question, body.top_k)
        except KeyError as exc:
            raise HTTPException(404, "Research session expired or is inaccessible.") from exc

    return app
