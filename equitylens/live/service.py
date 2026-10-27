import asyncio
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from datetime import date
from decimal import Decimal
from difflib import SequenceMatcher

import httpx

# Research content stays in this process, including workflow state.
os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"

from typing_extensions import TypedDict

from .followup import select_followup_matches
from .providers import PublicSources, bounded_call, utcnow
from .rag_pipeline import RagPipeline, as_context
from .sec import configured as sec_configured
from .sec import reconcile
from .types import CompanyMatch


def retrieved_calculations(calculations, passages):
    available = {p["source_id"] for p in passages}
    return [c for c in calculations if all(identifier in available for identifier in c["inputs"])]


def generation_failure(exc):
    """Return a fixed, credential-safe reason for a failed model request."""
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status in {401, 403}:
            return "authentication", "The model API rejected the key or model access. Check server configuration."
        if status == 429:
            return "rate_limit", "The model API rate limit was reached. Wait before researching again."
        if status in {400, 404, 422}:
            return "request_rejected", "The model API rejected this request or model format. Check model compatibility."
        return "provider_error", "The model API returned an error. Try again later."
    if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
        return "timeout", "The model API timed out before completing the outlook."
    if isinstance(exc, httpx.RequestError):
        return "connection", "The model API could not be reached. Check the connection and try again."
    return "invalid_response", "The model API returned an incomplete or invalid structured answer."


