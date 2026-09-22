"""ADR-051 — the names a pod asks for are the names Terraform creates.

Why this exists
---------------
Every staging and production deployment of this template was unable to
authenticate a single request, on both clouds, and the deploy went green.
Nothing was wrong in any one file. Four files each chose a name, and no two
chose the same one:

* Terraform bound Workload Identity to ``ml-services/<project>-sa`` and the
  IRSA trust policy to ``system:serviceaccount:ml-services:<service>``. The
  overlays run pods as ``<service>-sa`` in ``<service>-<env>``, annotated with
  a GSA (``<service>-sa@…``) and an IAM role (``<service>-irsa-role``) that
  Terraform never creates. A pod bound to nothing has no cloud identity.
* Terraform names the API key ``<project>-<service>-api_key`` (GCP) and
  ``<project>/<service>/api_key`` (AWS). The loader asked for
  ``<slug>-API_KEY`` and ``<slug>/API_KEY``.
* The loader read ``ENV``; every overlay sets ``ENVIRONMENT``.
* No image carried a cloud secret SDK.

The smoke test only called ``/ready``, which touches none of it.

What this checks
----------------
For every ``gcp-*`` and ``aws-*`` overlay, with the Terraform environment taken
from the overlay's own ``ENVIRONMENT`` value and every Terraform interpolation
evaluated rather than pattern-matched:

1. the Workload Identity member / IRSA trust ``sub`` names the overlay's
   namespace and the ServiceAccount the workload actually runs as — for the
   runtime AND the drift identity;
2. the ServiceAccount annotation names the GSA / IAM role Terraform creates;
3. ``common_utils.secrets.secret_id`` over the overlay's ``SECRETS_PREFIX``
   reproduces Terraform's secret name, and on AWS that secret is inside the
   runtime role's IAM resource scope;
4. the post-deploy smoke probe is admissible where it runs and exercises the
   auth path, and each cloud's image is built with its secret SDK.

Nothing here is a hand-kept copy of those names: a table in this file would
have agreed with whichever side it was copied from.
"""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from common_utils.secrets import secret_id

SERVICE = Path(__file__).resolve().parents[1]
K8S = SERVICE / "k8s"
TERRAFORM = SERVICE / "infra" / "terraform"
WORKFLOWS = SERVICE / ".github" / "workflows"
CLOUDS = ("gcp", "aws")

# Adopter-replaced overlay placeholders → the stand-ins Terraform variables get.
PLACEHOLDERS = {"{PROJECT_NAME}": "PROJECT_NAME", "{PROJECT_ID}": "PROJECT_ID", "{AWS_ACCOUNT_ID}": "ACCOUNT"}
TF_VARIABLES = {"var.project_name": "PROJECT_NAME", "var.project_id": "PROJECT_ID", "var.region": "REGION"}

# The key auth.verify_api_key resolves.
API_KEY = "API_KEY"


# ---------------------------------------------------------------------------
# Terraform reading
# ---------------------------------------------------------------------------
def _tf_text(cloud: str) -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted((TERRAFORM / cloud).glob("*.tf")))


def _resource(text: str, type_: str, name: str) -> str:
    match = re.search(rf'resource\s+"{type_}"\s+"{name}"\s*\{{', text)
    assert match, f'resource "{type_}" "{name}" not found'
    depth = 0
    for index in range(match.end() - 1, len(text)):
        depth += {"{": 1, "}": -1}.get(text[index], 0)
        if depth == 0:
            return text[match.start() : index + 1]
    raise AssertionError(f'unbalanced braces in resource "{type_}" "{name}"')


def _string_attribute(block: str, pattern: str) -> str:
    match = re.search(rf'{pattern}\s*=\s*"([^"]+)"', block)
    assert match, f"attribute matching {pattern!r} not found in:\n{block[:300]}"
    return match.group(1)


def _list_default(text: str, variable: str) -> list[str]:
    block = re.search(rf'variable\s+"{variable}"\s*\{{.*?default\s*=\s*\[([^\]]*)\]', text, re.S)
    assert block, f"variable {variable} has no list default"
    return re.findall(r'"([^"]+)"', block.group(1))


