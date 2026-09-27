# Managed vector store (PRD 7.1): Qdrant with dedicated, persistent, replicated capacity for 10M+ vectors.
# Deployed with the official Helm chart into its own namespace on the EKS cluster. To use Qdrant Cloud instead, skip
# this module and point QDRANT_URL / QDRANT_API_KEY at the hosted cluster.
terraform {
  required_version = ">= 1.6"
  required_providers {
    helm       = { source = "hashicorp/helm", version = "~> 2.14" }
    kubernetes = { source = "hashicorp/kubernetes", version = "~> 2.31" }
    random     = { source = "hashicorp/random", version = "~> 3.6" }
  }
}

resource "random_password" "api_key" {
  length  = 40
  special = false
}

resource "kubernetes_namespace" "vector" {
  metadata { name = "logpilot-vector" }
}

resource "helm_release" "qdrant" {
  name       = "qdrant"
  namespace  = kubernetes_namespace.vector.metadata[0].name
  repository = "https://qdrant.github.io/qdrant-helm"
  chart      = "qdrant"
  version    = var.chart_version

  values = [yamlencode({
    replicaCount = var.replicas
    apiKey       = random_password.api_key.result
    persistence  = { size = var.storage_size, storageClassName = var.storage_class }
    resources    = { requests = { cpu = var.cpu, memory = var.memory }, limits = { memory = var.memory } }
    config       = { cluster = { enabled = var.replicas > 1 } }
    nodeSelector = { "logpilot/pool" = "core" }
    podDisruptionBudget = { enabled = true, maxUnavailable = 1 }
  })]
}
