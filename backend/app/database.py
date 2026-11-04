"""
Database setup for Gearhead (FEA + CFD + CAD share one database).

PostgreSQL is the production database (set DATABASE_URL, e.g.
`postgresql://user:pass@host:5432/gearhead`). SQLite remains the zero-config
default for quick local hacking and the automated tests.

The CFD solver runs in separate worker processes that write progress into the
same database while the API reads it, so SQLite is opened in WAL mode with a
busy timeout (PostgreSQL needs nothing special).
"""
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, declarative_base

from .config import settings

_url = settings.sqlalchemy_url
if _url.startswith("sqlite"):
    engine = create_engine(_url, connect_args={"check_same_thread": False, "timeout": 30})

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _):  # pragma: no cover - trivial
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=30000")
        cur.close()
else:
    # pool_pre_ping survives DB restarts / idle-connection culling.
    engine = create_engine(_url, pool_pre_ping=True, pool_size=10, max_overflow=10)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
