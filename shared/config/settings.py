"""Central configuration for every LogPilot service (12-factor: all from env)."""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    # ---- runtime -----------------------------------------------------------
    environment: str = "dev"  # dev | staging | prod
    log_level: str = "INFO"
    seed_demo: bool = True
    cors_origins: str = "http://localhost:5173,http://localhost:3000"

    # ---- managed PostgreSQL + time-series extension ------------------------
    database_url: str = "postgresql+psycopg2://postgres:password@localhost:5432/logpilot"
    db_pool_size: int = 10
    db_max_overflow: int = 20

    # ---- managed Redis: broker + result backend + event bus ----------------
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"

    # ---- S3-compatible object storage --------------------------------------
    s3_endpoint_url: str | None = "http://localhost:9000"
    s3_access_key: str = "logpilot"
    s3_secret_key: str = "logpilot-secret"
    s3_bucket: str = "logpilot-logs"
    s3_region: str = "us-east-1"

    # ---- managed vector store ----------------------------------------------
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str | None = None
    embedding_dim: int = 256

    # ---- auth --------------------------------------------------------------
    jwt_secret: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60  # "JWT-based authentication with configurable expiry"
    internal_api_token: str = "internal-change-me"  # service-to-service

    # ---- cloud AI provider (OpenAI-compatible REST) ------------------------
    # ai_provider: "mock" (offline deterministic, no API key) | "openai_compatible" | "ibm_bob"
    ai_provider: str = "mock"
    # Override just for embeddings (e.g. a provider has great chat models but no properly-tuned
    # retrieval embedding model provisioned). Empty = same as ai_provider.
    ai_embedding_provider: str = ""
    ai_base_url: str = ""
    ai_api_key: str = ""
    ai_fast_model: str = "fast"
    ai_deep_model: str = "deep"
    ai_embedding_model: str = "embed"
    ai_timeout_seconds: float = 60.0
    # "Outbound network calls restricted to an explicit allowlist of approved AI provider endpoints"
    ai_egress_allowlist: str = ""  # comma separated hostnames; empty => only the host of ai_base_url

    # ---- internal service URLs --------------------------------------------
    ai_service_url: str = "http://localhost:8002"
    ingestion_service_url: str = "http://localhost:8001"
    forecasting_service_url: str = "http://localhost:8003"
    notification_service_url: str = "http://localhost:8004"
    audit_service_url: str = "http://localhost:8005"
    processing_service_url: str = "http://localhost:8006"

    # ---- ingestion ---------------------------------------------------------
    max_upload_bytes: int = 500 * 1024 * 1024  # 500MB
    max_decompressed_bytes: int = 5 * 1024 * 1024 * 1024
    max_zip_entries: int = 1000
    ingest_batch_size: int = 2000

    # ---- forecasting defaults (PRD 4.2 / 4.4) ------------------------------
    forecast_interval_seconds: int = 60
    forecast_warning_threshold: int = 60
    forecast_critical_threshold: int = 80
    forecast_window_seconds: int = 60
    forecast_lookback_minutes: int = 60
    weight_velocity: float = 0.30
    weight_similarity: float = 0.40
    weight_baseline: float = 0.30
    # Deliberately smaller than any service's actual scoring interval (floored at 5s - see
    # forecasting-service's cycle.py/scheduler.py), not equal to it: when the tick-scan period exactly
    # equals a service's interval, the reschedule timestamp (set slightly *after* the tick that claimed
    # it, from ordinary processing delay) lands just past the next tick's exact mark and gets picked up
    # only every OTHER tick - silently doubling the real cadence. A faster, independent poll avoids that.
    scheduler_tick_seconds: int = 2
    # "wall": 'now' is real time (production). "data": 'now' is the newest ingested record, so
    # historical/replayed logs can be forecast as if live (demos, backtests).
    forecast_clock: str = "wall"

    # ---- processing pipeline ----------------------------------------------
    dedup_threshold: float = 0.90  # cosine similarity above which two error templates are "the same error"
    cluster_eps: float = 0.30  # DBSCAN cosine-distance radius
    cluster_min_samples: int = 2
    cluster_match_threshold: float = 0.80  # centroid similarity for carrying cluster identity across runs
    anomaly_recent_minutes: int = 60  # anomalies newer than this surface in the feed / notifications

    # ---- notifications -----------------------------------------------------
    webhook_allow_private: bool = False  # dev only: allow webhooks to private IPs
    webhook_timeout_seconds: float = 5.0

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def ai_egress_hosts(self) -> set[str]:
        from urllib.parse import urlparse

        hosts = {h.strip().lower() for h in self.ai_egress_allowlist.split(",") if h.strip()}
        if not hosts and self.ai_base_url:
            host = urlparse(self.ai_base_url).hostname
            if host:
                hosts.add(host.lower())
        return hosts


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
