from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session
from src.database import get_db
from src.config import settings
from src.export_log_apscheduler.service import export_and_clean_logs, get_database_size_mb

router = APIRouter(prefix="/api/v1/export-logs", tags=["Database Export & Archival"])


@router.get("/status")
def get_db_storage_status(db: Session = Depends(get_db)):
    """Return current database disk size in MB and cleanup threshold."""
    size_mb = get_database_size_mb(db)
    return {
        "message": "Database storage status retrieved",
        "status_code": status.HTTP_200_OK,
        "data": {
            "current_database_size_mb": size_mb,
            "cleanup_threshold_mb": settings.DB_CLEANUP_THRESHOLD_MB,
            "recipient_email": settings.ALERT_EMAIL_RECIPIENT,
            "log_retention_days": settings.LOG_RETENTION_DAYS,
        },
    }


@router.post("/trigger")
def trigger_log_export(force: bool = False, db: Session = Depends(get_db)):
    """Manually trigger DB size check, log export, email attachment, and cleanup."""
    result = export_and_clean_logs(db, force=force)
    return {
        "message": "Log export process executed",
        "status_code": status.HTTP_200_OK,
        "data": result,
    }
