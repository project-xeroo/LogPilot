output "qdrant_url" { value = "http://qdrant.${kubernetes_namespace.vector.metadata[0].name}.svc:6333" }
output "api_key" {
  value     = random_password.api_key.result
  sensitive = true
}
