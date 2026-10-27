# Session-based RAG — version 0.3

Implemented and verified on 26 October 2026.

## Workflow

Live source collection → financial validation → bounded source extraction → passage chunking → local embeddings → temporary hybrid index → retrieved context → optional model generation.

The application remains on-demand: no preloaded company records, persistent document corpus, or saved company vectors are required. LangGraph has an explicit `extract_chunk_embed_retrieve` node. It uses no persistent checkpointer.

## What the index contains

- Live headlines, retaining their headline-only label.
- Available search excerpts.
- Reported financial observations, including currency, period, value, provider basis and observation ID.
- Available provider business profiles.
- Extracted HTML/PDF excerpts where source access and robots policy permit reading.

Each passage has a unique ID such as `S4:C002`, its original source ID, URL, location, evidence level, and timestamps. Extraction is not independent factual verification. Financial source data remains provider-reported and unreconciled against filings.

## Local embedding and retrieval

The installed encoder is `BAAI/bge-small-en-v1.5` through FastEmbed and ONNX Runtime, producing 384-dimensional vectors on CPU. General model weights default to `.cache/models` inside the project; this computer retains its existing `work/model-cache` through an ignored local configuration override. Company text is processed locally and is not saved in that cache. Request-time model loading is local-files-only; explicit setup performs any model download.

The index holds at most 250 passages. Passages are up to 750 characters with 150-character overlap. Large documents are capped and their excerpts are interleaved with other sources rather than occupying the entire corpus.

Queries combine semantic cosine similarity and BM25. Only positive keyword matches contribute to the keyword ranking, preventing document-order bias when a query has no exact matches. Reciprocal-rank fusion combines the rankings, and maximal marginal relevance encourages diversity. Retrieval allows at most two passages per source and enforces a character budget.

Initial report context combines four research questions into at most 12 passages and 8,500 characters. Follow-up retrieval returns at most six passages by default and 6,500 characters. Similarity scores are ranking signals, not calibrated probabilities. Thresholds are initial development settings, not an established financial-research benchmark.

If weights are unavailable, lexical fallback is explicitly labelled. The system does not claim semantic retrieval succeeded when corpus embedding failed.

## Source reader

The reader is bounded to four sources at most, two for quick research, and two concurrent reads. It respects robots policy, blocks unsupported aggregators, and never bypasses denied access. Public-IP validation happens at TCP connection time; the connection is pinned to a validated address while the original hostname is retained for TLS/SNI verification. Every redirect goes through the same checks.

HTML and PDF extraction happens in a short-lived subprocess using stdin/stdout. No source files are created. Inputs are capped at 3 MB; extracted text at 40,000 characters; PDFs at 20 pages. There is no OCR. This limits work but is not a hardened hostile-file sandbox with operating-system memory quotas.

Source outcomes are visible in the Retrieved evidence tab. If a page is unavailable, the corpus retains only evidence actually supplied by the source; it does not turn a headline into an invented article body.

## Generation

The optional frontier-model API adapter receives only retrieved passage IDs, text, source levels and locations. Financial calculations are supplied only when all their input observations occur in the retrieved context. Unknown or non-retrieved citation IDs are rejected.

Answer generation uses an API only and remains disabled until both LLM_API_KEY and LLM_MODEL are configured. A small Groq answer pilot using frozen public-filing facts is documented in [EVALUATION.md](EVALUATION.md); it does not test the full live-source workflow. The API connector and setup guide are provided for repository users; see [API-MODELS.md](API-MODELS.md). Controlled provider tests confirm the model receives only the retrieved subset, structured citation IDs are constrained, invalid citations are removed, and disabled mode makes no model connection. Without a model, the app still provides functioning semantic retrieval and inspectable passages. Citation and literal-figure checks do not prove semantic entailment.

## Retention and access

The index is stored alongside its token-protected report in process memory. Clearing, expiry, or application shutdown clears passages, token indexes and vectors; vector buffers are overwritten before references are removed. This does not promise operating-system-level secure erasure of all copied memory. User-exported reports and external providers have their own retention behavior.

The source index cannot be retrieved by guessing a report ID: the matching bearer token is required. Retrieval and generation recheck the session after processing so a concurrently deleted or expired report is not returned.

## Verified behavior

- All 60 automated tests passed, including chunk/source mapping, paraphrase retrieval, zero-keyword ranking, context bounds, expiry and deletion, source access rules, source extraction, session isolation, and retrieval-limited generation.
- The actual local embedding model retrieved the correct illustrative passages for three paraphrased questions about operating finance, manufacturing expansion and borrowing. An unrelated cake-recipe question produced zero hits in that check.
- The live safe reader successfully extracted the public Microsoft annual-report HTML page with block locations, without saving source bytes.
- Live Tesla research created 92 passages; live Reliance research created 73 passages in the verification run. Both used the real 384-dimensional embedding model, and cash-generation questions retrieved financial observations.
- Those particular Tesla/Reliance runs read zero full source bodies: their available corpus consisted of actual headlines, provider observations and available profile material. The reader did not claim otherwise.
- Streamlit AppTest passed report display, the Retrieved evidence question form, dynamic company search, and explicit session clearing.
- The real stdio MCP transport exposed four read-only tools, including `search_research`, and resolved Tesla live.

Counts and upstream content change between requests. Live optional search-API and generation-model branches still need private API credentials and separate evaluation.

## Reproduce

```powershell
.\.venv\Scripts\python.exe scripts\setup_rag_model.py
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe scripts\check_semantic_model.py
.\.venv\Scripts\python.exe scripts\check_public_reader.py
.\.venv\Scripts\python.exe scripts\smoke_on_demand.py
.\.venv\Scripts\python.exe scripts\smoke_mcp.py
```

The last three checks make public network requests. They do not save company research reports.
