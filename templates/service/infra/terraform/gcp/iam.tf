# ============================================================================
# Per-environment IAM split (ADR-017 / PR-A1)
# ============================================================================
# Five identities, each with the minimal permissions for its purpose:
#
#   ci       — Terraform plan/apply, image build push (used by GitHub Actions)
#   deploy   — Push images, update K8s manifests (used by deploy workflows)
#   runtime  — Pod runtime: read secrets, read storage (Workload Identity)
#   drift    — Drift CronJob: read metrics, write reports (Workload Identity)
#   retrain  — Retrain workflow: read data, write models (Workload Identity)
#
# Why 5 separate identities instead of one:
#   * Audit trail: a Cloud Audit Logs entry tells you EXACTLY which workflow
#     touched a resource. With a single identity you only know "the template
#     did it".
#   * Blast radius: a leaked CI key cannot read production model artifacts;
#     a leaked runtime key cannot push images.
#   * Least-privilege contract test: enforcing "no identity has *.admin"
#     is meaningful only when permissions are split by purpose.
#
# All identities are project-scoped (no org-level grants). Per-environment
# isolation is achieved by deploying this Terraform once per env, which
# creates env-suffixed names (e.g. ci-sa-staging, ci-sa-production).
#
# Parity note — why no iam-roles-split.tf (unlike AWS):
#   AWS splits `iam.tf` (per-SERVICE IRSA roles via for_each) from
#   `iam-roles-split.tf` (per-PURPOSE ci/deploy/drift/retrain roles) to
#   keep the for_each contract clean. GCP does not have per-service
#   identities — all services share the single `runtime` SA and gain
#   access via per-secret / per-bucket IAM bindings in secrets.tf and
#   logging.tf. So per-purpose and per-service concerns don't collide
#   here, and splitting the file would only add directory noise.
#   Cloud parity is at the semantic level (same 5 identities, same
#   permissions contract), not the filename level. See
#   test_iam_least_privilege.py for enforcement.
# ============================================================================

# ---------------------------------------------------------------------------
# 1. CI identity — runs Terraform + image build in GitHub Actions
# ---------------------------------------------------------------------------
resource "google_service_account" "ci" {
  account_id   = "${var.project_name}-ci-${var.environment}"
  display_name = "${var.project_name} CI (${var.environment})"
  description  = "Terraform plan/apply + image build. Used by GitHub Actions via WIF."
}

resource "google_project_iam_member" "ci_container_admin" {
  project = var.project_id
  role    = "roles/container.admin"
  member  = "serviceAccount:${google_service_account.ci.email}"
}

resource "google_project_iam_member" "ci_storage_admin" {
  project = var.project_id
  role    = "roles/storage.admin"
  member  = "serviceAccount:${google_service_account.ci.email}"
}

resource "google_artifact_registry_repository_iam_member" "ci_artifact_registry" {
  project    = var.project_id
  location   = google_artifact_registry_repository.ml_images.location
  repository = google_artifact_registry_repository.ml_images.repository_id
  role       = "roles/artifactregistry.writer"
  member     = "serviceAccount:${google_service_account.ci.email}"
}

# `roles/iam.serviceAccountUser`, granted ON THE SERVICE ACCOUNTS rather than
# on the project.
#
# This was a `google_project_iam_member` whose comment read "Scoped via
# condition (only acting on SAs in this project)". There was no condition
# block: the grant was project-wide and unconditional, so CI could impersonate
# **any** service account in the project — including `runtime`, `drift` and
# `retrain`, which is precisely the blast radius D-31 exists to prevent.
# Trivy flagged it as GCP-0011 and its resolution says the same thing:
# "Provide access at the service-level instead of project-level".
#
# One binding per service account CI legitimately needs to act as: `deploy`
# and `runtime` (impersonated during apply, the stated original intent) and
# `nodes` (attaching a service account to a node pool requires
# serviceAccountUser on it). `drift` and `retrain` are reached by workloads
# through Workload Identity, not by CI, so they are deliberately absent.
resource "google_service_account_iam_member" "ci_acts_as_deploy" {
  service_account_id = google_service_account.deploy.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.ci.email}"
}

resource "google_service_account_iam_member" "ci_acts_as_runtime" {
  service_account_id = google_service_account.runtime.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.ci.email}"
}

