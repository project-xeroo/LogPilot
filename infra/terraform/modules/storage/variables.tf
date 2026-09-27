variable "name" { type = string }
variable "bucket_name" { type = string }
variable "retention_days" {
  type    = number
  default = 365
}
variable "force_destroy" {
  type    = bool
  default = false
}
variable "tags" {
  type    = map(string)
  default = {}
}
