"""CAD sessions on the shared Gearhead database (same engine and declarative `Base` as the platform).

The CAD code expects `expire_on_commit=False` (rows stay readable after commit), so it keeps its own session factory.
"""
from sqlalchemy.orm import sessionmaker

from ..database import Base, engine  # noqa: F401  (Base re-exported for app.cad.models)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
