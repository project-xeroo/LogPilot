variable "chart_version" {
  type    = string
  default = "1.12.0"
}
variable "replicas" {
  description = "1 for dev, 3 for staging/prod (distributed mode)"
  type        = number
  default     = 1
}
variable "storage_size" {
  type    = string
  default = "100Gi"
}
variable "storage_class" {
  type    = string
  default = "gp3"
}
variable "cpu" {
  type    = string
  default = "1"
}
variable "memory" {
  type    = string
  default = "4Gi"
}
