output "primary_endpoint" { value = aws_elasticache_replication_group.this.primary_endpoint_address }
output "auth_token" {
  value     = random_password.auth.result
  sensitive = true
}
output "redis_url" {
  description = "rediss:// URL (TLS + AUTH) for REDIS_URL; append /1 and /2 for the Celery broker and result backend"
  value       = "rediss://:${random_password.auth.result}@${aws_elasticache_replication_group.this.primary_endpoint_address}:6379/0"
  sensitive   = true
}