def _k8s_env_suffix(text: str, environment: str) -> str:
    match = re.search(
        r'k8s_env_suffix\s*=\s*var\.environment\s*==\s*"(\w+)"\s*\?\s*"(\w+)"\s*:\s*var\.environment', text
    )
    assert match, "local.k8s_env_suffix changed shape; teach this contract test the new expression"
    return match.group(2) if environment == match.group(1) else environment


def _evaluate(expression: str, text: str, *, environment: str, service: str, secret: str = "") -> str:
    values = {
        **TF_VARIABLES,
        "var.environment": environment,
        "each.value": service,
        "each.value.service": service,
        "each.value.secret": secret,
    }
    # Resolved only when referenced, so a binding written without the local
    # (e.g. a hardcoded namespace) fails on its value, not on this lookup.
    if "local.k8s_env_suffix" in expression:
        values["local.k8s_env_suffix"] = _k8s_env_suffix(text, environment)

    def _substitute(match: re.Match[str]) -> str:
        key = match.group(1).strip()
        assert key in values, f"unhandled Terraform interpolation ${{{key}}} in {expression!r}"
        return values[key]

    return re.sub(r"\$\{([^}]+)\}", _substitute, expression)


# ---------------------------------------------------------------------------
# Kubernetes reading
# ---------------------------------------------------------------------------
def _docs(path: Path) -> list[dict[str, Any]]:
    # Mappings only: overlay patch files also carry JSON 6902 patches, which are lists.
    return [doc for doc in yaml.safe_load_all(path.read_text(encoding="utf-8")) if isinstance(doc, dict)]


def _replace_placeholders(value: str) -> str:
    for placeholder, stand_in in PLACEHOLDERS.items():
        value = value.replace(placeholder, stand_in)
    return value


def _overlays(cloud: str) -> list[Path]:
    found = sorted(p for p in (K8S / "overlays").glob(f"{cloud}-*") if p.is_dir())
    assert found, f"no {cloud}-* overlays found — a contract over nothing passes"
    return found


class _Overlay:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.kustomization = _docs(path / "kustomization.yaml")[0]
        self.namespace: str = self.kustomization["namespace"]
        patches = [path / p["path"] for p in self.kustomization.get("patches", [])]
        deployment = next(d for p in patches for d in _docs(p) if d.get("kind") == "Deployment")
        container = deployment["spec"]["template"]["spec"]["containers"][0]
        self.env = {e["name"]: e.get("value", "") for e in container.get("env", [])}
        self.service_accounts = {
            d["metadata"]["name"]: d["metadata"].get("annotations") or {}
            for p in patches
            for d in _docs(p)
            if d.get("kind") == "ServiceAccount"
        }
        base_deployment = _docs(K8S / "base" / "deployment.yaml")[0]
        drift = next(d for d in _docs(K8S / "base" / "cronjob-drift.yaml") if d.get("kind") == "CronJob")
        self.runtime_sa: str = (
            deployment["spec"]["template"]["spec"].get("serviceAccountName")
            or base_deployment["spec"]["template"]["spec"]["serviceAccountName"]
        )
        self.drift_sa: str = drift["spec"]["jobTemplate"]["spec"]["template"]["spec"]["serviceAccountName"]

    @property
    def environment(self) -> str:
        value = self.env.get("ENVIRONMENT")
        assert value, f"{self.path.name}: patch-deployment.yaml sets no ENVIRONMENT"
        return value

    def annotation(self, service_account: str, key: str) -> str:
        annotations = self.service_accounts.get(service_account)
        assert annotations is not None, (
            f"{self.path.name}: the workload runs as {service_account!r} but no overlay patch "
            f"annotates a ServiceAccount of that name (annotated: {sorted(self.service_accounts)})"
        )
        assert key in annotations, f"{self.path.name}: ServiceAccount {service_account} lacks {key}"
        return _replace_placeholders(annotations[key])


def _service(cloud: str) -> str:
    services = _list_default(_tf_text(cloud), "service_names")
    app_label = _docs(K8S / "base" / "deployment.yaml")[0]["metadata"]["labels"]["app"]
    assert app_label in services, (
        f"{cloud}: Terraform service_names default {services} does not include this service ({app_label!r}); "
        "no identity or secret would be created for it"
    )
    return app_label


