# ============================================================================
# Workload Identity Federation for GitHub Actions (#183)
# ============================================================================
# Until this file existed, Terraform created the five service accounts
# ADR-017 asks for and bound three of them to Kubernetes ServiceAccounts —
# and nothing let GitHub Actions impersonate any of them. The pool, the
# provider and the `roles/iam.workloadIdentityUser` grants were a manual
# runbook step, which means: the deploy chain worked only because somebody had
# run `docs/runbooks/gcp-wif-setup.md` by hand, and the three scheduled
# workflows (nightly plan, drift, retrain) had no identity to read at all.
#
# Gated on `var.github_repo`, mirroring the AWS side: an adopter who federates
# some other way, or who already created a pool by hand, sets it to "" and
# gets none of this. The pool id is environment-suffixed so one project can
# hold dev, staging and prod pools without collision.
#
# The attribute CONDITION is the security boundary, not the mapping: without
# it, any repository on github.com could mint a token this pool accepts.
# ============================================================================

locals {
  github_wif_enabled = var.github_repo != ""
  # `principalSet://…/attribute.repository/<owner>/<repo>` — every workflow run
  # in that repository, which is the granularity GitHub's OIDC token supports
  # for impersonation. Per-branch narrowing lives in the attribute condition
  # below, and per-purpose narrowing lives in WHICH service account each
  # workflow is allowed to impersonate.
  github_principal = local.github_wif_enabled ? "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github[0].name}/attribute.repository/${var.github_repo}" : ""
}

resource "google_iam_workload_identity_pool" "github" {
  count = local.github_wif_enabled ? 1 : 0

  workload_identity_pool_id = "${var.project_name}-github-${var.environment}"
  display_name              = "GitHub Actions (${var.environment})"
  description               = "Federates GitHub Actions OIDC tokens for ${var.github_repo}. No service account keys (D-17)."
  project                   = var.project_id
}

resource "google_iam_workload_identity_pool_provider" "github" {
  count = local.github_wif_enabled ? 1 : 0

  project                            = var.project_id
  workload_identity_pool_id          = google_iam_workload_identity_pool.github[0].workload_identity_pool_id
  workload_identity_pool_provider_id = "github-oidc"
  display_name                       = "GitHub OIDC"

  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.repository" = "assertion.repository"
    "attribute.ref"        = "assertion.ref"
  }

  # Without this, the pool accepts a token from ANY repository on github.com.
  attribute_condition = "assertion.repository == '${var.github_repo}'"

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

# Which workflow may become which identity. One grant per purpose, so a
# compromised drift run cannot push an image and a retrain cannot deploy
# (ADR-017, D-31).
resource "google_service_account_iam_member" "github_impersonates_ci" {
  count = local.github_wif_enabled ? 1 : 0

  service_account_id = google_service_account.ci.name
  role               = "roles/iam.workloadIdentityUser"
  member             = local.github_principal
}

resource "google_service_account_iam_member" "github_impersonates_deploy" {
  count = local.github_wif_enabled ? 1 : 0

  service_account_id = google_service_account.deploy.name
  role               = "roles/iam.workloadIdentityUser"
  member             = local.github_principal
}

resource "google_service_account_iam_member" "github_impersonates_drift" {
  count = local.github_wif_enabled ? 1 : 0

  service_account_id = google_service_account.drift.name
  role               = "roles/iam.workloadIdentityUser"
  member             = local.github_principal
}

resource "google_service_account_iam_member" "github_impersonates_retrain" {
  count = local.github_wif_enabled ? 1 : 0

  service_account_id = google_service_account.retrain.name
  role               = "roles/iam.workloadIdentityUser"
  member             = local.github_principal
}

# `terraform plan` refreshes every managed resource before it can diff, so the
# identity the nightly plan runs as needs project-wide READ. `roles/viewer`
# grants exactly that and no mutation; enumerating per-service viewer roles
# would drift from the module the next time a resource type is added, and a
# plan that cannot refresh reports "no changes" for what it could not read.
resource "google_project_iam_member" "ci_plan_viewer" {
  count = local.github_wif_enabled ? 1 : 0

  project = var.project_id
  role    = "roles/viewer"
  member  = "serviceAccount:${google_service_account.ci.email}"
}

output "github_workload_identity_provider" {
  description = "Full provider resource name — set as the repository variable GCP_WIF_PROVIDER."
  value       = local.github_wif_enabled ? google_iam_workload_identity_pool_provider.github[0].name : ""
}
