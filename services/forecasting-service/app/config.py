from shared.config import settings

SERVICE_NAME = "forecasting-service"
PORT = 8003
CYCLE_BUDGET_SECONDS = 60  # PRD: forecasting cycle < 60 seconds per service
__all__ = ["settings", "SERVICE_NAME", "PORT", "CYCLE_BUDGET_SECONDS"]