def _params(cloud: str) -> list[Any]:
    return [pytest.param(path, id=path.name) for path in _overlays(cloud)]


# ---------------------------------------------------------------------------
# 1 + 2. Workload identity
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("overlay_path", _params("gcp"))
@pytest.mark.parametrize("purpose", ["runtime", "drift"])
def test_gcp_workload_identity_binds_the_service_account_the_pod_runs_as(overlay_path: Path, purpose: str) -> None:
    overlay, text, service = _Overlay(overlay_path), _tf_text("gcp"), _service("gcp")
    ksa = overlay.runtime_sa if purpose == "runtime" else overlay.drift_sa
    evaluate = {"text": text, "environment": overlay.environment, "service": service}

    member = _evaluate(
        _string_attribute(
            _resource(text, "google_service_account_iam_member", f"{purpose}_workload_identity"), "member"
        ),
        **evaluate,
    )
    assert member == f"serviceAccount:PROJECT_ID.svc.id.goog[{overlay.namespace}/{ksa}]", (
        f"{overlay_path.name}: Workload Identity for {purpose} binds {member}, "
        f"but the workload runs as {overlay.namespace}/{ksa}"
    )

    account_id = _evaluate(
        _string_attribute(_resource(text, "google_service_account", purpose), "account_id"), **evaluate
    )
    annotation = overlay.annotation(ksa, "iam.gke.io/gcp-service-account")
    assert annotation == f"{account_id}@PROJECT_ID.iam.gserviceaccount.com", (
        f"{overlay_path.name}: {ksa} is annotated with {annotation}, Terraform creates {account_id}@…"
    )


@pytest.mark.parametrize("overlay_path", _params("aws"))
@pytest.mark.parametrize(("purpose", "role_resource"), [("runtime", "service"), ("drift", "drift")])
def test_aws_irsa_trusts_the_service_account_the_pod_runs_as(
    overlay_path: Path, purpose: str, role_resource: str
) -> None:
    overlay, text, service = _Overlay(overlay_path), _tf_text("aws"), _service("aws")
    ksa = overlay.runtime_sa if purpose == "runtime" else overlay.drift_sa
    evaluate = {"text": text, "environment": overlay.environment, "service": service}
    role = _resource(text, "aws_iam_role", role_resource)

    subject = _evaluate(_string_attribute(role, r':sub"'), **evaluate)
    assert subject == f"system:serviceaccount:{overlay.namespace}:{ksa}", (
        f"{overlay_path.name}: IRSA role for {purpose} trusts {subject}, "
        f"but the workload runs as {overlay.namespace}/{ksa}"
    )

    role_name = _evaluate(_string_attribute(role, r"(?m)^\s*name"), **evaluate)
    annotation = overlay.annotation(ksa, "eks.amazonaws.com/role-arn")
    assert annotation.split(":role/", 1)[-1] == role_name, (
        f"{overlay_path.name}: {ksa} assumes {annotation}, Terraform creates role {role_name}"
    )


# ---------------------------------------------------------------------------
# 3. Secret addressing
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("overlay_path", _params("gcp") + _params("aws"))
def test_secrets_prefix_reproduces_the_terraform_secret_name(overlay_path: Path) -> None:
    cloud = overlay_path.name.split("-", 1)[0]
    overlay, text, service = _Overlay(overlay_path), _tf_text(cloud), _service(cloud)
    assert overlay.env.get("CLOUD_PROVIDER") == cloud, f"{overlay_path.name}: CLOUD_PROVIDER must be {cloud!r}"
    prefix = overlay.env.get("SECRETS_PREFIX")
    assert prefix, f"{overlay_path.name}: SECRETS_PREFIX is not set, so API_KEY resolves to a bare 'api_key'"

    secret_name = API_KEY.lower()
    assert secret_name in _list_default(text, "secret_names"), f"{cloud}: Terraform creates no {secret_name} secret"
    resource, attribute = (
        ("google_secret_manager_secret", "secret_id")
        if cloud == "gcp"
        else ("aws_secretsmanager_secret", r"(?m)^\s*name")
    )
    terraform_name = _evaluate(
        _string_attribute(_resource(text, resource, "service"), attribute),
        text,
        environment=overlay.environment,
        service=service,
        secret=secret_name,
    )
    requested = secret_id(API_KEY, _replace_placeholders(prefix), cloud)
    assert requested == terraform_name, (
        f"{overlay_path.name}: the pod requests {requested!r}; Terraform creates {terraform_name!r}"
    )

    if cloud == "aws":
        policy = _resource(text, "aws_iam_policy", "service")
        scopes = [
            _evaluate(arn, text, environment=overlay.environment, service=service)
            for arn in re.findall(r'"(arn:aws:secretsmanager:[^"]+)"', policy)
        ]
        # Secrets Manager appends a 6-character suffix to every secret ARN.
        arn = f"arn:aws:secretsmanager:REGION:ACCOUNT:secret:{requested}-AbCdEf"
        assert any(fnmatch.fnmatchcase(arn, scope) for scope in scopes), (
            f"{overlay_path.name}: {requested} is outside the runtime role's secret scope {scopes}"
        )


