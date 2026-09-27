# LogPilot dev environment - capacity tier: Starter   (PRD 12.3)
# Usage:  terraform init && terraform plan -var 'bucket_suffix=<unique>' && terraform apply
terraform {
  required_version = ">= 1.6"
  required_providers {
    aws        = { source = "hashicorp/aws", version = "~> 5.60" }
    helm       = { source = "hashicorp/helm", version = "~> 2.14" }
    kubernetes = { source = "hashicorp/kubernetes", version = "~> 2.31" }
    random     = { source = "hashicorp/random", version = "~> 3.6" }
  }
  # Remote state (create the bucket + lock table once, then uncomment):
  # backend "s3" {
  #   bucket         = "REPLACE-tfstate"
  #   key            = "logpilot/dev/terraform.tfstate"
  #   region         = "us-east-1"
  #   dynamodb_table = "REPLACE-tflock"
  #   encrypt        = true
  # }
}

variable "bucket_suffix" {
  description = "Makes the log bucket name globally unique"
  type        = string
}
variable "admin_cidrs" {
  description = "CIDRs allowed to reach the EKS public endpoint (empty = private endpoint only)"
  type        = list(string)
  default     = []
}

locals {
  name = "logpilot-dev"
  tags = { Project = "logpilot", Environment = "dev", ManagedBy = "terraform" }
}

provider "aws" {
  region = "us-east-1"
  default_tags { tags = local.tags }
}

module "vpc" {
  source             = "../../modules/vpc"
  name               = local.name
  cidr               = "10.10.0.0/16"
  az_count           = 2
  single_nat_gateway = true
  tags               = local.tags
}

module "storage" {
  source         = "../../modules/storage"
  name           = local.name
  bucket_name    = "${local.name}-logs-${var.bucket_suffix}"
  retention_days = 30
  force_destroy  = true
  tags           = local.tags
}

module "eks" {
  source                = "../../modules/eks"
  name                  = local.name
  vpc_id                = module.vpc.vpc_id
  private_subnet_ids    = module.vpc.private_subnet_ids
  public_endpoint       = length(var.admin_cidrs) > 0
  public_endpoint_cidrs = var.admin_cidrs
  core_nodes            = { min = 1, desired = 2, max = 3 }
  worker_nodes          = { min = 1, desired = 2, max = 4 }
  worker_capacity_type  = "SPOT"
  storage_policy_arn    = module.storage.workload_policy_arn
  tags                  = local.tags
}

module "rds" {
  source                     = "../../modules/rds"
  name                       = local.name
  vpc_id                     = module.vpc.vpc_id
  db_subnet_group_name       = module.vpc.database_subnet_group_name
  allowed_security_group_ids = [module.eks.node_security_group_id]
  instance_class             = "db.t4g.large"
  multi_az                   = false
  backup_retention_days      = 7
  deletion_protection        = false
  tags                       = local.tags
}

module "redis" {
  source                     = "../../modules/redis"
  name                       = local.name
  vpc_id                     = module.vpc.vpc_id
  private_subnet_ids         = module.vpc.private_subnet_ids
  allowed_security_group_ids = [module.eks.node_security_group_id]
  node_type                  = "cache.t4g.small"
  replicas                   = 0
  tags                       = local.tags
}

# Kubernetes/Helm providers talk to the cluster created above (needed by the vector-store module)
data "aws_eks_cluster_auth" "this" { name = module.eks.cluster_name }
provider "kubernetes" {
  host                   = module.eks.cluster_endpoint
  cluster_ca_certificate = base64decode(module.eks.cluster_certificate_authority_data)
  token                  = data.aws_eks_cluster_auth.this.token
}
provider "helm" {
  kubernetes {
    host                   = module.eks.cluster_endpoint
    cluster_ca_certificate = base64decode(module.eks.cluster_certificate_authority_data)
    token                  = data.aws_eks_cluster_auth.this.token
  }
}

module "vector_store" {
  source       = "../../modules/vector-store"
  replicas     = 1
  storage_size = "50Gi"
  cpu          = "1"
  memory       = "2Gi"
}

output "cluster_name" { value = module.eks.cluster_name }
output "workload_role_arn" { value = module.eks.workload_role_arn }
output "log_bucket" { value = module.storage.bucket }
output "postgres_endpoint" { value = module.rds.endpoint }
output "database_url_secret_arn" { value = module.rds.database_url_secret_arn }
output "redis_endpoint" { value = module.redis.primary_endpoint }
output "qdrant_url" { value = module.vector_store.qdrant_url }
output "next_steps" {
  value = "aws eks update-kubeconfig --name ${module.eks.cluster_name}; kubectl apply -k ../../k8s/overlays/dev (after filling infra/k8s/base/configmap.yaml from these outputs and creating the logpilot-secrets secret)"
}
