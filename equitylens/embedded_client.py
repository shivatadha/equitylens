"""Call the live FastAPI app inside a Streamlit process on one event loop."""

import asyncio
import threading
from concurrent.futures import TimeoutError as FutureTimeoutError

import httpx

from .live_api import create_app


class EmbeddedClient:
    def __init__(self, app=None):
        self.app = app or create_app()
        self.loop = asyncio.new_event_loop()
        self.ready = threading.Event()
        self.thread = threading.Thread(target=self._run_loop, name="equitylens-embedded-api", daemon=True)
        self.thread.start()
        if not self.ready.wait(timeout=5):
            raise RuntimeError("Embedded research service did not start.")

    def _run_loop(self):
        asyncio.set_event_loop(self.loop)
        self.ready.set()
        self.loop.run_forever()

    async def _request(self, method, path, kwargs):
        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://api", timeout=195) as client:
            return await client.request(method, path, **kwargs)

    def request(self, method, path, **kwargs):
        future = asyncio.run_coroutine_threadsafe(self._request(method, path, kwargs), self.loop)
        try:
            return future.result(timeout=210)
        except FutureTimeoutError as exc:
            future.cancel()
            raise httpx.TimeoutException("Embedded research request timed out.") from exc

    def close(self):
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=5)
        if not self.thread.is_alive():
            self.loop.close()
