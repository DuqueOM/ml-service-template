# =============================================================================
# Per-service IRSA roles + narrow policies (PR-R2-6, audit R2 §3.4).
#
# This file implements the `runtime` identity of the ADR-017 5-identity
# split (ci / deploy / runtime / drift / retrain). The `runtime` IRSA
# is per-service rather than shared so a compromised service's token
# cannot move laterally. See iam-roles-split.tf for ci/deploy/drift/
# retrain (shared infrastructure identities).
#
# Each entry in var.service_names gets:
#   * an IAM role assumable by the K8s ServiceAccount `<service>-sa`
#     in namespace `<service>-<dev|staging|prod>`, via the EKS OIDC
#     provider — the names the Kustomize overlays create (ADR-051);
#   * an inline policy with the *minimum* permissions the template's
#     standard service shape needs:
#         - read on the data bucket (ingest features)
#         - read+write on the service's own model prefix in the
#           models bucket (write new artifacts, read on rollback)
#         - read+write on the mlflow_artifacts bucket (per-run
#           artefacts; tracking server is the source of truth)
#         - decrypt on the S3 SSE KMS key (so reads of encrypted
#           objects actually work)
#         - GetSecretValue on the service's secrets only
#
# Anti-patterns explicitly excluded:
#   * No `Action: "*"` and no `Resource: "*"` — both would defeat the
#     audit's narrow-IRSA requirement.
#   * No s3:DeleteBucket / s3:PutBucketPolicy — the role can write
#     OBJECTS, never reconfigure infrastructure.
#   * No iam:* — a compromised pod cannot self-elevate.
#
# To deploy a real service:
#   1. Add its name to var.service_names.
#   2. Its overlays already annotate `<service>-sa` with
#        arn:aws:iam::<account>:role/<project_name>-<service>-irsa-<environment>
#      — replace the {AWS_ACCOUNT_ID} and {PROJECT_NAME} placeholders.
# =============================================================================

# OIDC issuer hostname (without the https:// prefix). The trust policy
# expects this exact form because the OIDC sub claim references
# "<host>:sub:system:serviceaccount:<ns>:<sa>".
locals {
  oidc_issuer_host = replace(
    aws_iam_openid_connect_provider.eks.url,
    "https://",
    "",
  )

  # Kubernetes coordinates of each service's pods, derived exactly as the
  # Kustomize overlays name them: namespace `<service>-<dev|staging|prod>`,
  # ServiceAccounts `<service>-sa` (runtime) and `<service>-drift-sa` /
  # `<service>-retrain-sa` (ADR-017). Terraform says `production` where the
  # overlays say `prod`. A binding to any other name is a binding to nothing:
  # the pod gets no cloud identity and fails at model download (ADR-051).
  k8s_env_suffix = var.environment == "production" ? "prod" : var.environment
}

resource "aws_iam_role" "service" {
  for_each = toset(var.service_names)

  name = "${var.project_name}-${each.value}-irsa-${var.environment}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Federated = aws_iam_openid_connect_provider.eks.arn
      }
      Action = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          # `aud` claim must be sts.amazonaws.com (set in the OIDC
          # provider client_id_list); `sub` must match the exact
          # ServiceAccount.
          "${local.oidc_issuer_host}:aud" = "sts.amazonaws.com"
          "${local.oidc_issuer_host}:sub" = "system:serviceaccount:${each.value}-${local.k8s_env_suffix}:${each.value}-sa"
        }
      }
    }]
  })

  tags = {
    environment = var.environment
    managed-by  = "terraform"
    service     = each.value
  }
}

resource "aws_iam_policy" "service" {
  for_each = toset(var.service_names)

  name        = "${var.project_name}-${each.value}-policy-${var.environment}"
  description = "Narrow per-service S3+KMS+Secrets policy (PR-R2-6) for ${each.value}."

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      # Data bucket — read only.
      {
        Sid    = "DataBucketReadOnly"
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
      # Models bucket — read+write only under the service's prefix.
      {
        Sid    = "ModelsBucketServicePrefix"
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject",
          "s3:GetObjectVersion",
        ]
        Resource = [
          "${aws_s3_bucket.models.arn}/${each.value}/*",
        ]
      },
      {
        Sid      = "ModelsBucketList"
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = [aws_s3_bucket.models.arn]
        Condition = {
          StringLike = {
            "s3:prefix" = ["${each.value}/*"]
          }
        }
      },
      # MLflow artifacts — full read/write (per-run keys are unique).
      {
        Sid    = "MlflowArtifactsReadWrite"
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
      # KMS — needed to actually read/write the SSE-encrypted objects.
      {
        Sid    = "S3SseKmsUse"
        Effect = "Allow"
        Action = [
          "kms:Decrypt",
          "kms:GenerateDataKey",
        ]
        Resource = [aws_kms_key.s3.arn]
      },
      # Secrets — only the per-service secrets, never others.
      # Resource ARN pattern matches `secret_names` provisioned in
      # secrets.tf with the service prefix; if `var.service_names`
      # changes, this glob still scopes correctly.
      {
        Sid    = "ServiceSecretsRead"
        Effect = "Allow"
        Action = [
          "secretsmanager:GetSecretValue",
          "secretsmanager:DescribeSecret",
        ]
        Resource = [
          "arn:aws:secretsmanager:${var.region}:*:secret:${var.project_name}/${each.value}/*",
        ]
      },
    ]
  })
}

resource "aws_iam_role_policy_attachment" "service" {
  for_each = toset(var.service_names)

  role       = aws_iam_role.service[each.value].name
  policy_arn = aws_iam_policy.service[each.value].arn
}

# Convenience output: the ServiceAccount annotation each overlay needs.
output "service_irsa_role_arns" {
  description = "Map of service name → IRSA role ARN for serviceaccount.yaml annotations."
  value       = { for s in var.service_names : s => aws_iam_role.service[s].arn }
}
