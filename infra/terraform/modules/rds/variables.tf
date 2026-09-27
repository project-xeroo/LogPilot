variable "name" { type = string }
variable "vpc_id" { type = string }
variable "db_subnet_group_name" { type = string }
variable "allowed_security_group_ids" { type = list(string) }
variable "engine_version" {
  type    = string
  default = "15.7"
}
variable "instance_class" {
  description = "Capacity tier: Starter db.t4g.large, Growth db.r6g.xlarge, Enterprise db.r6g.4xlarge"
  type        = string
  default     = "db.t4g.large"
}
variable "storage_gb" {
  type    = number
  default = 100
}
variable "max_storage_gb" {
  type    = number
  default = 2000
}
variable "multi_az" {
  type    = bool
  default = true
}
variable "backup_retention_days" {
  type    = number
  default = 14
}
variable "deletion_protection" {
  type    = bool
  default = true
}
variable "tags" {
  type    = map(string)
  default = {}
}
