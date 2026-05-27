"""Convention enforcement for `log.source_log(..., tty_only=False)`.

`log.source_log` defaults to `tty_only=True`, silently dropping every line
when stderr isn't a real TTY (every Claude Code / Codex / CI / captured-
output run). The default exists to keep interactive output uncluttered, but
it weaponizes any source module that forgets to opt out: error logs, query
heartbeats, and success signals all disappear.

dzivkovi/last30days-skill#13 surfaced ten source modules that had quietly
shipped with this bug. This test prevents the eleventh. Every
`log.source_log(...)` call site under `skills/last30days/scripts/lib/` must
pass `tty_only=False` explicitly. The cost is one kwarg per call; the value
is that source observability never goes silent again, even when the next
contributor copies an old `_log` helper template without thinking.

The companion bug 2 (`_FOOTER_SOURCES` whitelist omitting perplexity) is
covered separately in `test_render_v3.py::test_emoji_footer_includes_
perplexity_when_present`.
"""

import io
import pathlib
import re
import sys
import unittest
from unittest.mock import patch

from lib import bluesky, perplexity

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "skills" / "last30days" / "scripts" / "lib"

# Match `log.source_log(...)` call openings. The arg list is then walked
# manually to handle balanced parens across multi-line calls.
CALL_RE = re.compile(r"log\.source_log\(", re.MULTILINE)


def _extract_call_args(text: str, open_paren_idx: int) -> str:
    """Return the substring between the matching parens of a call.

    `open_paren_idx` points at the `(`. Walks paren depth so multi-line
    calls and nested parens (e.g. f-strings) are handled correctly.
    """
    assert text[open_paren_idx] == "("
    depth = 1
    i = open_paren_idx + 1
    while i < len(text) and depth > 0:
        c = text[i]
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        i += 1
    return text[open_paren_idx + 1 : i - 1]


class SourceLogConventionTests(unittest.TestCase):
    """Enforce the project convention at the codebase level."""

    def test_every_source_log_call_passes_tty_only_false(self):
        violations: list[str] = []
        for path in sorted(LIB_DIR.glob("*.py")):
            if path.name == "log.py":
                continue  # definition site; not a caller
            text = path.read_text(encoding="utf-8")
            for match in CALL_RE.finditer(text):
                open_paren = match.end() - 1
                args = _extract_call_args(text, open_paren)
                if "tty_only=False" not in args:
                    line = text[: match.start()].count("\n") + 1
                    violations.append(f"{path.name}:{line} — {args.strip()[:100]}")
        if violations:
            self.fail(
                "Source modules must call `log.source_log(..., tty_only=False)` so "
                "lines stay visible under non-TTY contexts (Claude Code, Codex, CI, "
                "captured output). See dzivkovi/last30days-skill#13 for the bug "
                "class and AGENTS.md for the convention. Violations:\n  - "
                + "\n  - ".join(violations)
            )


class PerplexityAndBlueskyVisibilityTests(unittest.TestCase):
    """Targeted regression tests for the two original bug instances.

    Kept alongside the convention test so a future refactor of either
    module immediately surfaces a regression if the opt-out is dropped.
    """

    def _captured_stderr_under_non_tty(self, log_callable) -> str:
        fake_stderr = io.StringIO()
        fake_stderr.isatty = lambda: False  # type: ignore[method-assign]
        with patch.object(sys, "stderr", fake_stderr):
            log_callable("visibility probe")
        return fake_stderr.getvalue()

    def test_perplexity_log_visible_under_non_tty(self):
        out = self._captured_stderr_under_non_tty(perplexity._log)
        self.assertIn("[Perplexity] visibility probe", out)

    def test_bluesky_log_visible_under_non_tty(self):
        out = self._captured_stderr_under_non_tty(bluesky._log)
        self.assertIn("[Bluesky] visibility probe", out)


if __name__ == "__main__":
    unittest.main()
