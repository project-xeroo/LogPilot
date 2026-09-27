variable "name" { type = string }
variable "vpc_id" { type = string }
variable "private_subnet_ids" { type = list(string) }
variable "kubernetes_version" {
  type    = string
  default = "1.30"
}
variable "public_endpoint" {
  type    = bool
  default = false
}
variable "public_endpoint_cidrs" {
  type    = list(string)
  default = []
}
variable "core_instance_types" {
  type    = list(string)
  default = ["m6i.large"]
}
variable "core_nodes" {
  type = object({ min = number, desired = number, max = number })
}
variable "worker_instance_types" {
  type    = list(string)
  default = ["c6i.xlarge"]
}
variable "worker_capacity_type" {
  description = "ON_DEMAND or SPOT (workers are retry-safe: Celery task_acks_late + exponential backoff)"
  type        = string
  default     = "ON_DEMAND"
}
variable "worker_nodes" {
  type = object({ min = number, desired = number, max = number })
}
variable "storage_policy_arn" {
  description = "IAM policy from the storage module granting the log bucket + KMS key"
  type        = string
  default     = null
}
variable "tags" {
  type    = map(string)
  default = {}
}
