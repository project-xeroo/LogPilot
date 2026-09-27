output "cluster_name" { value = module.eks.cluster_name }
output "cluster_endpoint" { value = module.eks.cluster_endpoint }
output "cluster_certificate_authority_data" { value = module.eks.cluster_certificate_authority_data }
output "node_security_group_id" { value = module.eks.node_security_group_id }
output "oidc_provider_arn" { value = module.eks.oidc_provider_arn }
output "workload_role_arn" {
  description = "Annotate the logpilot ServiceAccount with this (eks.amazonaws.com/role-arn)"
  value       = aws_iam_role.workload.arn
}