# ---------------------------------------------------------------------------
# 4. Smoke probe and image build
# ---------------------------------------------------------------------------
def _smoke_step() -> str:
    text = (WORKFLOWS / "deploy-common.yml").read_text(encoding="utf-8")
    start = text.index("- name: Post-deploy smoke test")
    end = text.find("\n      - name:", start)
    return text[start : end if end != -1 else len(text)]


def test_smoke_probe_exercises_the_auth_path() -> None:
    step = _smoke_step()
    main = (SERVICE / "app" / "main.py").read_text(encoding="utf-8")
    probed = set(re.findall(r"http://\$\{FQDN\}(/[\w/]+)", step))
    auth_paths = sorted(probed - {"/ready"})
    assert "/ready" in probed and auth_paths, (
        f"smoke step probes only {sorted(probed)}; readiness never touches secrets"
    )
    auth_path = auth_paths[0]
    assert re.search(rf'@app\.\w+\("{re.escape(auth_path)}",\s*dependencies=\[Depends\(verify_api_key\)\]', main), (
        f"the smoke probe calls {auth_path}, which is not protected by verify_api_key — it proves nothing about auth"
    )
    assert "API_AUTH_ENABLED" in step and "401)" in step and "503)" in step, (
        "the smoke step must read API_AUTH_ENABLED from the live Deployment and fail on 503 (unresolvable secret)"
    )


def test_smoke_pod_is_admissible_and_reachable() -> None:
    step = _smoke_step()
    assert re.search(r'SMOKE_IMAGE:\s*"[^"@]+@sha256:[0-9a-f]{64}"', step), (
        "Kyverno require-image-digest rejects tag-only images in staging/production namespaces"
    )
    assert "--override-type=strategic" in step and "runAsNonRoot: true" in step and "RuntimeDefault" in step, (
        "prod namespaces enforce PSS restricted; the smoke pod needs a restricted securityContext"
    )
    label = re.search(r'--labels="([^"=]+)=([^"]+)"', step)
    assert label, "the smoke pod carries no label for NetworkPolicy to admit"
    selector = {label.group(1): label.group(2)}

    policies = _docs(K8S / "base" / "networkpolicy-smoke-test.yaml")
    resources = _docs(K8S / "base" / "kustomization.yaml")[0]["resources"]
    assert "networkpolicy-smoke-test.yaml" in resources, "the smoke NetworkPolicies are not in base resources"
    ingress_from = [
        peer.get("podSelector", {}).get("matchLabels")
        for policy in policies
        for rule in policy["spec"].get("ingress", [])
        for peer in rule.get("from", [])
    ]
    egress_owners = [
        p["spec"]["podSelector"].get("matchLabels") for p in policies if "Egress" in p["spec"]["policyTypes"]
    ]
    assert selector in ingress_from, f"no ingress rule admits pods labelled {selector} to the predictor"
    assert selector in egress_owners, f"no egress policy selects pods labelled {selector}; default-deny blocks DNS"


