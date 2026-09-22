"""Contract test — NetworkPolicy egress hygiene (R5-M3, hardened by May 2026 MED-11).

Authority: ACTION_PLAN_R5 §R5-M3; May 2026 audit MED-11.

Since MED-11 the base NetworkPolicy at
``templates/service/k8s/base/networkpolicy.yaml`` ships ZERO public egress
(default-deny). Defense-in-depth must fail closed: when an overlay
forgets its patch, the init container cannot reach cloud storage and
the rollout fails visibly instead of running with a wildcard egress.

EVERY overlay therefore MUST apply a JSON 6902 patch at
``patch-networkpolicy.yaml``:

- ``gcp-dev`` / ``aws-dev`` apply a deliberately permissive rule so
  ``kustomize build overlays/<cloud>-dev`` works out of the box;
- ``gcp-staging``/``gcp-prod``/``aws-staging``/``aws-prod`` apply
  cloud-specific allowlists and MUST NOT contain ``0.0.0.0/0``.

This contract test enforces:

1. The base NetworkPolicy carries the ``OVERLAY-OVERRIDE REQUIRED``
   banner (with MED-11 provenance) so the intent is visible to any
   future editor.
2. Every overlay controls egress — the cloud x env ones by shipping
   ``patch-networkpolicy.yaml`` and wiring it into ``kustomization.yaml``
   with ``target.kind: NetworkPolicy``; ``batch-only`` by shipping its own
   NetworkPolicy, because the base's podSelector does not match the batch
   pod's label and so leaves it uncovered.
3. No active patch or policy body contains ``0.0.0.0/0`` outside dev.

The overlay list is DISCOVERED. It used to be a literal six, which was the
right count for the patch-based overlays and the wrong count for the tree:
``batch-only`` was referenced by no test in this repository at all, while
shipping a real egress control.

The test parses YAML structurally; it does not require kustomize in
the test environment. An additional optional check invokes
``kustomize build`` if available and fails when the rendered non-dev
output still contains ``0.0.0.0/0`` — that catches patch-wiring
mistakes that the structural checks would miss (e.g., wrong
``target`` kind).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

try:  # optional dep — CI always has it, local dev environments may not.
    import yaml  # type: ignore
except ModuleNotFoundError:  # pragma: no cover
    yaml = None  # type: ignore

REPO_ROOT = Path(__file__).resolve().parents[3]
BASE_NETPOL = REPO_ROOT / "templates" / "service" / "k8s" / "base" / "networkpolicy.yaml"
OVERLAY_ROOT = REPO_ROOT / "templates" / "service" / "k8s" / "overlays"


# Discovered, never listed. The literal six here matched the six cloud x env
# overlays, and `batch-only` — a seventh, shipping its OWN NetworkPolicy with
# real egress rules — was mentioned by no test in the repository at all. The
# count was right for the patch-based overlays and wrong for the tree, which
# is how a whole overlay stayed invisible.
def _discover_overlays() -> list[str]:
    if not OVERLAY_ROOT.is_dir():
        return []
    return sorted(d.name for d in OVERLAY_ROOT.iterdir() if d.is_dir())


ALL_OVERLAYS = _discover_overlays()

# An overlay controls egress one of two legitimate ways: by patching the
# default-deny base, or by shipping a policy of its own for a pod the base's
# podSelector does not match (the batch CronJob). Which one an overlay uses is
# read from the tree rather than assumed.
PATCH_OVERLAYS = [o for o in ALL_OVERLAYS if (OVERLAY_ROOT / o / "patch-networkpolicy.yaml").exists()]
OWN_POLICY_OVERLAYS = [o for o in ALL_OVERLAYS if o not in PATCH_OVERLAYS]

# "dev" tiers may use a permissive rule; everything else must be specific.
DEV_OVERLAYS = [o for o in ALL_OVERLAYS if o.endswith("-dev")]
NON_DEV_OVERLAYS = [o for o in PATCH_OVERLAYS if o not in DEV_OVERLAYS]


# ---------------------------------------------------------------------------
# 1. Base policy carries the override-required banner.
# ---------------------------------------------------------------------------


def test_base_networkpolicy_carries_override_banner() -> None:
    """The base NetworkPolicy MUST flag that every overlay patches egress.

    Without this banner a future editor could silently remove the
    override instruction from the most visible surface (the base
    manifest), and the contract's intent would only live in a test.
    """
    text = BASE_NETPOL.read_text(encoding="utf-8")
    assert "OVERLAY-OVERRIDE REQUIRED" in text, (
        f"{BASE_NETPOL.relative_to(REPO_ROOT)} must carry the "
        "`OVERLAY-OVERRIDE REQUIRED` banner on the cloud-storage egress "
        "rule (R5-M3 / MED-11). Contributors need to see this in the "
        "base file, not just in a test."
    )
    # Provenance tags so a future audit can grep the lineage.
    assert "R5-M3" in text, "Base NetworkPolicy should cite R5-M3 as provenance"
    assert "MED-11" in text, "Base NetworkPolicy should cite MED-11 (default-deny hardening)"
    assert "every overlay" in text.lower(), "Banner should state that EVERY overlay patches egress"


# ---------------------------------------------------------------------------
# 2. Each overlay has a patch file wired into kustomization.yaml; non-dev
#    patch bodies must not contain 0.0.0.0/0.
# ---------------------------------------------------------------------------


def test_overlays_were_discovered() -> None:
    """Finding no overlays must fail, not silently parametrize to nothing.

    An empty parametrize set is reported as a pass. Every other assertion in
    this module is parametrized over the discovered list, so without this the
    whole file could go green having checked nothing.
    """
    assert ALL_OVERLAYS, f"no overlays found under {OVERLAY_ROOT}"
    assert PATCH_OVERLAYS, "no overlay patches the base NetworkPolicy — that cannot be right"


@pytest.mark.parametrize("overlay", ALL_OVERLAYS, ids=ALL_OVERLAYS or ["none"])
def test_every_overlay_controls_egress(overlay: str) -> None:
    """Every overlay restricts egress, by patch or by its own policy.

    `batch-only` is the case this exists for: the base NetworkPolicy selects
    `app: <service>` and the batch CronJob pod carries `app: <service>-batch`,
    so the base does not select it at all. It ships `networkpolicy-batch.yaml`
    instead — a real control that no test referenced before this one.
    """
    overlay_dir = OVERLAY_ROOT / overlay
    kustomization = overlay_dir / "kustomization.yaml"
    assert kustomization.is_file(), f"overlay `{overlay}` has no kustomization.yaml"
    wiring = kustomization.read_text(encoding="utf-8")

    if overlay in PATCH_OVERLAYS:
        assert "patch-networkpolicy.yaml" in wiring, (
            f"overlay `{overlay}` ships patch-networkpolicy.yaml but never wires it "
            f"into kustomization.yaml — an unreferenced patch is not applied"
        )
        return

    own = sorted(overlay_dir.glob("networkpolicy*.yaml"))
    assert own, (
        f"overlay `{overlay}` neither patches the base NetworkPolicy nor ships one "
        f"of its own. Since MED-11 the base is default-deny for the pods it selects "
        f"and selects nothing else, so this overlay's pods have no egress policy at all."
    )
    assert any(policy.name in wiring for policy in own), (
        f"overlay `{overlay}` ships {[p.name for p in own]} but wires none of them into kustomization.yaml"
    )


@pytest.mark.parametrize("overlay", OWN_POLICY_OVERLAYS, ids=OWN_POLICY_OVERLAYS or ["none"])
def test_own_policy_does_not_contain_wildcard(overlay: str) -> None:
    """An overlay's own NetworkPolicy must not open egress to the world."""
    for policy in sorted((OVERLAY_ROOT / overlay).glob("networkpolicy*.yaml")):
        body = policy.read_text(encoding="utf-8")
        active = "\n".join(line for line in body.splitlines() if not line.lstrip().startswith("#"))
        assert "0.0.0.0/0" not in active, (
            f"{policy.relative_to(REPO_ROOT)} opens egress to 0.0.0.0/0 in its active YAML"
        )


