from alembic import context

from equitylens.config import Settings
from equitylens.database import make_engine
from equitylens.models import Base

engine = make_engine(Settings().database_url)
with engine.connect() as connection:
    context.configure(connection=connection, target_metadata=Base.metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()
