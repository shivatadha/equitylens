"""Version the embedded service cache when backend source files change."""

from hashlib import sha256
from pathlib import Path

import streamlit as st

from .embedded_client import EmbeddedClient


def backend_revision(package_dir=None):
    package_dir = Path(package_dir) if package_dir is not None else Path(__file__).parent
    digest = sha256()
    for path in sorted(package_dir.rglob("*.py")):
        digest.update(path.relative_to(package_dir).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


@st.cache_resource(show_spinner=False, max_entries=1, on_release=lambda client: client.close())
def embedded_client(revision):
    # revision is intentionally part of the Streamlit cache key. The same
    # service survives UI reruns; a different backend build gets a fresh app.
    return EmbeddedClient()
