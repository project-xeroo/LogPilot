output "bucket" { value = aws_s3_bucket.logs.bucket }
output "kms_key_arn" { value = aws_kms_key.logs.arn }
output "workload_policy_arn" { value = aws_iam_policy.workload.arn }
