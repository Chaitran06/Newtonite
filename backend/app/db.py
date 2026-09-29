from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app import config

engine = create_engine(
    config.DATABASE_URL,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    """One session per request. Services never commit; the request handler commits
    exactly once after all writes (item change + history event + idempotency record)
    succeeded, so those writes are atomic. Any exception rolls everything back."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
