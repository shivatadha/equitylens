"""Download general model weights only. No company text is cached here."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
os.environ["TOKENIZERS_PARALLELISM"] = "false"

from fastembed import TextEmbedding

from equitylens.config import Settings

settings = Settings()
model = TextEmbedding(
    model_name=settings.rag_embedding_model, cache_dir=str(settings.rag_model_cache), threads=2
)
vector = next(iter(model.passage_embed(["A general test sentence for the embedding installation."])))
print(f"Embedding model ready: {settings.rag_embedding_model}, dimensions={len(vector)}")
