"""Migration connections use the same environment configuration as the loader."""

from alembic import context

from indexlens.db.models import Base
from indexlens.db.session import database_engine


def run_migrations() -> None:
    supplied = context.config.attributes.get("connection")
    if supplied is not None:
        context.configure(connection=supplied, target_metadata=Base.metadata)
        with context.begin_transaction():
            context.run_migrations()
        return
    engine = database_engine()
    try:
        if context.is_offline_mode():
            context.configure(
                url=engine.url, target_metadata=Base.metadata, literal_binds=True
            )
            with context.begin_transaction():
                context.run_migrations()
        else:
            with engine.connect() as connection:
                context.configure(connection=connection, target_metadata=Base.metadata)
                with context.begin_transaction():
                    context.run_migrations()
    finally:
        engine.dispose()


run_migrations()
