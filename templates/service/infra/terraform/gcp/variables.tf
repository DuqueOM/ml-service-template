variable "project_id" {
  description = "GCP project ID"
  type        = string
}

variable "project_name" {
  description = "Project name used in resource naming"
  type        = string
}

variable "region" {
  description = "GCP region"
  type        = string
  default     = "us-central1"
}

variable "environment" {
  description = "Environment name (staging, production)"
  type        = string
  default     = "production"
  validation {
    # The overlays and the Workload Identity / IRSA bindings derive the
    # Kubernetes namespace from this value; anything else binds to nothing.
    condition     = contains(["dev", "staging", "production"], var.environment)
    error_message = "environment must be one of: dev, staging, production."
  }
}

variable "machine_type" {
  description = "GKE node pool machine type"
  type        = string
  default     = "e2-medium"
}

variable "initial_node_count" {
  description = "Initial number of nodes"
  type        = number
  default     = 2
}

variable "min_node_count" {
  description = "Minimum nodes in autoscaling"
  type        = number
  default     = 1
}

variable "max_node_count" {
  description = "Maximum nodes in autoscaling"
  type        = number
  default     = 5
}

variable "model_archive_days" {
  description = "Days before archiving model artifacts to Nearline"
  type        = number
  default     = 90
}

variable "model_delete_days" {
  description = "Days before deleting old model artifacts"
  type        = number
  default     = 365
}

variable "monthly_budget" {
  description = "Monthly budget in USD"
  type        = number
  default     = 500
}

# ----------------------------------------------------------------------
# Network mode (ADR-017 / PR-A1)
# ----------------------------------------------------------------------
# 'managed':  template creates VPC + subnetwork with secondary ranges
# 'existing': caller provides network_name + subnetwork_name (data lookup)
#
# Default 'managed' preserves backwards compatibility for adopters who
# accepted the implicit auto-mode VPC. Teams with existing VPCs flip to
# 'existing' and pass their network/subnetwork names.
# ----------------------------------------------------------------------
variable "network_mode" {
  description = "Network topology mode: 'managed' (template creates VPC) or 'existing' (use provided VPC/subnets)"
  type        = string
  default     = "managed"

  validation {
    condition     = contains(["managed", "existing"], var.network_mode)
    error_message = "network_mode must be 'managed' or 'existing'"
  }
}

variable "network_name" {
  description = "Existing VPC network name (required when network_mode='existing')"
  type        = string
  default     = ""
}

variable "subnetwork_name" {
  description = "Existing subnetwork name (required when network_mode='existing')"
  type        = string
  default     = ""
}

variable "subnetwork_cidr" {
  description = "Subnetwork primary CIDR (managed mode only)"
  type        = string
  default     = "10.10.0.0/20"
}

variable "pods_cidr" {
  description = "Secondary range CIDR for GKE pods (managed mode only)"
  type        = string
  default     = "10.20.0.0/14"
}

variable "services_cidr" {
  description = "Secondary range CIDR for GKE services (managed mode only)"
  type        = string
  default     = "10.30.0.0/20"
}

# ----------------------------------------------------------------------
# Cluster defaults (ADR-015 PR-A3)
# ----------------------------------------------------------------------
# Hardening + isolation knobs that operators flip per environment.
# Default values target staging/prod; dev overlays may relax via tfvars.
# ----------------------------------------------------------------------

variable "enable_private_endpoint" {
  description = <<-EOT
    Lock the GKE control plane to PRIVATE access only (no public endpoint).
    Default true so staging/prod are secure by default. Dev environments
    without bastion/VPN access may explicitly set this to false and rely
    on master_authorized_networks. When true, kubectl
    must reach the master via the VPC (Cloud SQL Auth Proxy / IAP /
    bastion / VPN). PR-A3.
  EOT
  type        = bool
  default     = true
}

variable "node_oauth_scopes" {
  description = <<-EOT
    Minimal OAuth scopes for GKE node pools. Workload Identity is the
    pod-level auth mechanism; nodes only need logging/monitoring by
    default. Avoid cloud-platform unless a documented legacy dependency
    requires it.
  EOT
  type        = list(string)
  default = [
    "https://www.googleapis.com/auth/logging.write",
    "https://www.googleapis.com/auth/monitoring",
    # Image pulls from Artifact Registry authenticate as the node service
    # account, and a scope is an upper bound on what that account may do. The
    # node SA holds roles/artifactregistry.reader (scoped to this repository,
    # see iam.tf); without a storage read scope that grant can never be
    # exercised and private pulls fail. Still avoiding cloud-platform, per the
    # description above — this is the narrow scope, not the blanket one.
    "https://www.googleapis.com/auth/devstorage.read_only",
  ]
}