resource "google_service_account_iam_member" "ci_acts_as_nodes" {
  service_account_id = google_service_account.nodes.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.ci.email}"
}

# ---------------------------------------------------------------------------
# 2. Deploy identity — push images, update K8s
# ---------------------------------------------------------------------------
resource "google_service_account" "deploy" {
  account_id   = "${var.project_name}-deploy-${var.environment}"
  display_name = "${var.project_name} Deploy (${var.environment})"
  description  = "Push images + apply K8s manifests. Used by deploy-gcp.yml."
}

resource "google_artifact_registry_repository_iam_member" "deploy_artifact_registry" {
  project    = var.project_id
  location   = google_artifact_registry_repository.ml_images.location
  repository = google_artifact_registry_repository.ml_images.repository_id
  role       = "roles/artifactregistry.writer"
  member     = "serviceAccount:${google_service_account.deploy.email}"
}

resource "google_project_iam_member" "deploy_container_developer" {
  # Developer (NOT admin) — can deploy workloads but cannot create/delete clusters.
  project = var.project_id
  role    = "roles/container.developer"
  member  = "serviceAccount:${google_service_account.deploy.email}"
}

# ---------------------------------------------------------------------------
# 3. Runtime identity — pod runtime via Workload Identity
# ---------------------------------------------------------------------------
resource "google_service_account" "runtime" {
  account_id   = "${var.project_name}-runtime-${var.environment}"
  display_name = "${var.project_name} Runtime (${var.environment})"
  description  = "Pod runtime: read secrets + read storage. Bound to KSA via Workload Identity."
}

resource "google_storage_bucket_iam_member" "runtime_models_viewer" {
  bucket = google_storage_bucket.models.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.runtime.email}"
}

resource "google_storage_bucket_iam_member" "runtime_mlflow_viewer" {
  bucket = google_storage_bucket.mlflow_artifacts.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.runtime.email}"
}

locals {
  # Kubernetes coordinates of each service's pods, derived exactly as the
  # Kustomize overlays name them: namespace `<service>-<dev|staging|prod>`,
  # ServiceAccounts `<service>-sa` (runtime) and `<service>-drift-sa` /
  # `<service>-retrain-sa` (ADR-017). Terraform says `production` where the
  # overlays say `prod`. A binding to any other name is a binding to nothing:
  # the pod gets no cloud identity and fails at model download (ADR-051).
  k8s_env_suffix = var.environment == "production" ? "prod" : var.environment
}

# Workload Identity binding: each service's runtime KSA impersonates this GSA.
# The overlays annotate `<service>-sa` with this GSA's email (ADR-051).
resource "google_service_account_iam_member" "runtime_workload_identity" {
  for_each = toset(var.service_names)

  service_account_id = google_service_account.runtime.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "serviceAccount:${var.project_id}.svc.id.goog[${each.value}-${local.k8s_env_suffix}/${each.value}-sa]"
}

# ---------------------------------------------------------------------------
# 4. Drift identity — drift CronJob (read metrics, write reports)
# ---------------------------------------------------------------------------
resource "google_service_account" "drift" {
  account_id   = "${var.project_name}-drift-${var.environment}"
  display_name = "${var.project_name} Drift (${var.environment})"
  description  = "Drift CronJob: read metrics + write reports. Bound to KSA via Workload Identity."
}

resource "google_project_iam_member" "drift_monitoring_viewer" {
  project = var.project_id
  role    = "roles/monitoring.viewer"
  member  = "serviceAccount:${google_service_account.drift.email}"
}

resource "google_storage_bucket_iam_member" "drift_logs_creator" {
  # Drift writes reports to a dedicated bucket; can create new objects but
  # not overwrite (object versioning + retention enforce immutability).
  bucket = google_storage_bucket.logs.name
  role   = "roles/storage.objectCreator"
  member = "serviceAccount:${google_service_account.drift.email}"
}

resource "google_storage_bucket_iam_member" "drift_data_viewer" {
  # Read reference distributions for PSI calculation.
  bucket = google_storage_bucket.data.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.drift.email}"
}

resource "google_service_account_iam_member" "drift_workload_identity" {
  for_each = toset(var.service_names)

  service_account_id = google_service_account.drift.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "serviceAccount:${var.project_id}.svc.id.goog[${each.value}-${local.k8s_env_suffix}/${each.value}-drift-sa]"
}

