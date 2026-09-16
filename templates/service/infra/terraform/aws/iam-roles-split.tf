# ============================================================================
# Per-environment IAM split (ADR-017 / PR-A1) — AWS side
# ============================================================================
# Adds 4 roles to complement the existing per-service IRSA roles in iam.tf:
#
#   ci      — GitHub Actions: Terraform plan/apply, ECR push (OIDC)
#   deploy  — GitHub Actions: kubectl apply + image push (OIDC)
#   drift   — Drift CronJob: read CloudWatch + S3 reports (IRSA)
#   retrain — Retrain Job: read S3 data + write S3 models (IRSA)
#
# Why split off from iam.tf instead of edit-in-place:
#   * iam.tf is the per-service IRSA pattern (one role per service_name);
#     mixing per-purpose roles confuses the for_each contract.
#   * Keeping them in a separate file makes the split visible at first
#     glance — `ls iam*.tf` shows the structure.
#
# CI/Deploy roles are GATED on var.github_repo being non-empty:
#   * Adopters using long-lived AWS keys skip them entirely.
#   * Adopters using GitHub OIDC pass "owner/repo" and get federated trust.
# ============================================================================

# ---------------------------------------------------------------------------
# GitHub OIDC provider — used by CI + Deploy roles' trust policies
# ---------------------------------------------------------------------------
# Created only when github_repo is provided. The thumbprint is the well-known
# Sigstore-style fingerprint of GitHub's OIDC issuer; AWS deprecates manual
# thumbprint validation in favor of trusting the IAM service to handle it,
# so we keep the historical thumbprint for backwards-compatibility but the
# real validation is the audience + sub claims in the trust policy.
resource "aws_iam_openid_connect_provider" "github" {
  count = var.github_repo != "" ? 1 : 0

  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = ["6938fd4d98bab03faadb97b34396831e3780aea1"]

  tags = {
    environment = var.environment
    managed-by  = "terraform"
    purpose     = "github-actions-oidc"
  }
}

locals {
  # Restrict assume to the configured repo's main + release branches +
  # any tag (release workflow). Pull requests intentionally excluded —
  # they would let any forker assume CI roles.
  github_oidc_subs = var.github_repo != "" ? [
    "repo:${var.github_repo}:ref:refs/heads/main",
    "repo:${var.github_repo}:ref:refs/heads/release/*",
    "repo:${var.github_repo}:ref:refs/tags/*",
    # A job that declares `environment:` presents the ENVIRONMENT name as its
    # subject instead of its ref, and deploy-common.yml names environments
    # `aws-<environment>`. The bare `${var.environment}` matched no job, so no
    # deploy job could assume these roles (ADR-051 naming contract).
    "repo:${var.github_repo}:environment:aws-${var.environment}",
  ] : []

  service_ecr_repository_arns = [
    for name in var.service_names :
    "arn:aws:ecr:${var.region}:*:repository/${var.project_name}/${name}-predictor"
  ]
}

# ---------------------------------------------------------------------------
# 1. CI role — Terraform plan/apply + ECR push (GitHub Actions via OIDC)
# ---------------------------------------------------------------------------
resource "aws_iam_role" "ci" {
  count = var.github_repo != "" ? 1 : 0

  name        = "${var.project_name}-ci-${var.environment}"
  description = "GitHub Actions CI: Terraform + ECR push. ADR-017 PR-A1."

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Federated = aws_iam_openid_connect_provider.github[0].arn }
      Action    = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
        }
        StringLike = {
          "token.actions.githubusercontent.com:sub" = local.github_oidc_subs
        }
      }
    }]
  })

  tags = {
    environment = var.environment
    managed-by  = "terraform"
    purpose     = "ci"
  }
}

# CI permissions: read+write on infra resources, read+write on ECR.
# Deliberately NOT granted: SecretsManager mutation, IAM admin, S3 delete-bucket.
resource "aws_iam_policy" "ci" {
  count = var.github_repo != "" ? 1 : 0

  name        = "${var.project_name}-ci-policy-${var.environment}"
  description = "CI role: Terraform state access + ECR push. ADR-017."

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "EksReadOnly"
        Effect = "Allow"
        Action = [
          "eks:DescribeCluster",
          "eks:ListClusters",
        ]
        Resource = "*"
      },
      {
        Sid    = "EcrAuthToken"
        Effect = "Allow"
        Action = [
          "ecr:GetAuthorizationToken",
        ]
        Resource = "*"
      },
      {
        Sid    = "EcrPush"
        Effect = "Allow"
        Action = [
          "ecr:BatchCheckLayerAvailability",
          "ecr:GetDownloadUrlForLayer",
          "ecr:BatchGetImage",
          "ecr:InitiateLayerUpload",
          "ecr:UploadLayerPart",
          "ecr:CompleteLayerUpload",
          "ecr:PutImage",
        ]
        Resource = local.service_ecr_repository_arns
      },
      {
        Sid    = "TerraformStateBucket"
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:ListBucket",
        ]
        Resource = [
          "arn:aws:s3:::${var.project_name}-tfstate-${var.environment}",
          "arn:aws:s3:::${var.project_name}-tfstate-${var.environment}/*",
        ]
      },
      {
        Sid      = "TerraformStateLocks"
        Effect   = "Allow"
        Action   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:DeleteItem"]
        Resource = "arn:aws:dynamodb:${var.region}:*:table/${var.project_name}-tfstate-lock-${var.environment}"
      },
    ]
  })
}