@pytest.mark.parametrize("cloud", CLOUDS)
def test_cloud_image_carries_its_secret_backend(cloud: str) -> None:
    dockerfile = (SERVICE / "Dockerfile").read_text(encoding="utf-8")
    workflow = (WORKFLOWS / f"deploy-{cloud}.yml").read_text(encoding="utf-8")
    assert "ARG CLOUD_PROVIDER" in dockerfile and "requirements-${CLOUD_PROVIDER}.txt" in dockerfile
    assert f"--build-arg CLOUD_PROVIDER={cloud}" in workflow, (
        f"deploy-{cloud}.yml builds an image without the {cloud} secret SDK; every authenticated request would 503"
    )
    requirements = (SERVICE / f"requirements-{cloud}.txt").read_text(encoding="utf-8")
    loader = (SERVICE / "common_utils" / "secrets.py").read_text(encoding="utf-8")
    # The import each backend performs, and the distribution that provides it.
    backend = {
        "aws": ("import boto3", "boto3"),
        "gcp": ("from google.cloud import secretmanager", "google-cloud-secret-manager"),
    }
    statement, distribution = backend[cloud]
    assert statement in loader, f"secrets.py no longer imports {statement!r}; update this mapping"
    assert re.search(rf"^{re.escape(distribution)}\s*~=", requirements, re.M), (
        f"requirements-{cloud}.txt does not pin {distribution}"
    )


# ---------------------------------------------------------------------------
# 5. GitHub OIDC subject for environment-scoped deploy jobs
# ---------------------------------------------------------------------------
def test_aws_github_oidc_trusts_the_environments_deploy_jobs_run_in() -> None:
    """A job with ``environment:`` presents that environment's NAME as its OIDC subject.

    ``deploy-common.yml`` names it ``<cloud>-<environment>``; Terraform trusted
    ``environment:<environment>``, which no job presents, so no deploy job could
    assume the CI or deploy role.
    """
    common = (WORKFLOWS / "deploy-common.yml").read_text(encoding="utf-8")
    assert re.search(
        r"^    environment:\s*\$\{\{\s*inputs\.cloud\s*\}\}-\$\{\{\s*inputs\.environment\s*\}\}\s*$", common, re.M
    ), "deploy-common.yml no longer names its job environment <cloud>-<environment>; update this contract"
    callers = (WORKFLOWS / "deploy-aws.yml").read_text(encoding="utf-8")
    clouds = set(re.findall(r"^      cloud:\s*(\w+)\s*$", callers, re.M))
    environments = sorted(set(re.findall(r"^      environment:\s*(\w+)\s*$", callers, re.M)))
    assert clouds == {"aws"} and environments, "deploy-aws.yml passes no cloud/environment inputs"

    text = _tf_text("aws")
    block = re.search(r"github_oidc_subs\s*=.*?\[(.*?)\]", text, re.S)
    assert block, "local.github_oidc_subs not found"
    subjects = re.findall(r'"([^"]+)"', block.group(1))
    for environment in environments:
        rendered = {
            s.replace("${var.github_repo}", "OWNER/REPO").replace("${var.environment}", environment) for s in subjects
        }
        expected = f"repo:OWNER/REPO:environment:aws-{environment}"
        assert expected in rendered, (
            f"no Terraform OIDC subject matches the {environment} deploy job ({expected}): {sorted(rendered)}"
        )


# ---------------------------------------------------------------------------
# 6. Every identity a workflow reads exists in Terraform, trusted for GitHub
#
# The deploy chain worked because somebody had run `gcp-wif-setup.md` by hand:
# Terraform created the five ADR-017 service accounts and bound three of them
# to Kubernetes ServiceAccounts, and nothing let GitHub Actions impersonate
# any of them. The three scheduled workflows had no identity at all — they read
# `AWS_ROLE_ARN`, the per-environment deploy role, from jobs with no
# `environment:`, where it arrives empty.
#
# The mapping below IS the contract: a workflow input on the left, the
# Terraform identity that backs it on the right. It is written out rather than
# derived because the derivation would have to guess which of five identities
# a name refers to, and a guess that is wrong reports the wrong thing.
# ---------------------------------------------------------------------------
AWS_IDENTITIES = {
    "AWS_ROLE_ARN": "deploy",
    "AWS_BUILD_ROLE_ARN": "ci",
    "AWS_CI_ROLE_ARN": "ci",
    "AWS_DRIFT_ROLE_ARN": "drift_ci",
    "AWS_RETRAIN_ROLE_ARN": "retrain_ci",
}
GCP_IDENTITIES = {
    "GCP_SERVICE_ACCOUNT": "deploy",
    "GCP_CI_SERVICE_ACCOUNT": "ci",
    "GCP_DRIFT_SERVICE_ACCOUNT": "drift",
    "GCP_RETRAIN_SERVICE_ACCOUNT": "retrain",
}