@pytest.mark.parametrize("overlay", PATCH_OVERLAYS, ids=PATCH_OVERLAYS or ["none"])
def test_overlay_has_patch_file(overlay: str) -> None:
    """Each overlay ships `patch-networkpolicy.yaml` (MED-11: the base is
    default-deny, so an overlay without the patch cannot fetch models)."""
    patch = OVERLAY_ROOT / overlay / "patch-networkpolicy.yaml"
    assert patch.exists(), (
        f"Overlay `{overlay}` must carry patch-networkpolicy.yaml; since "
        "MED-11 the base ships zero public egress, so without the patch "
        "the init container cannot download the model. dev applies a "
        "permissive rule; staging/prod apply cloud-specific allowlists."
    )


@pytest.mark.parametrize("overlay", NON_DEV_OVERLAYS, ids=NON_DEV_OVERLAYS or ["none"])
def test_patch_file_does_not_contain_wildcard(overlay: str) -> None:
    """The patch body MUST NOT restore the wildcard egress CIDR."""
    patch = OVERLAY_ROOT / overlay / "patch-networkpolicy.yaml"
    if not patch.exists():
        pytest.skip("patch file absent — covered by the existence test")
    body = patch.read_text(encoding="utf-8")
    # Allow the string inside a comment line (e.g. "narrow from 0.0.0.0/0").
    # Grep each non-comment line independently.
    non_comment = "\n".join(line for line in body.splitlines() if not line.lstrip().startswith("#"))
    assert "0.0.0.0/0" not in non_comment, (
        f"{patch.relative_to(REPO_ROOT)} reintroduces the wildcard CIDR "
        "`0.0.0.0/0` in its active YAML. R5-M3 requires cloud-specific "
        "allowlists for non-dev overlays."
    )


