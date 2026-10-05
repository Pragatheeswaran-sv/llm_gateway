from fastapi import APIRouter, BackgroundTasks, Depends, status
from sqlalchemy.orm import Session

from src.config import settings
from src.database import SessionLocal, get_db
from src.export_log_apscheduler.service import export_and_clean_logs

router = APIRouter(prefix="/api/v1/export-logs", tags=["Database Export & Archival"])


def _run_export_in_background():
    """Opens its own DB session so it can run safely in a background task."""
    db = SessionLocal()
    try:
        result = export_and_clean_logs(db)
        import logging
        logging.getLogger(__name__).info("Background export finished: %s", result)
    except Exception as exc:
        import logging
        logging.getLogger(__name__).exception("Background export failed: %s", exc)
    finally:
        db.close()


@router.get("/status")
def get_export_status():
    """Return the current export configuration."""
    return {
        "message": "Export configuration retrieved",
        "status_code": status.HTTP_200_OK,
        "data": {
            "export_interval_hours": settings.EXPORT_INTERVAL_HOURS,
            "recipient_email": settings.ALERT_EMAIL_RECIPIENT,
            "smtp_host": settings.SMTP_HOST,
            "smtp_port": settings.SMTP_PORT,
            "smtp_user": settings.SMTP_USER,
        },
    }


@router.post("/trigger", status_code=status.HTTP_202_ACCEPTED)
def trigger_log_export(background_tasks: BackgroundTasks):
    """
    Manually trigger an immediate log export for the last N hours.
    Returns 202 immediately; export runs in the background.
    Check server logs for the result.
    """
    background_tasks.add_task(_run_export_in_background)
    return {
        "message": "Log export triggered successfully. Running in background.",
        "status_code": status.HTTP_202_ACCEPTED,
        "data": {
            "export_interval_hours": settings.EXPORT_INTERVAL_HOURS,
            "recipient_email": settings.ALERT_EMAIL_RECIPIENT,
        },
    }