def _workflow_text() -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted(WORKFLOWS.glob("*.yml")))


def test_the_mapping_covers_every_identity_the_workflows_read() -> None:
    """A new identity input with no entry here would otherwise pass unchecked."""
    text = _workflow_text()
    read_aws = set(re.findall(r"secrets\.(AWS_[A-Z_]*ROLE_ARN)\b", text))
    read_gcp = set(re.findall(r"vars\.(GCP_[A-Z_]*SERVICE_ACCOUNT)\b", text))
    assert read_aws and read_gcp, "no identity inputs found; the patterns stopped matching"
    assert read_aws <= set(AWS_IDENTITIES), f"unmapped AWS identity inputs: {sorted(read_aws - set(AWS_IDENTITIES))}"
    assert read_gcp <= set(GCP_IDENTITIES), f"unmapped GCP identity inputs: {sorted(read_gcp - set(GCP_IDENTITIES))}"


@pytest.mark.parametrize(("value", "resource"), sorted(AWS_IDENTITIES.items()), ids=sorted(AWS_IDENTITIES))
def test_aws_identity_exists_and_trusts_github(value: str, resource: str) -> None:
    text = _tf_text("aws")
    block = _resource(text, "aws_iam_role", resource)
    assert "aws_iam_openid_connect_provider.github" in block, (
        f"{value} maps to aws_iam_role.{resource}, which does not trust the GitHub OIDC provider. "
        "A workflow cannot assume a role trusted only by the EKS cluster's provider: that is the IRSA "
        "path, for a pod, not for a runner."
    )
    assert "local.github_oidc_subs" in block, (
        f"aws_iam_role.{resource} trusts GitHub but pins no `sub` condition, so any workflow in any "
        "repository using this provider could assume it"
    )


@pytest.mark.parametrize(("value", "resource"), sorted(GCP_IDENTITIES.items()), ids=sorted(GCP_IDENTITIES))
def test_gcp_identity_exists_and_github_may_impersonate_it(value: str, resource: str) -> None:
    text = _tf_text("gcp")
    _resource(text, "google_service_account", resource)  # raises if the SA is missing
    binding = _resource(text, "google_service_account_iam_member", f"github_impersonates_{resource}")
    assert "roles/iam.workloadIdentityUser" in binding and "local.github_principal" in binding, (
        f"{value} maps to google_service_account.{resource}, which GitHub Actions cannot impersonate: "
        "the workloadIdentityUser binding for the repository's principalSet is missing"
    )


def test_the_gcp_pool_is_restricted_to_this_repository() -> None:
    """Without an attribute condition the pool accepts a token from any repository on github.com."""
    text = _tf_text("gcp")
    provider = _resource(text, "google_iam_workload_identity_pool_provider", "github")
    assert "attribute_condition" in provider and "assertion.repository" in provider, (
        "the WIF provider has no attribute condition pinning the repository"
    )
    assert "token.actions.githubusercontent.com" in provider, "the provider does not name GitHub's issuer"


def test_the_plan_identity_can_refresh_state() -> None:
    """`terraform plan` refreshes every managed resource; without read it reports no changes for what it cannot see."""
    assert "ReadOnlyAccess" in _resource(_tf_text("aws"), "aws_iam_role_policy_attachment", "ci_plan_read"), (
        "the AWS plan identity has no account-wide read, so the nightly plan silently under-reports drift"
    )
    assert "roles/viewer" in _resource(_tf_text("gcp"), "google_project_iam_member", "ci_plan_viewer"), (
        "the GCP plan identity has no project-wide read, with the same consequence"
    )
