# Network isolation (PRD 10.3): private subnets for every workload and data store, public subnets only for the load
# balancer, NAT for controlled egress, flow logs for audit.
terraform {
  required_version = ">= 1.6"
  required_providers { aws = { source = "hashicorp/aws", version = "~> 5.60" } }
}

data "aws_availability_zones" "available" { state = "available" }

locals {
  azs = slice(data.aws_availability_zones.available.names, 0, var.az_count)
}

module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "~> 5.13"

  name = "${var.name}-vpc"
  cidr = var.cidr
  azs  = local.azs

  private_subnets  = [for i, _ in local.azs : cidrsubnet(var.cidr, 4, i)]
  public_subnets   = [for i, _ in local.azs : cidrsubnet(var.cidr, 8, 200 + i)]
  database_subnets = [for i, _ in local.azs : cidrsubnet(var.cidr, 8, 220 + i)]

  create_database_subnet_group = true
  enable_nat_gateway           = true
  single_nat_gateway           = var.single_nat_gateway # true for dev/staging (cost), false for prod (per-AZ NAT)
  one_nat_gateway_per_az       = !var.single_nat_gateway
  enable_dns_hostnames         = true

  enable_flow_log                                 = true
  create_flow_log_cloudwatch_iam_role             = true
  create_flow_log_cloudwatch_log_group            = true
  flow_log_cloudwatch_log_group_retention_in_days = 90

  public_subnet_tags  = { "kubernetes.io/role/elb" = 1 }
  private_subnet_tags = { "kubernetes.io/role/internal-elb" = 1 }
  tags                = var.tags
}