variable "master_authorized_networks" {
  description = <<-EOT
    CIDR blocks allowed to reach the GKE control plane.

    The authorized-networks block is always emitted, so an empty list means
    "enabled, no external CIDR allowed" — the restrictive reading. It used to
    mean the block was omitted entirely, which GKE reads as no restriction at
    all.

    REQUIRED when enable_private_endpoint = false: a public control plane has
    to be constrained. That pairing is enforced by a precondition on
    google_container_cluster.gke and fails at plan time.

    Format: list of objects with cidr_block + display_name.
  EOT
  type = list(object({
    cidr_block   = string
    display_name = string
  }))
  default = []
}

variable "system_node_count" {
  description = "Initial nodes in the SYSTEM pool (kube-system, monitoring, ingress). PR-A3."
  type        = number
  default     = 1
}

variable "system_machine_type" {
  description = "Machine type for SYSTEM pool. Smaller than workload — these nodes only run cluster infra."
  type        = string
  default     = "e2-small"
}

variable "workload_node_taint_key" {
  description = "Taint key applied to the workload pool. ML pods need a matching toleration."
  type        = string
  default     = "workload-type"
}

variable "workload_node_taint_value" {
  description = "Taint value applied to the workload pool."
  type        = string
  default     = "ml-services"
}

# ----------------------------------------------------------------------
# Parity with AWS — secrets / logging / services
# ----------------------------------------------------------------------
# These variables mirror the AWS-side defaults so a single set of
# overlay tfvars works for both clouds without per-cloud divergence.
# ----------------------------------------------------------------------

variable "service_names" {
  description = <<-EOT
    Logical service names that need per-service Secret Manager entries
    + log buckets + (future) Artifact Registry repos. One entry per ML
    service deployed to this cluster. Mirrors AWS variable.service_names.
  EOT
  type        = list(string)
  default     = ["{@ service_kebab @}"]
}

variable "secret_names" {
  description = <<-EOT
    Logical names of secrets to provision (one Secret Manager entry
    per name × service). Defaults to the canonical set used by the
    template: api_key (predict auth), admin_api_key (admin gate),
    mlflow_password. Mirrors AWS variable.secret_names.
  EOT
  type        = list(string)
  default     = ["api_key", "admin_api_key", "mlflow_password"]
}

variable "log_retention_days" {
  description = <<-EOT
    Cloud Logging bucket retention in days. Default 30 matches AWS dev
    tier; overlays bump to 90 (staging) and 365 (prod). Note: GCP
    enforces a hard floor of 1 day and a maximum of 3650 days
    (10 years). Mirrors AWS variable.log_retention_days.
  EOT
  type        = number
  default     = 30

  validation {
    condition     = var.log_retention_days >= 1 && var.log_retention_days <= 3650
    error_message = "log_retention_days must be 1..3650 (Cloud Logging max ~10y)."
  }
}

variable "billing_account" {
  description = <<-EOT
    GCP Billing Account ID (without the 'billingAccounts/' prefix) used
    to scope the budget alert. Required when using google_billing_budget.
    Find via: gcloud billing accounts list.
    Empty string disables the budget resource — useful for environments
    without billing-admin permissions on the calling principal.
  EOT
  type        = string
  default     = ""
}

variable "budget_notification_emails" {
  description = "Email addresses to notify on budget threshold breaches."
  type        = list(string)
  default     = []
}

# ----------------------------------------------------------------------
# Edge protection — Cloud Armor (ADR-042, D-38)
# ----------------------------------------------------------------------
variable "enable_edge_protection" {
  description = <<-EOT
    Create the Cloud Armor security policy + SSL policy in security.tf.
    Defaults to false — these are public-facing security resources that
    should only be created after a deliberate adopter decision (`/edge-setup`,
    CONSULT in every environment per ADR-042 §2.2), never as a side effect
    of an unrelated `terraform apply`.
  EOT
  type        = bool
  default     = false
}

variable "waf_mode" {
  description = <<-EOT
    Action for the OWASP preconfigured WAF rules: "count" (observe matches,
    take no action — the recommended starting point to measure false
    positives against real traffic) or "deny(403)" (actually block). See
    docs/runbooks/edge-protection-setup.md for the graduation process.
  EOT
  type        = string
  default     = "count"

  validation {
    condition     = contains(["count", "deny(403)"], var.waf_mode)
    error_message = "waf_mode must be 'count' or 'deny(403)'."
  }
}

variable "rate_limit_requests_per_minute" {
  description = <<-EOT
    Per-IP request threshold over a 60-second window before Cloud Armor
    responds 429 (throttle, not a hard ban). Starting point only — tune per
    service based on observed p99 request rate. NOTE: not directly comparable
    to the AWS module's rate_limit_requests_per_5min (WAFv2 rate-based rules
    use a fixed 5-minute window, a different mechanism) — see the
    cloud-equivalence matrix in docs/runbooks/edge-protection-setup.md.
  EOT
  type        = number
  default     = 600
}
