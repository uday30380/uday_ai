from alembic import context
from sqlalchemy import engine_from_config, pool

from app.database.models import Base

config = context.config
target_metadata = Base.metadata


def run_migrations_online() -> None:
    from app.database.session import make_engine
    connectable = make_engine(config.get_main_option("sqlalchemy.url"))
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True)
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
