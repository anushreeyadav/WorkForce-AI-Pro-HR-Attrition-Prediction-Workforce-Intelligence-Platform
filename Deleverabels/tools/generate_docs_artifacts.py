"""Documentation artifacts generated from the actual code, with no database connection:
- 07_API_Documentation/openapi.json and endpoint_inventory.md (from app.openapi() and the routers' role dependencies);
- 08_Database_Schema/schema_mysql.sql (alembic upgrade head --sql: offline MySQL DDL, nothing executed),
  table_inventory.md and er_diagram.md (Mermaid) from the ORM metadata, migration_history.txt.
Placeholder settings only (the same test-only values the test suite uses); no .env is read for them."""

import io
import json
import os
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
os.chdir(BACKEND)
sys.path[:0] = [str(BACKEND), str(BACKEND / "tests")]
import conftest  # noqa: E402,F401  test-only placeholder environment
from fastapi.routing import APIRoute  # noqa: E402

from app.main import app  # noqa: E402
from app.models import Base  # noqa: E402
from tests.test_authorization_matrix import ROLE_GROUPS, ROUTERS, _route_dependencies  # noqa: E402

# --- OpenAPI and endpoint inventory -----------------------------------------------------------------------------------
schema = app.openapi()
(OUT / "07_API_Documentation" / "openapi.json").write_text(json.dumps(schema, indent=2))
rows = []
for module in ROUTERS:
    for route in module.router.routes:
        if not isinstance(route, APIRoute):
            continue
        roles, authenticated = _route_dependencies(route)
        who = ", ".join(sorted(r.value for r in roles)) if roles else ("any signed-in user" if authenticated else "public")
        for method in sorted(route.methods):
            op = schema["paths"][route.path][method.lower()]
            body = op.get("requestBody", {}).get("content", {})
            body_type = next(iter(body), "") if body else ""
            response = op.get("responses", {})
            ok = next((code for code in response if code.startswith("2")), "")
            rows.append((module.__name__.split(".")[-1], method, route.path, who, op.get("summary", ""), body_type, ok))
rows.append(("main", "GET", "/api/health", "public", "Health Check", "", "200"))
lines = ["# Endpoint inventory", "",
         f"Generated from the application code ({len(rows)} operations): methods and paths from `app.openapi()`, roles from "
         "each route's `require_roles` dependency (the same introspection the authorization-matrix test uses).", "",
         "| Module | Method | Path | Roles | Summary | Request body | Success |", "|---|---|---|---|---|---|---|"]
lines += [f"| {m} | {meth} | `{p}` | {who} | {s} | {b or '—'} | {ok} |" for m, meth, p, who, s, b, ok in rows]
(OUT / "07_API_Documentation" / "endpoint_inventory.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
print(f"openapi.json: {len(schema['paths'])} paths; endpoint_inventory.md: {len(rows)} operations")

# --- Database schema ----------------------------------------------------------------------------------------------------
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from alembic.script import ScriptDirectory  # noqa: E402

config = Config(str(BACKEND / "alembic.ini"))
buffer = io.StringIO()
with redirect_stdout(buffer):
    command.upgrade(config, "head", sql=True)
schema_dir = OUT / "08_Database_Schema"
schema_dir.mkdir(exist_ok=True)
(schema_dir / "schema_mysql.sql").write_text(
    "-- WorkForce AI Pro: MySQL DDL rendered OFFLINE by `alembic upgrade head --sql` (nothing was executed;\n"
    "-- no database was contacted). Schema only: no data, users or credentials.\n" + buffer.getvalue(), encoding="utf-8")
script = ScriptDirectory.from_config(config)
history = [f"{rev.revision}  <-  {rev.down_revision or 'base'}  {rev.doc.splitlines()[0] if rev.doc else ''}"
           for rev in reversed(list(script.walk_revisions()))]
(schema_dir / "migration_history.txt").write_text(
    f"Head: {', '.join(script.get_heads())}\nRevisions (oldest first): {len(history)}\n\n" + "\n".join(history) + "\n")

inventory = ["# Table inventory", "", f"Generated from the SQLAlchemy models (`app/models`, {len(Base.metadata.tables)} tables). "
             "PK = primary key, FK = foreign key (target and ON DELETE rule).", ""]
mermaid = ["# Entity relationship diagram", "", "Generated from the SQLAlchemy models (foreign keys). Render with any Mermaid "
           "viewer (GitHub and VS Code preview render it).", "", "```mermaid", "erDiagram"]
for name in sorted(Base.metadata.tables):
    table = Base.metadata.tables[name]
    inventory += [f"## {name}", "", "| Column | Type | Null | Key / default |", "|---|---|---|---|"]
    mermaid.append(f"    {name} {{")
    for column in table.columns:
        keys = []
        if column.primary_key:
            keys.append("PK")
        keys += [f"FK → {fk.column.table.name}.{fk.column.name} ({fk.ondelete or 'NO ACTION'})" for fk in column.foreign_keys]
        if column.server_default is not None:
            keys.append(f"default {column.server_default.arg.text if hasattr(column.server_default.arg, 'text') else column.server_default.arg}")
        inventory.append(f"| {column.name} | {column.type} | {'yes' if column.nullable else 'no'} | {'; '.join(keys) or ''} |")
        mermaid.append(f"        {str(column.type).split('(')[0].replace(' ', '_')} {column.name}{' PK' if column.primary_key else ''}{' FK' if column.foreign_keys else ''}")
    mermaid.append("    }")
    constraints = sorted(c.name for c in table.constraints if c.name and not c.name.startswith(("pk_", "fk_")))
    indexes = sorted(f"{i.name} ({', '.join(c.name for c in i.columns)}){' unique' if i.unique else ''}" for i in table.indexes)
    inventory += ["", f"Constraints: {', '.join(constraints) or '—'}", "", f"Indexes: {', '.join(indexes) or '—'}", ""]
for name in sorted(Base.metadata.tables):
    for fk in Base.metadata.tables[name].foreign_keys:
        nullable = fk.parent.nullable
        mermaid.append(f"    {fk.column.table.name} ||--o{{ {name} : \"{fk.parent.name}{' (optional)' if nullable else ''}\"")
mermaid.append("```")
(schema_dir / "table_inventory.md").write_text("\n".join(inventory) + "\n", encoding="utf-8")
(schema_dir / "er_diagram.md").write_text("\n".join(mermaid) + "\n", encoding="utf-8")
print(f"schema: {len(Base.metadata.tables)} tables; head {script.get_heads()}; {len(history)} revisions")
