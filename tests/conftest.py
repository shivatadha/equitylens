import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import make_url

from equitylens.api import create_app
from equitylens.config import Settings
from equitylens.database import make_engine, session_factory
from equitylens.models import Base
from equitylens.seed import seed


@pytest.fixture
def store(tmp_path):
    url = os.environ.get("TEST_DATABASE_URL", f"sqlite:///{tmp_path / 'test.sqlite'}")
    if not url.startswith("sqlite") and make_url(url).database != "equitylens_test":
        raise RuntimeError("Integration tests require the isolated equitylens_test database.")
    settings = Settings(database_url=url, data_dir=tmp_path / "objects", sec_user_agent="", _env_file=None)
    engine = make_engine(url)
    Base.metadata.create_all(engine)
    factory = session_factory(engine)
    with factory() as session:
        seed(session)
    yield factory, settings
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def client(store):
    factory, settings = store
    with TestClient(create_app(settings, factory)) as client:
        yield client
