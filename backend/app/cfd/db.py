"""CFD sessions on the shared Gearhead database.

CFD tables use the platform's engine and declarative `Base` (one database, one migration step at start-up in
`app.main`). The CFD code expects `expire_on_commit=False` (objects stay readable after commit, also in the solver
worker process), so it keeps its own session factory bound to the same engine.
"""
from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy.orm import Session, sessionmaker

from ..database import Base, engine  # noqa: F401  (Base re-exported for app.cfd.models)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Tables are created by the platform (`app.main`); kept for the worker / scripts that import it."""
    from . import models  # noqa: F401  (register tables)

    Base.metadata.create_all(engine)