# ---------------------------------------------------------------------------
# 5. Retrain identity — retrain workflow (read data, write models)
# ---------------------------------------------------------------------------
resource "google_service_account" "retrain" {
  account_id   = "${var.project_name}-retrain-${var.environment}"
  display_name = "${var.project_name} Retrain (${var.environment})"
  description  = "Retrain workflow: read data + write models. Bound to KSA via Workload Identity."
}

resource "google_storage_bucket_iam_member" "retrain_data_viewer" {
  bucket = google_storage_bucket.data.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.retrain.email}"
}

resource "google_storage_bucket_iam_member" "retrain_models_creator" {
  bucket = google_storage_bucket.models.name
  role   = "roles/storage.objectCreator"
  member = "serviceAccount:${google_service_account.retrain.email}"
}

# No manifest in the template runs as `<service>-retrain-sa` yet: retraining
# runs in GitHub Actions. The binding is kept on the same naming contract so an
# adopter-supplied in-cluster retrain Job needs a ServiceAccount, not Terraform.
resource "google_service_account_iam_member" "retrain_workload_identity" {
  for_each = toset(var.service_names)

  service_account_id = google_service_account.retrain.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "serviceAccount:${var.project_id}.svc.id.goog[${each.value}-${local.k8s_env_suffix}/${each.value}-retrain-sa]"
}

# ---------------------------------------------------------------------------
# Outputs — consumed by GitHub Actions secrets + K8s ServiceAccount annotations
# ---------------------------------------------------------------------------
output "ci_service_account_email" {
  description = "Email of the CI service account (for GitHub Actions WIF binding)"
  value       = google_service_account.ci.email
}

output "deploy_service_account_email" {
  description = "Email of the deploy service account"
  value       = google_service_account.deploy.email
}

output "runtime_service_account_email" {
  description = "Email of the runtime service account (annotate KSA with this)"
  value       = google_service_account.runtime.email
}

output "drift_service_account_email" {
  description = "Email of the drift service account (annotate drift KSA with this)"
  value       = google_service_account.drift.email
}

output "retrain_service_account_email" {
  description = "Email of the retrain service account (annotate retrain KSA with this)"
  value       = google_service_account.retrain.email
}

# ---------------------------------------------------------------------------
# 6. Node identity — the GKE nodes themselves (D-31 / ADR-017)
# ---------------------------------------------------------------------------
# Without this the node pools fall back to the DEFAULT Compute Engine service
# account, which in most projects carries `roles/editor` across the whole
# project. Trivy flags it as GCP-0050, and it is a D-31 violation in the
# module that defines D-31's other five identities.
#
# Workload Identity means pods do not use this identity — they impersonate
# `runtime`/`drift`/`retrain` — so the exposure is bounded. But the node
# identity still governs node-level operations (log and metric export, image
# pulls) and is what an attacker inherits from a compromised node, so it gets
# the documented GKE minimum and nothing more.
resource "google_service_account" "nodes" {
  account_id   = "${var.project_name}-nodes-${var.environment}"
  display_name = "${var.project_name} GKE nodes (${var.environment})"
  description  = "GKE node identity: log/metric export + image pull. No workload permissions — pods use Workload Identity."
}

# Google's documented minimum for a custom node service account.
resource "google_project_iam_member" "nodes_log_writer" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.nodes.email}"
}

resource "google_project_iam_member" "nodes_metric_writer" {
  project = var.project_id
  role    = "roles/monitoring.metricWriter"
  member  = "serviceAccount:${google_service_account.nodes.email}"
}

resource "google_project_iam_member" "nodes_monitoring_viewer" {
  project = var.project_id
  role    = "roles/monitoring.viewer"
  member  = "serviceAccount:${google_service_account.nodes.email}"
}

resource "google_project_iam_member" "nodes_resource_metadata_writer" {
  project = var.project_id
  role    = "roles/stackdriver.resourceMetadata.writer"
  member  = "serviceAccount:${google_service_account.nodes.email}"
}

# Image pull. Scoped to the repository rather than granted project-wide.
resource "google_artifact_registry_repository_iam_member" "nodes_artifact_reader" {
  location   = google_artifact_registry_repository.ml_images.location
  repository = google_artifact_registry_repository.ml_images.name
  role       = "roles/artifactregistry.reader"
  member     = "serviceAccount:${google_service_account.nodes.email}"
}