resource "aws_iam_role_policy_attachment" "ci" {
  count      = var.github_repo != "" ? 1 : 0
  role       = aws_iam_role.ci[0].name
  policy_arn = aws_iam_policy.ci[0].arn
}

# ---------------------------------------------------------------------------
# 2. Deploy role — kubectl apply + image push (GitHub Actions via OIDC)
# ---------------------------------------------------------------------------
resource "aws_iam_role" "deploy" {
  count = var.github_repo != "" ? 1 : 0

  name        = "${var.project_name}-deploy-${var.environment}"
  description = "GitHub Actions Deploy: image push + kubectl apply. ADR-017 PR-A1."

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Federated = aws_iam_openid_connect_provider.github[0].arn }
      Action    = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
        }
        StringLike = {
          "token.actions.githubusercontent.com:sub" = local.github_oidc_subs
        }
      }
    }]
  })

  tags = {
    environment = var.environment
    managed-by  = "terraform"
    purpose     = "deploy"
  }
}

resource "aws_iam_policy" "deploy" {
  count = var.github_repo != "" ? 1 : 0

  name        = "${var.project_name}-deploy-policy-${var.environment}"
  description = "Deploy role: ECR push + EKS describe (kubectl). ADR-017."

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "EcrAuthToken"
        Effect = "Allow"
        Action = [
          "ecr:GetAuthorizationToken",
        ]
        Resource = "*"
      },
      {
        Sid    = "EcrPush"
        Effect = "Allow"
        Action = [
          "ecr:BatchCheckLayerAvailability",
          "ecr:InitiateLayerUpload",
          "ecr:UploadLayerPart",
          "ecr:CompleteLayerUpload",
          "ecr:PutImage",
          "ecr:BatchGetImage",
          "ecr:GetDownloadUrlForLayer",
        ]
        Resource = local.service_ecr_repository_arns
      },
      {
        Sid      = "EksDescribeForKubeconfig"
        Effect   = "Allow"
        Action   = ["eks:DescribeCluster"]
        Resource = aws_eks_cluster.eks.arn
      },
    ]
  })
}

resource "aws_iam_role_policy_attachment" "deploy" {
  count      = var.github_repo != "" ? 1 : 0
  role       = aws_iam_role.deploy[0].name
  policy_arn = aws_iam_policy.deploy[0].arn
}

# ---------------------------------------------------------------------------
# 3. Drift IRSA role — drift CronJob (read CloudWatch, write S3 reports)
# ---------------------------------------------------------------------------
# Bound to the drift KSA the overlays create: <service>-<env>/<service>-drift-sa
# A separate role (vs the per-service IRSA in iam.tf) means a compromised
# drift CronJob cannot read predictions or write models — only its own
# reports bucket.
resource "aws_iam_role" "drift" {
  for_each = toset(var.service_names)

  name = "${var.project_name}-${each.value}-drift-irsa-${var.environment}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Federated = aws_iam_openid_connect_provider.eks.arn }
      Action    = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "${local.oidc_issuer_host}:aud" = "sts.amazonaws.com"
          "${local.oidc_issuer_host}:sub" = "system:serviceaccount:${each.value}-${local.k8s_env_suffix}:${each.value}-drift-sa"
        }
      }
    }]
  })

  tags = {
    environment = var.environment
    managed-by  = "terraform"
    service     = each.value
    purpose     = "drift"
  }
}

