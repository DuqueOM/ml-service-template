"""Contract — the deploy gate catches both shapes of undocumented configuration.

Why this exists
---------------
A deploy workflow that reads a value nobody was told to set fails on the first
real deployment, and the error names the consumer rather than the cause. Worse,
GitHub does **not** fall back from ``secrets.X`` to ``vars.X``: a value put in
the wrong place arrives as an empty string, so the wrong *channel* fails exactly
as silently as a missing entry.

Both were true in this repository, and neither was detectable, because nothing
compared what the workflows read against what the runbooks tell an adopter to
configure. Nobody had ever completed an L4 deployment, so nobody had found out.

* ``AWS_BUILD_ROLE_ARN`` is required by ``deploy-aws.yml``'s build job to push
  to ECR. It appeared in **no** runbook.
* ``AWS_ROLE_ARN`` is read from ``secrets`` while ``aws-irsa-setup.md`` §A.4
  said *"Add repository variables (NOT secrets — these are not sensitive)"* and
  listed it among them. The reasoning is right and the code disagreed.

The GCP side was consistent — runbook says variables, workflow reads ``vars`` —
which is what makes the AWS mismatch drift rather than design.

A note on the channel check
---------------------------
It is table-scoped and exact. An earlier proximity heuristic — nearest
preceding heading containing "secret" or "variable" — produced **eleven false
positives** against a tree with one real mismatch, because any document that
used the word "secret" in a sentence before listing a variable was flagged. A
control that is wrong eleven times out of twelve sends its reader to edit
documentation that was already correct. The test below pins the precision, not
just the detection.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
GATE = REPO_ROOT / "scripts" / "check_deploy_contract_documented.py"
DEPLOY_DIR = Path("templates/service/.github/workflows")
RUNBOOK = Path("docs/runbooks/aws-irsa-setup.md")


def _sandbox(tmp_path: Path) -> Path:
    """The workflows and docs the gate reads, copied so they can be broken."""
    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True)
    shutil.copy2(GATE, root / "scripts" / GATE.name)
    for rel in (DEPLOY_DIR, Path("docs/runbooks"), Path("templates/service/docs")):
        src = REPO_ROOT / rel
        if src.is_dir():
            shutil.copytree(src, root / rel, ignore=shutil.ignore_patterns("__pycache__"))
    docs = root / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    for md in (REPO_ROOT / "docs").glob("*.md"):
        shutil.copy2(md, docs / md.name)
    return root


def _run(root: Path) -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, str(root / "scripts" / GATE.name)],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=120,
    )
    return proc.returncode, proc.stdout + proc.stderr


def test_the_current_tree_passes(tmp_path: Path) -> None:
    """The control. Eleven false positives is what this guards against."""
    code, out = _run(_sandbox(tmp_path))
    assert code == 0, out
    assert "documented in the channel it is read from" in out


def test_an_undocumented_secret_is_rejected(tmp_path: Path) -> None:
    """AWS_BUILD_ROLE_ARN's original state: required, and named nowhere."""
    root = _sandbox(tmp_path)
    runbook = root / RUNBOOK
    text = runbook.read_text(encoding="utf-8")
    assert "AWS_BUILD_ROLE_ARN" in text, "the runbook no longer documents it; this control is stale"
    runbook.write_text(text.replace("AWS_BUILD_ROLE_ARN", "AWS_SOMETHING_ELSE"), encoding="utf-8")

    code, out = _run(root)
    assert code == 1, f"a secret documented nowhere was accepted:\n{out}"
    assert "AWS_BUILD_ROLE_ARN" in out
    assert "documented nowhere" in out


def test_the_wrong_channel_is_rejected(tmp_path: Path) -> None:
    """AWS_ROLE_ARN's original state: read from secrets, documented as a variable.

    This is the half that fails silently. A missing secret at least produces an
    obviously empty value; a value in the wrong channel looks configured.
    """
    root = _sandbox(tmp_path)
    runbook = root / RUNBOOK
    text = runbook.read_text(encoding="utf-8")

    # Move the row out of the Secrets table and into the Variables table, which
    # is exactly what the runbook said to do before this was fixed.
    row = [ln for ln in text.splitlines() if ln.startswith("| `AWS_ROLE_ARN`")]
    assert row, "AWS_ROLE_ARN is no longer a table row in the runbook; this control is stale"
    text = text.replace(row[0] + "\n", "", 1)
    text = text.replace(
        "| `AWS_REGION` | e.g. `us-east-1` |",
        "| `AWS_ROLE_ARN` | the CI role |\n| `AWS_REGION` | e.g. `us-east-1` |",
        1,
    )
    runbook.write_text(text, encoding="utf-8")

    code, out = _run(root)
    assert code == 1, f"a secret documented under Variables was accepted:\n{out}"
    assert "AWS_ROLE_ARN" in out
    assert "does NOT fall back" in out


def test_prose_mentions_do_not_declare_a_channel(tmp_path: Path) -> None:
    """The precision assertion, and the reason the first version was rewritten.

    A sentence about secrets that happens to precede a variable's table row must
    not make that variable look mis-channelled. Eleven names failed exactly this
    way under the proximity heuristic.
    """
    root = _sandbox(tmp_path)
    runbook = root / RUNBOOK
    text = runbook.read_text(encoding="utf-8")
    # A paragraph mentioning both channels and GCP_SERVICE_ACCOUNT (a `vars`
    # name) immediately before the Secrets table.
    text = text.replace(
        "**Secrets** —",
        "Note: unlike the secrets below, `GCP_SERVICE_ACCOUNT` and\n"
        "`GCP_WIF_PROVIDER` are variables on the GCP side.\n\n**Secrets** —",
        1,
    )
    runbook.write_text(text, encoding="utf-8")

    code, out = _run(root)
    assert code == 0, (
        "prose that mentions a `vars` name near a Secrets heading was read as a "
        f"channel declaration. Only a table header declares the channel:\n{out}"
    )


def test_the_gate_refuses_to_pass_on_an_empty_scan(tmp_path: Path) -> None:
    """A gate that finds no workflows must fail, not report success."""
    root = _sandbox(tmp_path)
    for workflow in (root / DEPLOY_DIR).glob("deploy-*.yml"):
        workflow.unlink()

    code, out = _run(root)
    assert code == 1, f"the gate examined nothing and reported success:\n{out}"
    assert "no vars/secrets references found" in out


def test_the_scope_is_reported_for_the_ratchet() -> None:
    """The success line must carry a count, or the ratchet cannot see shrinkage."""
    proc = subprocess.run([sys.executable, str(GATE)], cwd=REPO_ROOT, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    match = re.search(r"OK — (\d+) name\(s\)", proc.stdout)
    assert match, f"no scope count in the success line:\n{proc.stdout}"
    assert int(match.group(1)) >= 15, f"only {match.group(1)} names checked"
