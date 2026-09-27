# Managed cloud PostgreSQL (PRD 7.1). Encrypted at rest (KMS), TLS enforced, multi-AZ in production, automated backups.
#
# TIME-SERIES EXTENSION: the application uses TimescaleDB when the `timescaledb` extension is available (hypertable
# partitioning of log_records). Amazon RDS does not ship that extension - for hypertables use Timescale Cloud (its
# Terraform provider is `timescale/timescale`) or self-managed Timescale on EC2 and pass its endpoint through
# DATABASE_URL. On plain PostgreSQL (this module) LogPilot falls back to regular indexed tables, which is fine for the
# 1M+ record requirement; partitioning becomes worthwhile beyond that.
terraform {
  required_version = ">= 1.6"
  required_providers {
    aws    = { source = "hashicorp/aws", version = "~> 5.60" }
    random = { source = "hashicorp/random", version = "~> 3.6" }
  }
}

resource "random_password" "master" {
  length  = 32
  special = false
}

resource "aws_kms_key" "db" {
  description             = "${var.name} database encryption"
  enable_key_rotation     = true
  deletion_window_in_days = 14
  tags                    = var.tags
}

resource "aws_security_group" "db" {
  name_prefix = "${var.name}-db-"
  vpc_id      = var.vpc_id
  description = "PostgreSQL from workloads only"
  ingress {
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = var.allowed_security_group_ids
  }
  tags = var.tags
  lifecycle { create_before_destroy = true }
}

resource "aws_db_parameter_group" "pg" {
  name_prefix = "${var.name}-pg15-"
  family      = "postgres15"
  parameter {
    name  = "rds.force_ssl"
    value = "1"
  }
  parameter {
    name  = "log_min_duration_statement"
    value = "1000"
  }
  parameter {
    name         = "shared_preload_libraries"
    value        = "pg_stat_statements"
    apply_method = "pending-reboot"
  }
  tags = var.tags
}

resource "aws_db_instance" "this" {
  identifier             = "${var.name}-postgres"
  engine                 = "postgres"
  engine_version         = var.engine_version
  instance_class         = var.instance_class
  allocated_storage      = var.storage_gb
  max_allocated_storage  = var.max_storage_gb # storage autoscaling
  storage_type           = "gp3"
  storage_encrypted      = true
  kms_key_id             = aws_kms_key.db.arn
  db_name                = "logpilot"
  username               = "logpilot"
  password               = random_password.master.result
  db_subnet_group_name   = var.db_subnet_group_name
  vpc_security_group_ids = [aws_security_group.db.id]
  parameter_group_name   = aws_db_parameter_group.pg.name

  multi_az                     = var.multi_az # multi-AZ failover
  backup_retention_period      = var.backup_retention_days
  backup_window                = "02:00-03:00"
  maintenance_window           = "sun:03:30-sun:04:30"
  copy_tags_to_snapshot        = true
  deletion_protection          = var.deletion_protection
  skip_final_snapshot          = !var.deletion_protection
  final_snapshot_identifier    = var.deletion_protection ? "${var.name}-final" : null
  performance_insights_enabled = true
  monitoring_interval          = 60
  monitoring_role_arn          = aws_iam_role.monitoring.arn
  auto_minor_version_upgrade   = true
  tags                         = var.tags
}

resource "aws_iam_role" "monitoring" {
  name_prefix = "${var.name}-rds-mon-"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = "sts:AssumeRole", Principal = { Service = "monitoring.rds.amazonaws.com" } }]
  })
}
resource "aws_iam_role_policy_attachment" "monitoring" {
  role       = aws_iam_role.monitoring.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonRDSEnhancedMonitoringRole"
}

resource "aws_secretsmanager_secret" "url" {
  name_prefix = "${var.name}/database-url-"
  kms_key_id  = aws_kms_key.db.arn
  tags        = var.tags
}
resource "aws_secretsmanager_secret_version" "url" {
  secret_id     = aws_secretsmanager_secret.url.id
  secret_string = "postgresql+psycopg2://logpilot:${random_password.master.result}@${aws_db_instance.this.address}:5432/logpilot?sslmode=require"
}
