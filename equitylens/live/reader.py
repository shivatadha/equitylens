"""Bounded public-source reading with DNS checked at the connection boundary.

Original source bytes and extracted blocks stay in RAM. Redirects and robots.txt are
checked using the same public-IP-only transport. No proxy environment is used.
"""

import asyncio
import ipaddress
import socket
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpcore

AGENT = "EquityLens"
MAX_BYTES = 3 * 1024 * 1024
SKIP_HOSTS = {"news.google.com", "finance.yahoo.com", "screener.in", "www.screener.in"}


def checked_url(url):
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
    ):
        raise ValueError("Only public HTTPS sources without credentials are supported.")
    host = parsed.hostname.lower().rstrip(".")
    if host in {"localhost", "localhost.localdomain"} or host.endswith((".localhost", ".local", ".internal")):
        raise ValueError("Local destinations are not allowed.")
    try:
        if not ipaddress.ip_address(host).is_global:
            raise ValueError("Private or special-use destinations are not allowed.")
    except ValueError as exc:
        if "destinations" in str(exc):
            raise
    return urlunsplit(("https", parsed.netloc, parsed.path or "/", parsed.query, ""))


async def public_addresses(host, port):
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    addresses = list(dict.fromkeys(info[4][0] for info in infos))
    if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses):
        raise ValueError("DNS resolved to a private or special-use address.")
    return addresses


class PublicNetworkBackend(httpcore.AsyncNetworkBackend):
    def __init__(self, delegate=None, resolver=None):
        self.delegate = delegate or httpcore.AnyIOBackend()
        self.resolver = resolver or public_addresses

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        if port != 443:
            raise ValueError("Only HTTPS connections are permitted.")
        addresses = await asyncio.wait_for(self.resolver(host, port), timeout=min(timeout or 6, 6))
        # Defense in depth, including when a custom resolver is used.
        if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses):
            raise ValueError("Connection target is not globally routable.")
        # Pin the connection to a validated IP. HTTPCore retains the original origin
        # for Host and TLS certificate/SNI verification; it never resolves again.
        return await self.delegate.connect_tcp(
            addresses[0], port, timeout=timeout, local_address=local_address, socket_options=socket_options
        )

    async def connect_unix_socket(self, *args, **kwargs):
        raise ValueError("Unix sockets are not allowed.")

    async def sleep(self, seconds):
        await asyncio.sleep(seconds)


class PublicReader:
    def __init__(self, fetch=None, extractor=None):
        self.fetch = fetch or self._fetch
        self.extractor = extractor or self._extract
        self.slots = asyncio.Semaphore(2)

    async def _fetch(self, url):
        url = checked_url(url)
        async with httpcore.AsyncConnectionPool(
            network_backend=PublicNetworkBackend(), max_connections=2
        ) as pool:
            async with pool.stream(
                "GET",
                url,
                headers={
                    "User-Agent": AGENT + "/0.3 local research",
                    "Accept": "text/html,application/pdf,text/plain",
                    "Accept-Encoding": "identity",
                },
                extensions={"timeout": {"connect": 6, "read": 8, "write": 6, "pool": 6}},
            ) as response:
                headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in response.headers}
                data = bytearray()
                async for block in response.aiter_stream():
                    data.extend(block)
                    if len(data) > MAX_BYTES:
                        raise ValueError("Source exceeds the three-megabyte limit.")
                if headers.get("content-encoding", "identity") not in {"identity", ""}:
                    raise ValueError("Compressed source responses are not processed by this reader.")
                return response.status, headers, bytes(data)

    async def _robots_allowed(self, url):
        parsed = urlsplit(url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        status, _, data = await self.fetch(robots_url)
        if status == 404:
            return True
        if status != 200 or len(data) > 512_000:
            return False
        parser = RobotFileParser()
        parser.parse(data.decode("utf-8", errors="replace").splitlines())
        return parser.can_fetch(AGENT, url)

    async def _extract(self, data, kind):
        import sys

        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "equitylens.live.extract",
            kind,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            output, _ = await asyncio.wait_for(process.communicate(data), timeout=12)
        except BaseException:
            if process.returncode is None:
                process.kill()
            await process.wait()
            raise
        if process.returncode:
            raise ValueError("Source could not be safely extracted.")
        import json

        return json.loads(output)

    async def read(self, source):
        async with self.slots:
            try:
                return await asyncio.wait_for(self._read(source), timeout=23)
            except (
                ValueError,
                httpcore.NetworkError,
                httpcore.TimeoutException,
                httpcore.ProtocolError,
                OSError,
                TimeoutError,
            ):
                return {
                    "source_id": source.id,
                    "status": "unavailable",
                    "blocks": [],
                    "notice": "Source body unavailable, blocked, unsafe, or beyond processing limits.",
                }

    async def _read(self, source):
        url = checked_url(source.url)
        for _ in range(3):
            host = urlsplit(url).hostname
            if host in SKIP_HOSTS:
                return {
                    "source_id": source.id,
                    "status": "not_read",
                    "blocks": [],
                    "notice": "Aggregator/unsupported source: only the already supplied headline or excerpt is available.",
                }
            if not await self._robots_allowed(url):
                return {
                    "source_id": source.id,
                    "status": "robots_denied",
                    "blocks": [],
                    "notice": "Source body was not fetched because robots policy could not be satisfied.",
                }
            status, headers, data = await self.fetch(url)
            if status in {301, 302, 303, 307, 308}:
                url = checked_url(urljoin(url, headers.get("location", "")))
                continue
            if status != 200:
                raise ValueError("Source access denied or unavailable.")
            kind = headers.get("content-type", "").split(";")[0].strip().lower()
            if kind not in {"text/html", "application/xhtml+xml", "text/plain", "application/pdf"}:
                raise ValueError("Unsupported source type.")
            parsed = await self.extractor(data, kind)
            blocks = parsed.get("blocks", [])
            return {
                "source_id": source.id,
                "status": "read" if blocks else "empty",
                "url": url,
                "blocks": blocks,
                "notice": parsed.get("notice", ""),
            }
        raise ValueError("Source redirect limit reached.")
