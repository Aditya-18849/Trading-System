"""
SQLAlchemy database engine and session factory.

Updated to use NullPool to auto-defer connection management 
to Supabase's pgBouncer (Transaction Pooling).
"""
from sqlalchemy import create_engine, event, text
from sqlalchemy.pool import NullPool
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.dialects.postgresql import UUID

from app.config import settings

@compiles(UUID, "sqlite")
def compile_uuid_sqlite(type_, compiler, **kw):
    return "VARCHAR(36)"

Base = declarative_base()

@event.listens_for(Base.metadata, "before_create")
def setup_sqlite_ddl(target, connection, **kw):
    if connection.dialect.name == "sqlite":
        for table in target.tables.values():
            for column in table.columns:
                if column.server_default is not None:
                    column.server_default = None

is_postgres = "postgres" in settings.database_url.lower()

engine_kwargs = {}
if is_postgres:
    engine_kwargs["poolclass"] = NullPool  # Disables SQLAlchemy pooling to let pgBouncer handle it
    engine_kwargs["connect_args"] = {"options": "-c timezone=Asia/Kolkata"}
elif "sqlite" in settings.database_url.lower():
    engine_kwargs["connect_args"] = {"check_same_thread": False}

engine = create_engine(
    settings.database_url,
    **engine_kwargs
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    """FastAPI dependency: yields a DB session and guarantees it's closed."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()