# Managed Kubernetes (PRD 12.1): EKS with autoscaling managed node groups, secrets encrypted with KMS, IRSA for
# least-privilege IAM per workload.
terraform {
  required_version = ">= 1.6"
  required_providers { aws = { source = "hashicorp/aws", version = "~> 5.60" } }
}

module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "~> 20.24"

  cluster_name    = "${var.name}-eks"
  cluster_version = var.kubernetes_version

  vpc_id     = var.vpc_id
  subnet_ids = var.private_subnet_ids

  cluster_endpoint_public_access       = var.public_endpoint
  cluster_endpoint_public_access_cidrs = var.public_endpoint_cidrs
  enable_irsa                          = true
  create_kms_key                       = true
  cluster_encryption_config            = { resources = ["secrets"] }
  cluster_enabled_log_types            = ["api", "audit", "authenticator"]

  eks_managed_node_groups = {
    # general workloads (API, console, supporting services)
    core = {
      instance_types = var.core_instance_types
      min_size       = var.core_nodes.min
      desired_size   = var.core_nodes.desired
      max_size       = var.core_nodes.max
      labels         = { "logpilot/pool" = "core" }
    }
    # elastic processing/forecasting/ingestion workers: scale with log volume
    workers = {
      instance_types = var.worker_instance_types
      capacity_type  = var.worker_capacity_type
      min_size       = var.worker_nodes.min
      desired_size   = var.worker_nodes.desired
      max_size       = var.worker_nodes.max
      labels         = { "logpilot/pool" = "workers" }
    }
  }
  tags = var.tags
}

# Workload identity: the LogPilot service account may use only the log bucket and its KMS key.
data "aws_iam_policy_document" "workload_assume" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [module.eks.oidc_provider_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "${module.eks.oidc_provider}:sub"
      values   = ["system:serviceaccount:logpilot:logpilot"]
    }
  }
}

resource "aws_iam_role" "workload" {
  name               = "${var.name}-workload"
  assume_role_policy = data.aws_iam_policy_document.workload_assume.json
  tags               = var.tags
}

resource "aws_iam_role_policy_attachment" "workload_storage" {
  count      = var.storage_policy_arn == null ? 0 : 1
  role       = aws_iam_role.workload.name
  policy_arn = var.storage_policy_arn
}
