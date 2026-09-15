variable "project_name" {
  description = "Project name used in resource naming"
  type        = string
}

variable "region" {
  description = "AWS region"
  type        = string
  default     = "us-east-1"
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

variable "k8s_version" {
  description = "Kubernetes version for EKS"
  type        = string
  default     = "1.29"
}

variable "instance_type" {
  description = "EKS node instance type"
  type        = string
  default     = "t3.medium"
}

variable "initial_node_count" {
  description = "Desired number of nodes"
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

variable "subnet_ids" {
  description = "List of subnet IDs for EKS (required when network_mode='existing'; ignored when 'managed')"
  type        = list(string)
  default     = []
}

# -----------------------------------------------------------------------------
# PR-R2-6 — AWS parity additions
# -----------------------------------------------------------------------------
# Each variable below has a sane production default and an explicit reason
# (linked to ADR-016 §"PR-R2-6 — AWS parity") so the template is operable
# without forcing every adopter to read every doc first.
# -----------------------------------------------------------------------------

variable "allow_public_endpoint" {
  description = <<-EOT
    EKS API server reachability. Default is private-only (audit R2 §3.1).
    Set true for environments where bastion/VPN access is impractical;
    public access is gated by `public_endpoint_access_cidrs` so it is
    never wide-open. ADR-018 captures the parity tier where flipping
    this is acceptable.
  EOT
  type        = bool
  default     = false
}

variable "public_endpoint_access_cidrs" {
  description = <<-EOT
    CIDR blocks that may reach the EKS public endpoint when
    `allow_public_endpoint=true`. Empty list = block everyone, which
    AWS would reject — so when `allow_public_endpoint=true` this MUST
    be a non-empty list of office/VPN CIDRs.
  EOT
  type        = list(string)
  default     = []
}

variable "model_archive_days" {
  description = "Days before archiving model artifacts to S3 Glacier Instant Retrieval (parity with GCS NEARLINE)."
  type        = number
  default     = 90
}

variable "model_delete_days" {
  description = "Days before deleting old model artifact versions (post-archive lifecycle)."
  type        = number
  default     = 365
}

variable "log_retention_days" {
  description = <<-EOT
    CloudWatch log retention in days. Default 30 matches the dev tier;
    overlays bump this to 90 (staging) and 365 (prod). Setting 0 means
    "never expire" — explicitly rejected by the validate block below.
  EOT
  type        = number
  default     = 30
  validation {
    condition     = var.log_retention_days >= 1 && var.log_retention_days <= 3653
    error_message = "log_retention_days must be 1..3653 (CloudWatch caps at ~10y)."
  }
}

variable "monthly_budget" {
  description = "Monthly budget in USD (used by AWS Budgets alarm; parity with GCP variant)."
  type        = number
  default     = 500
}

variable "enable_secret_rotation" {
  description = <<-EOT
    Enable Secrets Manager automatic rotation. Off by default because
    rotation requires a customer-supplied Lambda (rotation logic is
    secret-shape-specific, e.g. RDS vs API key). Operators flip this
    to true after wiring `rotation_lambda_arn`.
  EOT
  type        = bool
  default     = false
}

variable "rotation_lambda_arn" {
  description = "ARN of the Lambda implementing the rotation contract. Required iff enable_secret_rotation=true."
  type        = string
  default     = ""
}

variable "secret_names" {
  description = <<-EOT
    Logical names of secrets to provision (one Secrets Manager entry
    per name). Defaults to the canonical set used by the template:
    api_key (predict auth), admin_api_key (admin gate), mlflow_password.
  EOT
  type        = list(string)
  default     = ["api_key", "admin_api_key", "mlflow_password"]
}

variable "service_names" {
  description = <<-EOT
    Logical service names that need IRSA roles (one per ML service
    deployed to this cluster). Each gets a narrow per-service IAM
    policy (read on data bucket, write on its own model prefix).
  EOT
  type        = list(string)
  default     = ["{@ service_kebab @}"]
}

# ----------------------------------------------------------------------
# Network mode (ADR-017 / PR-A1)
# ----------------------------------------------------------------------
# 'managed':  template creates VPC + 3 private + 3 public subnets + NAT
# 'existing': caller provides subnet_ids (current default — backwards-compat)
# ----------------------------------------------------------------------
variable "network_mode" {
  description = "Network topology mode: 'managed' (template creates VPC) or 'existing' (use provided subnet_ids)"
  type        = string
  default     = "existing"

  validation {
    condition     = contains(["managed", "existing"], var.network_mode)
    error_message = "network_mode must be 'managed' or 'existing'"
  }
}

variable "vpc_cidr" {
  description = "VPC CIDR block (managed mode only)"
  type        = string
  default     = "10.0.0.0/16"
}

variable "availability_zones" {
  description = "AZ list for managed-mode VPC. Defaults to 3 AZs in us-east-1."
  type        = list(string)
  default     = ["us-east-1a", "us-east-1b", "us-east-1c"]
}

# ----------------------------------------------------------------------
# GitHub OIDC (ADR-017 / PR-A1)
# ----------------------------------------------------------------------
# Required for the CI + Deploy roles' trust policies. Format:
#   "owner/repo" e.g. "DuqueOM/ml-service-template"
# Empty string skips creation of CI/Deploy roles (callers using
# long-lived AWS keys instead).
# ----------------------------------------------------------------------
variable "github_repo" {
  description = "GitHub repo (owner/name) allowed to assume CI + Deploy roles via OIDC. Empty = skip."
  type        = string
  default     = ""
}

# ----------------------------------------------------------------------
# Cluster defaults (ADR-015 PR-A3) — system/workload node group split
# ----------------------------------------------------------------------
variable "system_node_count" {
  description = "Initial nodes in the SYSTEM node group (kube-system, monitoring, ingress). PR-A3."
  type        = number
  default     = 1
}

variable "system_instance_type" {
  description = "Instance type for SYSTEM node group. Smaller than workload — these only run cluster infra."
  type        = string
  default     = "t3.small"
}

variable "workload_node_taint_key" {
  description = "Taint key applied to the workload node group. ML pods need a matching toleration."
  type        = string
  default     = "workload-type"
}

variable "workload_node_taint_value" {
  description = "Taint value applied to the workload node group."
  type        = string
  default     = "ml-services"
}

# ----------------------------------------------------------------------
# Edge protection — AWS WAFv2 + Shield Standard (ADR-042, D-38)
# ----------------------------------------------------------------------
variable "enable_edge_protection" {
  description = <<-EOT
    Create the WAFv2 Web ACL in security.tf. Defaults to false — this is a
    public-facing security resource that should only be created after a
    deliberate adopter decision (`/edge-setup`, CONSULT in every environment
    per ADR-042 §2.2), never as a side effect of an unrelated
    `terraform apply`.
  EOT
  type        = bool
  default     = false
}

variable "waf_mode" {
  description = <<-EOT
    Override action for the AWS Managed Rule groups: "count" (observe
    matches, take no action — the recommended starting point to measure
    false positives against real traffic) or "block" (let the managed rule
    group's own block action take effect). See
    docs/runbooks/edge-protection-setup.md for the graduation process.
  EOT
  type        = string
  default     = "count"

  validation {
    condition     = contains(["count", "block"], var.waf_mode)
    error_message = "waf_mode must be 'count' or 'block'."
  }
}

variable "rate_limit_requests_per_5min" {
  description = <<-EOT
    Per-IP request threshold over WAFv2's fixed 5-minute rolling window
    before the rate-based rule blocks the source IP. AWS enforces a minimum
    of 100. Starting point only — tune per service based on observed
    traffic. NOTE: not directly comparable to the GCP module's
    rate_limit_requests_per_minute (Cloud Armor uses a configurable,
    shorter window) — see the cloud-equivalence matrix in
    docs/runbooks/edge-protection-setup.md.
  EOT
  type        = number
  default     = 3000

  validation {
    condition     = var.rate_limit_requests_per_5min >= 100
    error_message = "rate_limit_requests_per_5min must be >= 100 (AWS WAFv2 minimum)."
  }
}
