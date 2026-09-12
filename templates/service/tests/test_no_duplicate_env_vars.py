"""Contract — no rendered container may declare the same env var twice.

Why this exists
---------------
A Deployment with two entries of the same ``name`` in ``env`` keeps **one** of
them, and kubectl says so on stderr rather than refusing::

    Warning: spec.template.spec.containers[0].env[9]: hides previous definition
    of "ENVIRONMENT", which may be dropped when using apply

"may be dropped" is the part that matters: which value survives depends on the
apply path, so the pod's configuration is decided by something other than the
manifest. For ``ENVIRONMENT`` specifically that reaches the MLflow run tag and
the structured-log context, so a run can be labelled `dev` while the pod
believes it is `ci`, or the reverse, with nothing failing.

It happened here. ``golden-path-extended.yml``'s CI-only patch appended
``ENVIRONMENT=ci`` to the end of the env list while the ``gcp-dev`` overlay
already set ``ENVIRONMENT=dev`` at index 3. Two consequences, and the second is
the reason this file exists rather than just a one-line workflow fix:

1. the rendered Deployment carried the name twice;
2. the warning line survived that step's error-tolerance grep — which only
   excused two known-absent CRDs — so **the L3 closed-loop lane had never once
   been green**, while every apply in it succeeded and every resource was
   created. A lane that fails on a warning teaches people to stop reading it.

An adopter writing their own overlay patch can reproduce exactly this, and a
JSON-6902 ``add`` to ``env/-`` is the natural way to do it wrong: it appends
unconditionally, whether or not the name already exists.

What this checks
----------------
Every overlay renders, and in every rendered pod spec — containers and init
containers — no container declares one env name twice. Rendering is done with
``kubectl kustomize`` so patches, generators and merges are all applied; a check
against the source YAML would miss the case entirely, because each file is
individually correct and the duplicate only exists after the merge.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

SERVICE_ROOT = Path(__file__).resolve().parents[1]
OVERLAYS = SERVICE_ROOT / "k8s" / "overlays"

_TOKENS = {
    "{@ service_name @}": "RenderProbe",
    "{@ service_slug @}": "render_probe",
    "{@ service_kebab @}": "render-probe",
    "{@ gh_org @}": "org",
    "{@ gh_repo @}": "repo",
}


def _overlay_names() -> list[str]:
    if not OVERLAYS.is_dir():
        return []
    return sorted(d.name for d in OVERLAYS.iterdir() if d.is_dir() and (d / "kustomization.yaml").is_file())


NAMES = _overlay_names()
_KUSTOMIZE = shutil.which("kubectl") or shutil.which("kustomize")


def _render(tmp_path: Path, overlay: str) -> str:
    """`kubectl kustomize` over a token-substituted copy of k8s/.

    Substituted rather than rendered through Copier: this test is about the
    merge result, and the tokens are opaque strings to kustomize. Keeping it
    self-contained means it runs in the same lane as the rest of the suite.
    """
    root = tmp_path / "k8s"
    shutil.copytree(SERVICE_ROOT / "k8s", root)
    for path in root.rglob("*.yaml"):
        text = path.read_text(encoding="utf-8")
        for token, value in _TOKENS.items():
            text = text.replace(token, value)
        path.write_text(text, encoding="utf-8")

    binary = Path(_KUSTOMIZE or "kubectl")
    argv = [str(binary), "kustomize", str(root / "overlays" / overlay)]
    if binary.name == "kustomize":
        argv = [str(binary), "build", str(root / "overlays" / overlay)]
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=180)
    assert proc.returncode == 0, f"overlay '{overlay}' does not render:\n{proc.stderr[-1500:]}"
    return proc.stdout


def test_overlays_were_discovered() -> None:
    """An empty parametrize set reports as a pass; make that a failure."""
    assert NAMES, f"no overlays with a kustomization.yaml found under {OVERLAYS}"
    assert len(NAMES) >= 6, f"only {len(NAMES)} overlay(s) discovered: {NAMES}"


@pytest.mark.skipif(_KUSTOMIZE is None, reason="needs kubectl or kustomize to render")
@pytest.mark.parametrize("overlay", NAMES, ids=NAMES or ["none"])
def test_no_container_declares_an_env_var_twice(tmp_path: Path, overlay: str) -> None:
    rendered = _render(tmp_path, overlay)

    checked = 0
    for doc in yaml.safe_load_all(rendered):
        if not isinstance(doc, dict):
            continue
        spec = doc.get("spec") or {}
        # Deployment/CronJob/Job all nest a pod template somewhere.
        pod_specs = []
        template = (spec.get("template") or {}).get("spec")
        if template:
            pod_specs.append(template)
        job = ((spec.get("jobTemplate") or {}).get("spec") or {}).get("template") or {}
        if job.get("spec"):
            pod_specs.append(job["spec"])

        for pod in pod_specs:
            for key in ("containers", "initContainers"):
                for container in pod.get(key) or []:
                    names = [e["name"] for e in container.get("env") or [] if isinstance(e, dict) and "name" in e]
                    checked += 1
                    duplicates = sorted({n for n in names if names.count(n) > 1})
                    assert not duplicates, (
                        f"overlay '{overlay}': {doc.get('kind')}/{(doc.get('metadata') or {}).get('name')} "
                        f"{key[:-1]} '{container.get('name')}' declares {duplicates} more than once.\n"
                        f"  Kubernetes keeps ONE of them and only warns — 'may be dropped when using "
                        f"apply' — so which value the pod gets is decided by the apply path rather than "
                        f"by the manifest.\n"
                        f"  A JSON-6902 `add` to `/…/env/-` appends whether or not the name already "
                        f"exists; to change an existing value, patch it where it is declared."
                    )

    assert checked, f"overlay '{overlay}' rendered no containers — this assertion would be vacuous"
