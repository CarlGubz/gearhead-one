"""Mounts the CAD / CAM workbench (formerly the stand-alone CADWeb API) into the Gearhead app.

Routes live under ``/api/cad`` and require a signed-in user (see `app.scope`); tables are created with the platform's
(`app.main`). `migrate()` keeps CADWeb's small forward-only migration for the ``cad_`` tables: nullable columns
added in later versions are added to an existing table, and widened VARCHARs are widened on PostgreSQL.
"""
import logging

from fastapi import FastAPI
from sqlalchemy import inspect, text

from . import models  # noqa: F401  (registers tables)
from .database import Base, engine
from .routers import assemblies, cam, drawings, meta, parts, projects, simulations, sketches

log = logging.getLogger("gearhead.cad")


def migrate():
    insp = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not table.name.startswith("cad_") or not insp.has_table(table.name):
                continue
            cols = {c["name"]: c for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name not in cols and col.nullable:
                    ddl = col.type.compile(dialect=engine.dialect)
                    conn.execute(text(f'ALTER TABLE {table.name} ADD COLUMN "{col.name}" {ddl}'))
                    log.info("added column %s.%s", table.name, col.name)
                elif engine.dialect.name == "postgresql" and col.name in cols:
                    if col.nullable and not cols[col.name]["nullable"]:
                        conn.execute(text(f'ALTER TABLE {table.name} ALTER COLUMN "{col.name}" DROP NOT NULL'))
                    db_len = getattr(cols[col.name]["type"], "length", None)
                    new_len = getattr(col.type, "length", None)
                    if db_len and new_len and new_len > db_len:
                        conn.execute(text(f'ALTER TABLE {table.name} ALTER COLUMN "{col.name}" TYPE VARCHAR({new_len})'))


def mount(app: FastAPI) -> None:
    for r in (meta.router, projects.router, sketches.router, parts.router, assemblies.router, cam.router,
              simulations.router, drawings.router):
        app.include_router(r)
