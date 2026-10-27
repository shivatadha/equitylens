from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker


def make_engine(url):
    engine = create_engine(url, pool_pre_ping=True)
    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def foreign_keys(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")

    return engine


def session_factory(engine):
    return sessionmaker(engine, expire_on_commit=False)
