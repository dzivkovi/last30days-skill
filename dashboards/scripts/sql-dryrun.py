"""Dry-run every panel SQL in dashboards/trends.yaml against research.db.

Strips datasette's `[[ AND ... ]]` optional-WHERE blocks (the "no parameter
bound" code path — i.e. the dashboard's cold-start view with no filter set),
then executes each query and reports row count + columns. Errors are printed
with the failing SQL for context.

Use this before committing any panel SQL change. It is faster than launching
datasette + curl-validating, and it catches the "renders but wrong" failure
modes (zero rows in a table panel, malformed SQL) that HTTP 200 alone misses.

Run from the repo root:

    python dashboards/scripts/sql-dryrun.py
    python dashboards/scripts/sql-dryrun.py --db /path/to/other.db

Exit codes: 0 = all panels OK, 1 = setup error, 2 = at least one panel failed.
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from pathlib import Path

import yaml

DEFAULT_DB = Path("C:/Users/danie/.local/share/last30days/research.db")
DEFAULT_YAML = Path(__file__).resolve().parents[1] / "trends.yaml"
OPTIONAL_WHERE = re.compile(r"\[\[.*?\]\]", re.DOTALL)


def strip_optional_where(sql: str) -> str:
    """Drop datasette `[[ ... ]]` blocks — equivalent to no params bound."""
    return OPTIONAL_WHERE.sub("", sql)


def extract_charts(yaml_path: Path) -> dict[str, str]:
    """Walk the datasette-dashboards YAML and return {chart_id: sql}."""
    doc = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    plugins = (doc or {}).get("plugins", {}) or {}
    dashboards = plugins.get("datasette-dashboards", {}) or {}
    charts: dict[str, str] = {}
    for dashboard in dashboards.values():
        for chart_id, chart in (dashboard.get("charts") or {}).items():
            charts[chart_id] = chart.get("query", "")
    return charts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB,
                        help=f"SQLite database (default: {DEFAULT_DB})")
    parser.add_argument("--yaml", type=Path, default=DEFAULT_YAML,
                        help=f"Dashboard YAML (default: {DEFAULT_YAML})")
    args = parser.parse_args()

    if not args.db.exists():
        print(f"FAIL: database missing at {args.db}")
        return 1
    if not args.yaml.exists():
        print(f"FAIL: yaml missing at {args.yaml}")
        return 1

    charts = extract_charts(args.yaml)
    if not charts:
        print(f"FAIL: no charts found in {args.yaml}")
        return 1

    print(f"Found {len(charts)} chart queries: {list(charts)}")
    conn = sqlite3.connect(str(args.db))
    failures: list[tuple[str, str, str]] = []
    for name, sql in charts.items():
        stripped = strip_optional_where(sql)
        try:
            cur = conn.execute(stripped)
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description] if cur.description else []
            print(f"  OK   {name:22s} -> {len(rows):3d} rows, cols={cols}")
        except Exception as exc:
            failures.append((name, str(exc), stripped))
            print(f"  FAIL {name:22s} -> {exc}")

    conn.close()
    if failures:
        print(f"\n{len(failures)} panel SQL failures:")
        for name, err, sql in failures:
            print(f"\n--- {name} ---\n{err}\n{sql}")
        return 2
    print("\nAll panels' SQL parses + executes cleanly.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
