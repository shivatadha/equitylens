"""Run the same tests against a dedicated PostgreSQL test database."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from equitylens.config import Settings

url = make_url(Settings().database_url)
admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
with admin.connect() as connection:
    if not connection.scalar(text("SELECT 1 FROM pg_database WHERE datname='equitylens_test'")):
        connection.execute(text("CREATE DATABASE equitylens_test"))
admin.dispose()
os.environ["TEST_DATABASE_URL"] = url.set(database="equitylens_test").render_as_string(hide_password=False)
raise SystemExit(pytest.main(["-q", "tests"]))
