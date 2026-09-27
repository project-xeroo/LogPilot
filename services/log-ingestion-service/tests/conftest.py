import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
for p in (ROOT, ROOT / "services" / "log-ingestion-service"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))
