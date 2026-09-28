"""No tracked file contains a literal string shaped like a Google API key.

GitHub secret scanning alerts on the SHAPE, whatever the value means. Two test
fixtures carried a deliberately fake `AIza…` key to prove the template's own
detectors work, and GitHub opened an alert for each copy — in this repository
and in a service generated from it. Nothing leaked, but an alert that is
always a false positive teaches its reader to dismiss alerts. Fixtures build
the string at runtime instead ("AIza" + "Example" * 5), which exercises the
same detector and never matches as a literal.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
# The shape GitHub's `google_api_key` scanner reports, assembled here so this
# file does not contain it either.
_GCP_KEY = re.compile("AI" + r"za[0-9A-Za-z_\-]{35}")


def _tracked() -> list[Path]:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=REPO_ROOT, capture_output=True, text=True, check=True).stdout
    return [REPO_ROOT / p for p in out.split("\0") if p]


def test_the_pattern_matches_a_key_shaped_string() -> None:
    """A scanner that matches nothing passes everything."""
    assert _GCP_KEY.search("AIza" + "Example" * 5)


def test_no_tracked_file_carries_a_literal_key_shape() -> None:
    hits = []
    for path in _tracked():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
            continue
        for match in _GCP_KEY.finditer(text):
            hits.append(f"{path.relative_to(REPO_ROOT)}:{text[: match.start()].count(chr(10)) + 1}")
    assert not hits, (
        "literal Google-API-key-shaped strings — build test fixtures at runtime "
        '("AIza" + "Example" * 5) so secret scanning does not alert on them: ' + ", ".join(hits)
    )
