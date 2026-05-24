# ruff: noqa: E402
"""Tests for `--db <path>` flag and `LAST30DAYS_DB_PATH` env-var fallback.

When `/landscape` (or any orchestrator) wraps the engine to drive multiple
per-engagement runs, every spoke subprocess used to persist into the single
shared store at `~/.local/share/last30days/research.db`. This file pins the
fix: a new `--db <path>` flag (and `LAST30DAYS_DB_PATH` env var as a
fallback) lets the caller route persistence to an engagement-private DB
without leaking into the default store.

The tests cover two layers:

1. **Pure unit tests** against ``store._get_db_path()`` — the resolver that
   every persistence helper funnels through. Cheap, hermetic, no subprocess.
2. **End-to-end engine invocations** with ``--store`` + ``--db`` (and env-var
   permutations) to confirm the flag actually reaches ``persist_report``.

Mirrors the precedence contract documented in CONFIGURATION.md: flag wins
over env var wins over default. Empty strings fall through (DB path '' has
no semantic meaning, unlike --save-dir '' which suppresses save).

Issue: https://github.com/dzivkovi/horizon-scanner/issues/2
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "skills" / "last30days" / "scripts"

# Make `import store` resolve to the engine's module for the unit-test layer.
sys.path.insert(0, str(SCRIPTS_DIR))

import store  # noqa: E402  (path mutation above)


def _engine_path() -> Path:
    return SCRIPTS_DIR / "last30days.py"


def _run_engine(
    topic: str,
    extra_argv: list[str],
    env_overrides: dict[str, str],
) -> subprocess.CompletedProcess:
    cmd = [
        sys.executable,
        str(_engine_path()),
        topic,
        "--mock",
        "--emit=md",
        "--store",
        *extra_argv,
    ]
    # Scrub LAST30DAYS_DB_PATH (and LAST30DAYS_MEMORY_DIR, which other tests
    # exercise) from the inherited parent env so a developer who exports it
    # doesn't accidentally satisfy the negative tests. Each test re-introduces
    # the env var explicitly through env_overrides when needed.
    scrubbed = {"LAST30DAYS_DB_PATH", "LAST30DAYS_MEMORY_DIR", "LAST30DAYS_STORE"}
    base = {k: v for k, v in os.environ.items() if k not in scrubbed}
    env = {**base, "LAST30DAYS_SKIP_PREFLIGHT": "1", **env_overrides}
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        env=env,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def _count_findings(db_path: Path) -> int:
    """Return how many rows the findings table holds, or -1 if DB absent."""
    if not db_path.exists():
        return -1
    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute("SELECT COUNT(*) FROM findings").fetchone()[0]
    finally:
        conn.close()


class GetDbPathResolverTests(unittest.TestCase):
    """Pure unit tests for store._get_db_path() — no subprocess, no engine."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="l30d-dbpath-resolver-"))
        self.original_override = store._db_override
        self.original_env = os.environ.pop("LAST30DAYS_DB_PATH", None)

    def tearDown(self) -> None:
        store._db_override = self.original_override
        if self.original_env is not None:
            os.environ["LAST30DAYS_DB_PATH"] = self.original_env
        else:
            os.environ.pop("LAST30DAYS_DB_PATH", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_default_path_when_nothing_set(self) -> None:
        """No override, no env var → fall back to ~/.local/share/last30days/research.db."""
        self.assertEqual(store._get_db_path(), store.DB_PATH)

    def test_in_process_override_wins_over_env_var(self) -> None:
        """_db_override (tests / engine main) wins over LAST30DAYS_DB_PATH."""
        override_path = self.tmp / "override.db"
        env_path = self.tmp / "from-env.db"
        store._db_override = override_path
        os.environ["LAST30DAYS_DB_PATH"] = str(env_path)
        self.assertEqual(store._get_db_path(), override_path)

    def test_env_var_used_when_no_override(self) -> None:
        """LAST30DAYS_DB_PATH supplies the path when no in-process override is set."""
        env_path = self.tmp / "from-env.db"
        os.environ["LAST30DAYS_DB_PATH"] = str(env_path)
        self.assertEqual(store._get_db_path(), env_path)

    def test_empty_env_var_falls_back_to_default(self) -> None:
        """LAST30DAYS_DB_PATH='' (accidental shell export) must not break things.

        Unlike --save-dir '' (which suppresses save), DB persistence has no
        natural 'off' state — if --store is on, a DB must exist somewhere.
        Empty env var is treated as 'no override', falling back to the default
        path rather than attempting to open ``Path('')``.
        """
        os.environ["LAST30DAYS_DB_PATH"] = ""
        self.assertEqual(store._get_db_path(), store.DB_PATH)

    def test_env_var_path_is_expanded(self) -> None:
        """~ in LAST30DAYS_DB_PATH is expanded to the home directory."""
        os.environ["LAST30DAYS_DB_PATH"] = "~/never-actually-used.db"
        resolved = store._get_db_path()
        self.assertEqual(resolved, Path.home() / "never-actually-used.db")


class DbFlagEngineTests(unittest.TestCase):
    """End-to-end engine invocations confirming --db routes persistence."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="l30d-dbpath-e2e-"))
        self.config_dir = self.tmp / "config"
        self.config_dir.mkdir()
        self.flag_db = self.tmp / "flag.db"
        self.env_db = self.tmp / "env.db"

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_dotenv(self, contents: str) -> None:
        (self.config_dir / ".env").write_text(contents, encoding="utf-8")

    def test_flag_routes_persistence_to_custom_path(self) -> None:
        """--db <path> + --store writes findings to the custom DB, not the default."""
        result = _run_engine(
            topic="OpenAI",
            extra_argv=["--db", str(self.flag_db)],
            env_overrides={"LAST30DAYS_CONFIG_DIR": ""},
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertGreater(
            _count_findings(self.flag_db), 0,
            msg=f"No findings in custom DB at {self.flag_db}. stderr: {result.stderr}",
        )

    def test_env_var_routes_persistence_when_flag_missing(self) -> None:
        """LAST30DAYS_DB_PATH=<path> + --store writes to the env-specified DB."""
        result = _run_engine(
            topic="OpenAI",
            extra_argv=[],
            env_overrides={
                "LAST30DAYS_CONFIG_DIR": "",
                "LAST30DAYS_DB_PATH": str(self.env_db),
            },
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertGreater(
            _count_findings(self.env_db), 0,
            msg=f"No findings in env-routed DB at {self.env_db}. stderr: {result.stderr}",
        )

    def test_flag_wins_over_env_var(self) -> None:
        """--db beats LAST30DAYS_DB_PATH when both are supplied."""
        result = _run_engine(
            topic="OpenAI",
            extra_argv=["--db", str(self.flag_db)],
            env_overrides={
                "LAST30DAYS_CONFIG_DIR": "",
                "LAST30DAYS_DB_PATH": str(self.env_db),
            },
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertGreater(
            _count_findings(self.flag_db), 0,
            msg=f"Flag-targeted DB empty — precedence broken. stderr: {result.stderr}",
        )
        self.assertEqual(
            _count_findings(self.env_db), -1,
            msg="Env-targeted DB got created when flag was explicit — precedence broken.",
        )

    def test_dotenv_value_used_when_neither_flag_nor_shell_env_set(self) -> None:
        """LAST30DAYS_DB_PATH in ~/.config/last30days/.env supplies the path."""
        self._write_dotenv(f"LAST30DAYS_DB_PATH={self.env_db}\n")
        result = _run_engine(
            topic="OpenAI",
            extra_argv=[],
            env_overrides={"LAST30DAYS_CONFIG_DIR": str(self.config_dir)},
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertGreater(
            _count_findings(self.env_db), 0,
            msg=f"No findings at .env-supplied DB {self.env_db}. stderr: {result.stderr}",
        )

    def test_default_db_untouched_when_flag_supplies_alternate_path(self) -> None:
        """Smoke check that the engine never silently falls back to ``DB_PATH``.

        We can't safely write to the real ~/.local/share path from a test,
        so this asserts the negative on the env-supplied dir: when --db
        points at a custom location, no DB file appears beside it.
        """
        # Use a unique sibling path the engine could conceivably target if
        # the flag plumbing broke (e.g., dropped to default DB_DIR/research.db).
        decoy = self.tmp / "research.db"
        result = _run_engine(
            topic="OpenAI",
            extra_argv=["--db", str(self.flag_db)],
            env_overrides={"LAST30DAYS_CONFIG_DIR": ""},
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertFalse(
            decoy.exists(),
            msg=f"Decoy default-named DB was created at {decoy} — flag plumbing leaked.",
        )


if __name__ == "__main__":
    unittest.main()