resource "aws_iam_policy" "drift" {
  for_each = toset(var.service_names)

  name        = "${var.project_name}-${each.value}-drift-policy-${var.environment}"
  description = "Drift role: read metrics + write reports. ADR-017."

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "ReadMetrics"
        Effect = "Allow"
        Action = [
          "cloudwatch:GetMetricData",
          "cloudwatch:GetMetricStatistics",
          "cloudwatch:ListMetrics",
        ]
        Resource = "*"
      },
      {
        Sid    = "ReadReferenceDistributions"
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:ListBucket",
        ]
        Resource = [
          aws_s3_bucket.data.arn,
          "${aws_s3_bucket.data.arn}/${each.value}/reference/*",
          # The drift CronJob's production-fetch step reads this object;
          # without it the job fails AccessDenied before computing PSI.
          "${aws_s3_bucket.data.arn}/${each.value}/latest.csv",
        ]
      },
      {
        Sid    = "WriteDriftReports"
        Effect = "Allow"
        Action = [
          "s3:PutObject",
        ]
        Resource = [
          "${aws_s3_bucket.data.arn}/${each.value}/drift-reports/*",
        ]
      },
      {
        Sid      = "S3SseKmsUse"
        Effect   = "Allow"
        Action   = ["kms:Decrypt", "kms:GenerateDataKey"]
        Resource = [aws_kms_key.s3.arn]
      },
    ]
  })
}

resource "aws_iam_role_policy_attachment" "drift" {
  for_each   = toset(var.service_names)
  role       = aws_iam_role.drift[each.value].name
  policy_arn = aws_iam_policy.drift[each.value].arn
}

# ---------------------------------------------------------------------------
# 4. Retrain IRSA role — retrain Job (read data, write models)
# ---------------------------------------------------------------------------
resource "aws_iam_role" "retrain" {
  for_each = toset(var.service_names)

  name = "${var.project_name}-${each.value}-retrain-irsa-${var.environment}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Federated = aws_iam_openid_connect_provider.eks.arn }
      Action    = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "${local.oidc_issuer_host}:aud" = "sts.amazonaws.com"
          "${local.oidc_issuer_host}:sub" = "system:serviceaccount:${each.value}-${local.k8s_env_suffix}:${each.value}-retrain-sa"
        }
      }
    }]
  })

  tags = {
    environment = var.environment
    managed-by  = "terraform"
    service     = each.value
    purpose     = "retrain"
  }
}

resource "aws_iam_policy" "retrain" {
  for_each = toset(var.service_names)

  name        = "${var.project_name}-${each.value}-retrain-policy-${var.environment}"
  description = "Retrain role: read data + write new model versions. ADR-017."

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "ReadTrainingData"
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:ListBucket",
        ]
        Resource = [
          aws_s3_bucket.data.arn,
          "${aws_s3_bucket.data.arn}/*",
        ]
      },
      {
        Sid    = "WriteNewModelVersions"
        Effect = "Allow"
        Action = [
          "s3:PutObject",
          "s3:GetObject",
          "s3:ListBucket",
        ]
        Resource = [
          aws_s3_bucket.models.arn,
          "${aws_s3_bucket.models.arn}/${each.value}/*",
        ]
      },
      {
        Sid    = "MlflowArtifactsForRetrainRun"
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:ListBucket",
        ]
        Resource = [
          aws_s3_bucket.mlflow_artifacts.arn,
          "${aws_s3_bucket.mlflow_artifacts.arn}/*",
        ]
      },
      {
        Sid      = "S3SseKmsUse"
        Effect   = "Allow"
        Action   = ["kms:Decrypt", "kms:GenerateDataKey"]
        Resource = [aws_kms_key.s3.arn]
      },
    ]
  })
}

resource "aws_iam_role_policy_attachment" "retrain" {
  for_each   = toset(var.service_names)
  role       = aws_iam_role.retrain[each.value].name
  policy_arn = aws_iam_policy.retrain[each.value].arn
}

# ---------------------------------------------------------------------------
# Outputs — consumed by GitHub Actions secrets + K8s ServiceAccount annotations
# ---------------------------------------------------------------------------
output "ci_role_arn" {
  description = "ARN of CI role (set as AWS_ROLE_TO_ASSUME in GHA). Empty when github_repo is unset."
  value       = var.github_repo != "" ? aws_iam_role.ci[0].arn : ""
}

output "deploy_role_arn" {
  description = "ARN of Deploy role. Empty when github_repo is unset."
  value       = var.github_repo != "" ? aws_iam_role.deploy[0].arn : ""
}

output "drift_irsa_role_arns" {
  description = "Map of service name → drift IRSA role ARN for drift KSA annotations."
  value       = { for s in var.service_names : s => aws_iam_role.drift[s].arn }
}

output "retrain_irsa_role_arns" {
  description = "Map of service name → retrain IRSA role ARN for retrain KSA annotations."
  value       = { for s in var.service_names : s => aws_iam_role.retrain[s].arn }
}