# Patch-based overlays only: an overlay that ships its own policy has nothing
# to wire here, and `test_every_overlay_controls_egress` covers that path.
@pytest.mark.parametrize("overlay", PATCH_OVERLAYS, ids=PATCH_OVERLAYS or ["none"])
def test_kustomization_wires_the_patch(overlay: str) -> None:
    """``kustomization.yaml`` MUST reference ``patch-networkpolicy.yaml``
    with an explicit target ``kind: NetworkPolicy``. Without the target
    the JSON 6902 patch fails to apply silently.
    """
    kust = OVERLAY_ROOT / overlay / "kustomization.yaml"
    assert kust.exists(), f"Overlay `{overlay}` missing kustomization.yaml"
    body = kust.read_text(encoding="utf-8")
    assert "patch-networkpolicy.yaml" in body, (
        f"{kust.relative_to(REPO_ROOT)} does not reference "
        "patch-networkpolicy.yaml; the patch will not be applied by "
        "`kustomize build` even if the file exists."
    )
    assert "kind: NetworkPolicy" in body, (
        f"{kust.relative_to(REPO_ROOT)} references the patch but does not "
        "set `target.kind: NetworkPolicy`; JSON 6902 patches need an "
        "explicit target to find the resource."
    )


# ---------------------------------------------------------------------------
# 3. (Optional) end-to-end kustomize build render check.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("overlay", NON_DEV_OVERLAYS, ids=NON_DEV_OVERLAYS)
def test_kustomize_render_has_no_wildcard_egress(overlay: str) -> None:
    """If ``kustomize`` is available, render the overlay and assert the
    final YAML has no ``0.0.0.0/0`` egress. Skipped when kustomize is
    not installed (CI installs it via `actions/setup-kustomize`).
    """
    kustomize_bin = shutil.which("kustomize")
    if not kustomize_bin:
        pytest.skip("kustomize binary not in PATH; CI covers this path")
    overlay_dir = OVERLAY_ROOT / overlay
    result = subprocess.run(  # noqa: S603 — kustomize is a well-known bin
        [kustomize_bin, "build", str(overlay_dir)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"`kustomize build` failed for overlay `{overlay}`: {result.stderr}"
    assert "0.0.0.0/0" not in result.stdout, (
        f"kustomize build of `{overlay}` still contains 0.0.0.0/0 in the "
        "rendered manifest; the JSON 6902 patch did not take effect. "
        "Check the `target:` block in kustomization.yaml (R5-M3)."
    )


# ---------------------------------------------------------------------------
# 4. Dev overlays MUST carry the permissive patch (MED-11): the base is
#    default-deny, so without it `kustomize build overlays/<cloud>-dev`
#    renders a NetworkPolicy under which the init container cannot
#    download the model.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("overlay", DEV_OVERLAYS)
def test_dev_overlays_carry_the_permissive_patch(overlay: str) -> None:
    """Dev overlays ship a deliberately permissive egress patch.

    The wildcard CIDR is ALLOWED here — dev is the documented
    exception (see the base manifest banner). What we assert is that
    the patch exists and actually opens egress, so the local golden
    path keeps working against the default-deny base.
    """
    patch = OVERLAY_ROOT / overlay / "patch-networkpolicy.yaml"
    assert patch.exists(), (
        f"Dev overlay `{overlay}` is missing patch-networkpolicy.yaml; "
        "since MED-11 the base ships zero public egress, so dev MUST "
        "opt in to a permissive rule or the scaffolded service cannot "
        "fetch its model locally."
    )
    body = patch.read_text(encoding="utf-8")
    non_comment = "\n".join(line for line in body.splitlines() if not line.lstrip().startswith("#"))
    assert "cidr" in non_comment.lower(), (
        f"{patch.relative_to(REPO_ROOT)} does not declare any egress "
        "CIDR; the dev patch must open egress for the init-container "
        "model download."
    )


# ---------------------------------------------------------------------------
# The general control: a workload nobody's policy selects has no egress at all.
#
# The issue that prompted this named the drift CronJob. Asking the question for
# every workload instead found three: `-drift`, `-perf` and `-gt`. The base
# policy selects `app: <service>` — the predictor — and default-deny covers the
# rest of the namespace, so each CronJob was launched into a namespace where it
# could not resolve DNS, reach its bucket or push a metric. Every lane here was
# green because kind enforces no NetworkPolicy.
# ---------------------------------------------------------------------------
WORKLOAD_KINDS = {"Deployment", "CronJob", "Rollout", "StatefulSet", "DaemonSet", "Job"}


def _pod_labels(document: dict) -> dict[str, str] | None:
    spec = document.get("spec") or {}
    template = spec.get("template")
    if template is None:
        job = (spec.get("jobTemplate") or {}).get("spec") or {}
        template = job.get("template")
    if template is None:
        return None
    labels = ((template.get("metadata") or {}).get("labels")) or {}
    # A patch fragment carries a name and the fields it overrides, never the pod
    # labels — treating one as a workload asks which policy selects a document
    # that never becomes a pod, and answers "none" every time.
    return labels or None


def _selects(selector: dict | None, labels: dict[str, str]) -> bool:
    if selector is None:
        return False
    if selector == {}:
        return True
    for key, value in (selector.get("matchLabels") or {}).items():
        if labels.get(key) != value:
            return False
    for expression in selector.get("matchExpressions") or []:
        operator, key = expression["operator"], expression["key"]
        values = expression.get("values", [])
        if operator == "In" and labels.get(key) not in values:
            return False
        if operator == "NotIn" and labels.get(key) in values:
            return False
        if operator == "Exists" and key not in labels:
            return False
        if operator == "DoesNotExist" and key in labels:
            return False
    return True


def _documents(directory: Path) -> list[dict]:
    out: list[dict] = []
    for path in sorted(directory.glob("*.yaml")):
        for document in yaml.safe_load_all(path.read_text(encoding="utf-8")):
            if isinstance(document, dict) and document.get("kind"):
                out.append(document)
    return out


def _egress_policies(documents: list[dict]) -> list[dict]:
    return [d for d in documents if d["kind"] == "NetworkPolicy" and (d["spec"].get("egress") or [])]


def test_every_base_workload_is_selected_by_an_egress_policy() -> None:
    documents = _documents(BASE_NETPOL.parent)
    policies = _egress_policies(documents)
    assert policies, "base ships no NetworkPolicy that grants egress at all"

    uncovered = []
    for document in documents:
        if document["kind"] not in WORKLOAD_KINDS:
            continue
        labels = _pod_labels(document)
        if labels is None:
            continue
        if not any(_selects(p["spec"].get("podSelector"), labels) for p in policies):
            uncovered.append(f"{document['kind']} {document['metadata']['name']} (labels: {labels})")

    assert not uncovered, (
        "these workloads are selected by no egress policy, so default-deny leaves them without "
        "DNS, bucket access or a way to push a metric — and it fails at the init container, "
        "daily, one layer below where anyone looks:\n  " + "\n  ".join(uncovered)
    )


@pytest.mark.parametrize("overlay", _discover_overlays())
def test_every_overlay_workload_is_selected_by_an_egress_policy(overlay: str) -> None:
    """An overlay that adds a workload must add or extend a policy that covers it."""
    documents = _documents(BASE_NETPOL.parent) + _documents(OVERLAY_ROOT / overlay)
    policies = _egress_policies(documents)
    uncovered = []
    for document in documents:
        if document["kind"] not in WORKLOAD_KINDS:
            continue
        labels = _pod_labels(document)
        if labels is None:
            continue
        if not any(_selects(p["spec"].get("podSelector"), labels) for p in policies):
            uncovered.append(f"{document['kind']} {document['metadata']['name']}")
    assert not uncovered, f"{overlay}: no egress policy selects " + ", ".join(uncovered)


@pytest.mark.parametrize("overlay", [o for o in _discover_overlays() if "-" in o and not o.startswith("batch")])
def test_overlay_patches_the_jobs_policy_too(overlay: str) -> None:
    """Two policies need cloud egress, so an overlay that patches one and forgets the other fails closed."""
    directory = OVERLAY_ROOT / overlay
    patch = directory / "patch-networkpolicy-jobs.yaml"
    assert patch.is_file(), (
        f"{overlay} ships patch-networkpolicy.yaml but no patch-networkpolicy-jobs.yaml: the CronJobs "
        "would have DNS and the Pushgateway, and no route to the bucket they download their window from"
    )
    kustomization = yaml.safe_load((directory / "kustomization.yaml").read_text(encoding="utf-8"))
    targets = [
        p.get("target", {}).get("name", "")
        for p in kustomization.get("patches", [])
        if p.get("path") == "patch-networkpolicy-jobs.yaml"
    ]
    assert any(name.endswith("-jobs-network-policy") for name in targets), (
        f"{overlay}: patch-networkpolicy-jobs.yaml is not wired to the jobs policy (targets: {targets})"
    )
    if overlay.endswith("-dev"):
        return
    # Structural, not textual: gcp-staging's rationale mentions 0.0.0.0/0 to say
    # its rule is tighter than the base used to be. A substring check reads that
    # sentence as a wildcard rule and sends the reader to edit a comment.
    operations = yaml.safe_load(patch.read_text(encoding="utf-8")) or []
    cidrs = [
        peer["ipBlock"]["cidr"]
        for op in operations
        for peer in (op.get("value", {}).get("to") or [])
        if "ipBlock" in peer
    ]
    assert "0.0.0.0/0" not in cidrs, f"{overlay}: wildcard egress outside dev ({cidrs})"
