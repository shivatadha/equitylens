"""Streamlit Community Cloud entrypoint for the single-process portfolio demo."""

import os
import runpy
from pathlib import Path

import streamlit as st

# Load root-level Cloud secrets into environment variables before Settings reads them.
try:
    st.secrets.to_dict()
except FileNotFoundError:
    pass  # Local smoke tests may use environment variables instead.

os.environ["EQUITYLENS_BACKEND"] = "embedded"
os.environ["EQUITYLENS_REQUIRE_DEMO_PASSWORD"] = "1"
os.environ.setdefault("RAG_DOWNLOAD_MODEL_IF_MISSING", "1")

runpy.run_path(str(Path(__file__).with_name("ui.py")), run_name="__main__")
