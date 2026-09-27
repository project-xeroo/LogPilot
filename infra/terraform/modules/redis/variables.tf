variable "name" { type = string }
variable "vpc_id" { type = string }
variable "private_subnet_ids" { type = list(string) }
variable "allowed_security_group_ids" { type = list(string) }
variable "node_type" {
  description = "Starter cache.t4g.small, Growth cache.m6g.large, Enterprise cache.r6g.xlarge"
  type        = string
  default     = "cache.t4g.small"
}
variable "replicas" {
  description = "Read replicas per shard (>=1 enables multi-AZ failover)"
  type        = number
  default     = 1
}
variable "tags" {
  type    = map(string)
  default = {}
}
