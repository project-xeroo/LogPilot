output "endpoint" { value = aws_db_instance.this.address }
output "port" { value = aws_db_instance.this.port }
output "database_url_secret_arn" {
  description = "Secrets Manager secret holding DATABASE_URL (sync into the logpilot-secrets Kubernetes secret)"
  value       = aws_secretsmanager_secret.url.arn
}
