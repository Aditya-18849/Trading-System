"""
SQLAlchemy database engine and session factory.

Updated to use NullPool to auto-defer connection management 
to Supabase's pgBouncer (Transaction Pooling).
"""
from sqlalchemy import create_engine, event, text
from sqlalchemy.pool import NullPool
from sqlalchemy.orm import sessionmaker, declarative_base

from app.config import settings

engine = create_engine(
    settings.database_url,
    poolclass=NullPool, # Disables SQLAlchemy pooling to let pgBouncer handle it
    connect_args={"options": "-c timezone=Asia/Kolkata"},
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    """FastAPI dependency: yields a DB session and guarantees it's closed."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()