class CompanyTokens:
    def __init__(self, clock=time.time):
        self.secret = secrets.token_bytes(32)
        self.clock = clock

    def issue(self, company):
        payload = json.dumps(
            {"company": company.model_dump(mode="json"), "expires": self.clock() + 900}, separators=(",", ":")
        ).encode()
        raw = base64.urlsafe_b64encode(payload).decode().rstrip("=")
        signature = hmac.new(self.secret, raw.encode(), hashlib.sha256).hexdigest()
        return raw + "." + signature

    def verify(self, token):
        try:
            raw, signature = token.rsplit(".", 1)
            expected = hmac.new(self.secret, raw.encode(), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(signature, expected):
                raise ValueError("Invalid company selection.")
            payload = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
            if payload["expires"] < self.clock():
                raise ValueError("Company selection expired. Search again.")
            return CompanyMatch.model_validate(payload["company"])
        except (KeyError, TypeError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError("Invalid company selection. Search again.") from exc


class SessionStore:
    def __init__(self, ttl=1800, capacity=30, clock=time.monotonic):
        self.ttl, self.capacity, self.clock = ttl, capacity, clock
        self._items = {}
        self._lock = threading.RLock()

    def purge(self):
        with self._lock:
            for sid in [sid for sid, item in self._items.items() if item["expires"] <= self.clock()]:
                index = self._items[sid].get("index")
                if index is not None:
                    index.clear()
                del self._items[sid]

    def create(self, report, index=None):
        with self._lock:
            self.purge()
            if len(self._items) >= self.capacity:
                raise ValueError("Research session capacity reached. Clear a session or retry after expiry.")
            sid, token = secrets.token_urlsafe(18), secrets.token_urlsafe(32)
            self._items[sid] = {
                "digest": hashlib.sha256(token.encode()).digest(),
                "report": report,
                "index": index,
                "expires": self.clock() + self.ttl,
            }
            return sid, token

    def get(self, sid, token):
        with self._lock:
            self.purge()
            item = self._items.get(sid)
            if not item or not hmac.compare_digest(item["digest"], hashlib.sha256(token.encode()).digest()):
                raise KeyError("Session not found, expired, or inaccessible.")
            return item["report"]

    def delete(self, sid, token):
        with self._lock:
            self.get(sid, token)
            index = self._items[sid].get("index")
            if index is not None:
                index.clear()
            del self._items[sid]

    def get_index(self, sid, token):
        with self._lock:
            self.get(sid, token)
            return self._items[sid].get("index")

    def clear(self):
        with self._lock:
            for item in self._items.values():
                if item.get("index") is not None:
                    item["index"].clear()
            self._items.clear()


def deduplicate(items):
    result = []
    for item in items:
        normalized = item.title.casefold().removesuffix(" - " + item.publisher.casefold()).strip()
        existing = next(
            (
                e
                for e in result
                if e.url == item.url
                or SequenceMatcher(
                    None, normalized, e.title.casefold().removesuffix(" - " + e.publisher.casefold()).strip()
                ).ratio()
                > 0.94
            ),
            None,
        )
        if existing:
            if item.category not in existing.categories:
                existing.categories.append(item.category)
            continue
        item.id = f"S{len(result) + 1}"
        item.categories = [item.category]
        result.append(item)
    return result


def calculate(rows):
    calculations = []
    annual = [r for r in rows if r["period_type"] == "12M"]
    revenues = sorted(
        [r for r in annual if r["metric"] == "TotalRevenue"], key=lambda r: r["period_end"], reverse=True
    )
    if not revenues:
        return calculations
    latest = revenues[0]
    prev = next(
        (
            r
            for r in revenues[1:]
            if r["currency"] == latest["currency"]
            and 330
            <= (date.fromisoformat(latest["period_end"]) - date.fromisoformat(r["period_end"])).days
            <= 400
        ),
        None,
    )
    if prev and Decimal(prev["value"]) > 0:
        value = (Decimal(latest["value"]) / Decimal(prev["value"]) - 1) * 100
        calculations.append(
            {
                "label": "Annual revenue growth",
                "value": str(value.quantize(Decimal(".01"))),
                "unit": "%",
                "period_end": latest["period_end"],
                "inputs": [latest["id"], prev["id"]],
                "formula": "(current revenue / previous annual revenue - 1) × 100",
            }
        )
    operating = next(
        (
            r
            for r in annual
            if r["metric"] == "OperatingIncome"
            and r["period_end"] == latest["period_end"]
            and r["currency"] == latest["currency"]
        ),
        None,
    )
    if operating and Decimal(latest["value"]) > 0:
        value = Decimal(operating["value"]) / Decimal(latest["value"]) * 100
        calculations.append(
            {
                "label": "Annual operating margin",
                "value": str(value.quantize(Decimal(".01"))),
                "unit": "%",
                "period_end": latest["period_end"],
                "inputs": [operating["id"], latest["id"]],
                "formula": "operating income / revenue × 100",
            }
        )
    return calculations


def validate_analysis(payload, evidence):
    known = {e.id: e for e in evidence}

    def normalized_punctuation(text):
        return text.translate(str.maketrans("‐‑‒–—−", "------"))

    def figures(text):
        text = normalized_punctuation(text)
        text = re.sub(r"\b\d{1,2}[ -]*months?\b", "", text, flags=re.IGNORECASE)
        pattern = r"(?<![A-Za-z0-9])[-+]?\d[\d,]*(?:\.\d+)?(?:[eE][+-]?\d+)?(?![A-Za-z0-9])"
        return {match.group().replace(",", "").lstrip("+") for match in re.finditer(pattern, text)}

    def periods(text):
        text = normalized_punctuation(text)
        return set(re.findall(r"\b(\d{1,2})[ -]*months?\b", text, flags=re.IGNORECASE)) | set(
            re.findall(r"\breporting period\s+(\d{1,2})M\b", text, flags=re.IGNORECASE)
        )

    def units(text):
        currency = set()
        if re.search(r"\bINR\b|₹", text, re.IGNORECASE):
            currency.add("INR")
        if re.search(r"\bUSD\b|\$", text, re.IGNORECASE):
            currency.add("USD")
        scales = set(re.findall(r"\b(?:crore|lakh|million|billion)\b", text.lower()))
        return currency | scales

    def unsupported_reporting_basis(text, refs):
        for basis in ("consolidated", "standalone"):
            if not re.search(rf"\b{basis}\b", text, re.IGNORECASE):
                continue
            if not any(
                re.search(rf"\b{basis}\b", known[ref].content, re.IGNORECASE)
                and not re.search(
                    r"consolidated/standalone basis not independently verified",
                    known[ref].content,
                    re.IGNORECASE,
                )
                for ref in refs
            ):
                return True
        return False

    sections, dropped, count = [], 0, 0
    for section in payload.get("sections", [])[:6]:
        claims = []
        for claim in section.get("claims", [])[:6]:
            text, refs = claim.get("text"), claim.get("source_ids")
            if (
                count >= 12
                or not isinstance(text, str)
                or not isinstance(refs, list)
                or not refs
                or not all(isinstance(ref, str) and ref in known for ref in refs)
                or not figures(text).issubset(
                    set().union(*(figures(known[ref].title + " " + known[ref].content) for ref in refs))
                )
                or not periods(text).issubset(
                    set().union(*(periods(known[ref].title + " " + known[ref].content) for ref in refs))
                )
                or not units(text).issubset(
                    set().union(*(units(known[ref].title + " " + known[ref].content) for ref in refs))
                )
                or unsupported_reporting_basis(text, refs)
            ):
                dropped += 1
                continue
            claims.append({"text": text[:1500], "source_ids": list(dict.fromkeys(refs))})
            count += 1
        if claims:
            sections.append({"title": str(section.get("title", "Analysis"))[:120], "claims": claims})
    return sections, dropped


def reporting_basis_gap(question, matches):
    """Give a cited abstention when provider facts cannot establish the requested basis."""
    requested = set(re.findall(r"\b(?:consolidated|standalone)\b", question, re.IGNORECASE))
    if not requested:
        return []
    unverified = [
        match
        for match in matches
        if "consolidated/standalone basis not independently verified" in match["text"].lower()
    ]
    if not unverified:
        return []
    for basis in requested:
        if any(
            re.search(rf"\b{basis}\b", match["text"], re.IGNORECASE)
            and "consolidated/standalone basis not independently verified" not in match["text"].lower()
            for match in matches
        ):
            return []
    return [
        {
            "title": "Answer",
            "claims": [
                {
                    "text": "The supplied financial observation does not verify the requested reporting basis.",
                    "source_ids": [unverified[0]["id"]],
                }
            ],
        }
    ]


def direct_financial_observation(question, matches):
    """Answer a narrow single-period amount from an exact provider display conversion."""
    year = re.search(r"20\d{2}", question)
    if not year or re.search(
        r"\b(?:consolidated|standalone|compare|versus|growth|why|how)\b|after GST|from operations",
        question,
        re.IGNORECASE,
    ):
        return []
    metric = "Revenue" if re.search(r"\btotal revenue\b", question, re.IGNORECASE) else None
    if metric is None:
        return []
    for match in matches:
        if match["category"] != "financial" or not match["title"].endswith("— Revenue"):
            continue
        content = match["text"]
        period = re.search(r"Reporting period 12M, ended (20\d{2}-\d{2}-\d{2})", content)
        display = re.search(
            r"Exact display conversion: ([\d,.]+) (USD million|INR crore) \(from provider amount\)",
            content,
        )
        if not period or not display or period.group(1)[:4] != year.group():
            continue
        amount, unit = display.groups()
        if (re.search(r"\bUSD\b|\bdollars?\b", question, re.IGNORECASE) and "USD" not in unit) or (
            re.search(r"\bINR\b|\brupees?\b|₹", question, re.IGNORECASE) and "INR" not in unit
        ) or (re.search(r"\bmillions?\b", question, re.IGNORECASE) and "million" not in unit) or (
            re.search(r"\bcrores?\b", question, re.IGNORECASE) and "crore" not in unit
        ):
            continue
        return [
            {
                "title": "Answer",
                "claims": [
                    {
                        "text": (
                            f"The provider reports {metric.lower()} of {amount} {unit} for the "
                            f"12-month period ended {period.group(1)}. The accounting basis is not independently verified."
                        ),
                        "source_ids": [match["id"]],
                    }
                ],
            }
        ]
    return []


class ResearchState(TypedDict, total=False):
    company: CompanyMatch
    days: int
    depth: str
    tavily: str
    eodhd: str
    model: str
    collected: dict
    rows: list
    evidence: list
    calculations: list
    report: dict
    rag_bundle: dict


class ResearchService:
    def __init__(self, settings, provider=None, rag=None):
        from langgraph.graph import END, START, StateGraph

        self.settings = settings
        self.provider = provider or PublicSources(settings=settings)
        self.tokens = CompanyTokens()
        self.sessions = SessionStore(ttl=settings.research_ttl_seconds)
        self.rag = rag or RagPipeline(settings)
        self.slots = asyncio.Semaphore(3)
        graph = StateGraph(ResearchState)
        graph.add_node("fetch_live_sources", self.collect)
        graph.add_node("validate_and_calculate", self.validate)
        graph.add_node("extract_chunk_embed_retrieve", self.build_rag)
        graph.add_node("compose_report", self.compose)
        graph.add_edge(START, "fetch_live_sources")
        graph.add_edge("fetch_live_sources", "validate_and_calculate")
        graph.add_edge("validate_and_calculate", "extract_chunk_embed_retrieve")
        graph.add_edge("extract_chunk_embed_retrieve", "compose_report")
        graph.add_edge("compose_report", END)
        self.graph = graph.compile()  # No checkpointer, database, disk cache, or remote trace.

    async def lookup(self, query, market):
        matches = await self.provider.lookup(query, market)
        return [{**c.model_dump(mode="json"), "company_token": self.tokens.issue(c)} for c in matches]

    async def collect(self, state):
        company, days = state["company"], state["days"]
        limit = 18 if state["depth"] == "detailed" else 10
        tasks = {
            "stock": self.provider.stock(company),
            "financials": self.provider.financials(company),
            "news": self.provider.news(company, days, "news", limit),
            "projects": self.provider.news(company, max(days, 30), "projects", 10),
            "industry": self.provider.news(company, max(days, 30), "industry", 8),
            "profile": self.provider.profile(company, state["eodhd"]),
        }
        if state["tavily"]:
            tasks["research"] = self.provider.web_research(company, state["tavily"], state["depth"])
        if company.market == "US" and sec_configured(self.settings.sec_user_agent):
            tasks["filings"] = self.provider.sec_filings(company)
        if company.market == "IN" and company.symbol.endswith(".NS") and hasattr(self.provider, "nse_announcements"):
            tasks["nse_announcements"] = self.provider.nse_announcements(company, days)
        values = await asyncio.gather(*(bounded_call(task) for task in tasks.values()))
        collected = dict(zip(tasks, values, strict=True))
        return {"collected": collected}

    def validate(self, state):
        rows = state["collected"].get("financials") or []
        for i, row in enumerate(rows, 1):
            row["id"] = f"F{i}"
        evidence = []
        for kind in ("news", "projects", "industry", "research"):
            evidence.extend(state["collected"].get(kind) or [])
        evidence = deduplicate(evidence)
        filings = state["collected"].get("filings") or {}
        evidence.extend(filings.get("evidence", []))
        announcements = state["collected"].get("nse_announcements") or []
        for i, item in enumerate(announcements, 1):
            item.id = f"NSE{i}"
        evidence.extend(announcements)
        return {"rows": rows, "evidence": evidence, "calculations": calculate(rows)}

    async def compose(self, state):
        gaps = []
        stock = state["collected"].get("stock")
        if stock:
            gaps.extend(stock["gaps"])
        for name, value in state["collected"].items():
            if value is None:
                gaps.append(f"{name.capitalize()} source was unavailable or returned no supported data.")
        if not state["rows"]:
            gaps.append("No financial history available from the live source for this company.")
        if not any("news" in e.categories for e in state["evidence"]):
            gaps.append(f"No matching, dated news found in the last {state['days']} days.")
        if not state["tavily"]:
            gaps.append(
                "Web research API is not configured. Company announcements, management guidance, and full business research are incomplete; news-feed items are headlines only."
            )
        if state["company"].market == "US":
            if not sec_configured(self.settings.sec_user_agent):
                gaps.append("SEC filing facts are disabled. Set SEC_USER_AGENT to a project name and real contact email.")
            elif not (state["collected"].get("filings") or {}).get("facts"):
                gaps.append("No supported annual 10-K SEC XBRL facts were retrieved for this company.")
        else:
            gaps.append("Official Indian financial filing facts are not integrated; provider figures remain unreconciled.")
            if state["company"].symbol.endswith(".NS") and not state["collected"].get("nse_announcements"):
                gaps.append("No ticker-matched NSE announcements were found in the rolling RSS window; older filings may exist.")
        sections = []
        context = state["rag_bundle"]["context"]
        if state["rag_bundle"]["summary"]["notice"]:
            gaps.append(state["rag_bundle"]["summary"]["notice"])
        if state["rag_bundle"]["summary"].get("body_sources_read") == 0:
            gaps.append(
                "No full company article or filing body was read for this report; news and project coverage may rely on headlines or excerpts."
            )
        attempted = self.settings.generation_configured(state["model"]) and bool(context)
        failure_code = ""
        failure_message = ""
        if attempted:
            try:
                payload = await asyncio.wait_for(
                    self.provider.synthesize(
                        state["company"],
                        context,
                        retrieved_calculations(
                            state["calculations"], state["rag_bundle"]["summary"]["selected_passages"]
                        ),
                        state["model"],
                    ),
                    timeout=self.settings.llm_timeout_seconds + 5,
                )
            except (httpx.HTTPError, TimeoutError, ValueError, TypeError, KeyError, AttributeError) as exc:
                payload = None
                failure_code, failure_message = generation_failure(exc)
            if isinstance(payload, dict):
                try:
                    sections, dropped = validate_analysis(payload, context)
                    if dropped:
                        gaps.append(
                            f"{dropped} generated claims were excluded because citations or cited figures failed validation."
                        )
                except (TypeError, AttributeError):
                    failure_code = "invalid_response"
                    failure_message = "The model returned an invalid report structure."
                if not sections and not failure_code:
                    failure_code = "claims_rejected" if dropped else "no_claims"
                    failure_message = (
                        "All generated claims failed evidence checks. Try a narrower question about the retrieved evidence."
                        if dropped
                        else "The model returned no cited claims for this report. Try a specific question."
                    )
            else:
                if not failure_code:
                    failure_code = "invalid_response"
                    failure_message = "The model API did not return a structured answer."
            if failure_message:
                gaps.append(failure_message)
        if not sections:
            gaps.append(
                "No AI-written outlook was generated. Live evidence and deterministic financial calculations are shown directly."
            )
        report = {
            "company": state["company"].model_dump(mode="json"),
            "researched_at": utcnow().isoformat(),
            "news_days": state["days"],
            "project_days": max(state["days"], 30),
            "profile": state["collected"].get("profile"),
            "stock": stock,
            "financials": state["rows"],
            "calculations": state["calculations"],
            "filing_reconciliation": reconcile(
                state["rows"], (state["collected"].get("filings") or {}).get("facts", [])
            ),
            "evidence": [e.model_dump(mode="json") for e in state["evidence"]],
            "rag": state["rag_bundle"]["summary"],
            "rag_sources": [e.model_dump(mode="json") for e in state["rag_bundle"]["sources"]],
            "analysis": sections,
            "generation": {
                "state": "completed"
                if sections
                else "unavailable"
                if attempted
                else "no_evidence"
                if self.settings.generation_configured(state["model"])
                else "disabled",
                "model": state["model"] or None,
                "api_mode": self.settings.llm_api_mode,
                "failure_code": failure_code or None,
                "message": failure_message or None,
            },
            "gaps": gaps,
            "status": "partial" if gaps else "complete",
            "source_notes": [
                "Public Yahoo Finance endpoints and Google News RSS are unofficial, best-effort development sources. Availability and coverage can change.",
                "RSS evidence contains headlines, not article-body verification. Google News links may redirect to the publisher.",
                "NSE announcement items, when available, are official exchange feed summaries linked to issuer documents. The rolling feed is not a complete filing archive.",
                "Financial amounts retain provider periods and currencies. US SEC XBRL comparison checks matched annual amounts only; reporting basis and concept equivalence still need review.",
                "Stock quotes retain trading timestamps and exchange time zones; provider delay is unknown. Valuations use dated financial inputs and are not recommendations.",
                "AI citations and literal figures are checked against cited passages; semantic support is not independently guaranteed. Inspect linked evidence.",
                "No downloaded company reports, financial data or news are written to disk by this research flow. External providers have their own retention policies.",
            ],
            "workflow": [
                "Fetched live sources",
                "Checked dates and source identity",
                "Deduplicated coverage",
                "Extracted and chunked available source content",
                "Built temporary semantic and keyword index",
                "Retrieved bounded evidence context",
                "Calculated metrics from matched periods",
                "Assembled evidence report",
            ],
            "follow_up_questions": [
                "Which developments are confirmed by official company filings?",
                "What assumptions would have to hold for growth to accelerate?",
                "Which competitive or regulatory changes could weaken the business?",
            ],
        }
        return {"report": report}

    async def build_rag(self, state):
        bundle = await self.rag.build(
            state["company"],
            state["evidence"],
            state["rows"],
            state["collected"].get("profile"),
            state["depth"],
            stock=state["collected"].get("stock"),
        )
        return {"rag_bundle": bundle}

    async def research(self, request):
        company = self.tokens.verify(request.company_token)
        async with self.slots:
            result = await asyncio.wait_for(
                self.graph.ainvoke(
                    {
                        "company": company,
                        "days": request.news_days,
                        "depth": request.depth,
                        "tavily": request.tavily_api_key.get_secret_value()
                        if request.tavily_api_key
                        else self.settings.tavily_api_key,
                        "eodhd": request.eodhd_api_key.get_secret_value()
                        if request.eodhd_api_key
                        else self.settings.eodhd_api_key,
                        "model": request.llm_model or self.settings.llm_model,
                    },
                    config={"recursion_limit": 8},
                ),
                timeout=180,
            )
        sid, token = self.sessions.create(result["report"], result["rag_bundle"]["index"])
        return {
            "session_id": sid,
            "session_token": token,
            "expires_in_seconds": self.settings.research_ttl_seconds,
            "report": result["report"],
        }

    async def follow_up(self, sid, token, question, model=""):
        report = self.sessions.get(sid, token)
        index = self.sessions.get_index(sid, token)
        candidates = await asyncio.to_thread(index.search, question, top_k=10) if index is not None else []
        matches = select_followup_matches(question, candidates)
        requested_year = re.search(r"20\d{2}", question)
        if not matches and requested_year:
            closest = next((item for item in candidates if item["category"] == "financial"), None)
            if closest is not None:
                self.sessions.get(sid, token)
                return {
                    "question": question,
                    "analysis": [
                        {
                            "title": "Answer",
                            "claims": [
                                {
                                    "text": "The supplied evidence does not cover the requested reporting period.",
                                    "source_ids": [closest["id"]],
                                }
                            ],
                        }
                    ],
                    "matches": [closest],
                    "retrieval": index.summary() if index is not None else {},
                    "failure_code": None,
                    "notice": "Uses this session’s evidence only. Run fresh research for updates.",
                }
        direct = direct_financial_observation(question, matches)
        if direct:
            self.sessions.get(sid, token)
            return {
                "question": question,
                "analysis": direct,
                "matches": matches,
                "retrieval": index.summary() if index is not None else {},
                "failure_code": None,
                "notice": "Uses this session’s evidence only. Run fresh research for updates.",
            }
        evidence = as_context(matches)
        sections = []
        chosen_model = model or self.settings.llm_model
        attempted = self.settings.generation_configured(chosen_model) and bool(evidence)
        failure_code = ""
        failure_message = ""
        if attempted:
            try:
                payload = await asyncio.wait_for(
                    self.provider.synthesize(
                        CompanyMatch.model_validate(report["company"]),
                        evidence,
                        retrieved_calculations(report["calculations"], matches),
                        chosen_model,
                        question=question,
                    ),
                    timeout=self.settings.llm_timeout_seconds + 5,
                )
            except (httpx.HTTPError, TimeoutError, ValueError, TypeError, KeyError, AttributeError) as exc:
                payload = None
                failure_code, failure_message = generation_failure(exc)
            if isinstance(payload, dict):
                try:
                    sections, dropped = validate_analysis(payload, evidence)
                except (TypeError, AttributeError):
                    failure_code = "invalid_response"
                    failure_message = "The model returned an invalid answer structure."
                if not sections and not failure_code:
                    sections = reporting_basis_gap(question, matches)
                if not sections and not failure_code:
                    failure_code = "claims_rejected" if dropped else "no_claims"
                    failure_message = (
                        "The model's answer failed citation or figure checks. Retrieved evidence is shown below."
                        if dropped
                        else "The model returned no cited answer. Try a more specific question."
                    )
            elif not failure_code:
                failure_code = "invalid_response"
                failure_message = "The model API did not return a structured answer."
        self.sessions.get(sid, token)  # Do not return an answer after concurrent deletion or expiry.
        return {
            "question": question,
            "analysis": sections,
            "matches": matches,
            "retrieval": index.summary() if index is not None else {},
            "failure_code": failure_code or None,
            "notice": "Uses this session’s evidence only. Run fresh research for updates."
            if sections
            else "No sufficiently relevant source passages were found. Run fresh research or try a more specific question."
            if not evidence
            else failure_message
            if attempted
            else "AI answers are optional. Configure your frontier-model API key and model when needed. Relevant source passages are shown below.",
        }

    async def retrieve(self, sid, token, question, top_k=6):
        self.sessions.get(sid, token)
        index = self.sessions.get_index(sid, token)
        passages = await asyncio.to_thread(index.search, question, top_k=top_k) if index is not None else []
        self.sessions.get(sid, token)
        return {
            "question": question,
            "passages": passages,
            "retrieval": index.summary() if index else {},
            "notice": "Retrieved evidence only; similarity scores are not confidence probabilities."
            if passages
            else "No sufficiently relevant passages found in this session. Refresh research or add better sources.",
        }
