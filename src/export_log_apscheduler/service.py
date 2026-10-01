import csv
import gzip
import io
import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from src.config import settings
from src.conversation.models import LLMRequestLog
from src.database import engine
from src.export_log_apscheduler.email import send_email_with_attachment

logger = logging.getLogger(__name__)


def        get_database_size_mb(db: Session) -> float:
    """Return the total PostgreSQL database size in MB."""
    try:
        result = db.execute(text("SELECT pg_database_size(current_database())")).scalar()
        if result is None:
            return 0.0
        return round(float(result) / (1024 * 1024), 2)
    except Exception as exc:
        logger.warning("Failed to check database size: %s", exc)
        return 0.0


def export_and_clean_logs(db: Session, force: bool = False) -> dict:
    """Export old logs to a compressed CSV if DB size exceeds threshold, email to recipient, and delete."""
    current_size_mb = get_database_size_mb(db)
    logger.info(
        "Checking DB Storage Size: %.2f MB (Threshold: %.2f MB)",
        current_size_mb,
        settings.DB_CLEANUP_THRESHOLD_MB,
    )

    if not force and current_size_mb < settings.DB_CLEANUP_THRESHOLD_MB:
        return {
            "action": "skipped",
            "reason": (
                f"Database size ({current_size_mb:.2f} MB) is below "
                f"threshold ({settings.DB_CLEANUP_THRESHOLD_MB:.2f} MB)"
            ),
            "current_size_mb": current_size_mb,
        }

    cutoff_date = datetime.now(timezone.utc) - timedelta(days=settings.LOG_RETENTION_DAYS)
    logs_to_export = (
        db.query(LLMRequestLog)
        .filter(LLMRequestLog.created_at < cutoff_date)
        .order_by(LLMRequestLog.created_at.asc())
        .all()
    )

    if not logs_to_export:
        logger.info(
            "No logs older than %d days found to archive.",
            settings.LOG_RETENTION_DAYS,
        )
        return {
            "action": "skipped",
            "reason": f"No request logs older than {settings.LOG_RETENTION_DAYS} days found to archive.",
            "current_size_mb": current_size_mb,
        }

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "id",
        "request_id",
        "provider",
        "model_name",
        "status",
        "http_status_code",
        "total_attempts",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "duration_ms",
        "user_prompt",
        "dbml_query",
        "summary",
        "is_fallback_mode",
        "failed_attempts",
        "started_at",
        "completed_at",
        "created_at",
    ])

    log_ids = []
    for log in logs_to_export:
        log_ids.append(log.id)
        writer.writerow([
            str(log.id),
            str(log.request_id) if log.request_id else "",
            log.provider or "",
            log.model_name or "",
            log.status or "",
            log.http_status_code or "",
            log.total_attempts or 1,
            log.prompt_tokens or "",
            log.completion_tokens or "",
            log.total_tokens or "",
            log.duration_ms or "",
            log.user_prompt or "",
            log.dbml_query or "",
            log.summary or "",
            log.is_fallback_mode,
            json.dumps(log.failed_attempts) if log.failed_attempts else "",
            log.started_at.isoformat() if log.started_at else "",
            log.completed_at.isoformat() if log.completed_at else "",
            log.created_at.isoformat() if log.created_at else "",
        ])

    csv_data = output.getvalue().encode("utf-8")
    compressed_data = gzip.compress(csv_data)
    filename = (
        f"llm_request_logs_archive_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.csv.gz"
    )

    subject = f"[Database Alert] LLM Gateway Storage Export ({len(logs_to_export)} logs archived)"
    body = (
        f"LLM Gateway Storage Cleanup Report\n"
        f"-------------------------------------\n"
        f"Recipient: {settings.ALERT_EMAIL_RECIPIENT}\n"
        f"Database Size Before Cleanup: {current_size_mb:.2f} MB\n"
        f"Archived Rows Count: {len(logs_to_export)}\n"
        f"Log Retention Period: {settings.LOG_RETENTION_DAYS} days\n"
        f"Exported File: {filename}\n\n"
        f"The exported log archive is attached as a compressed .csv.gz file.\n"
    )

    send_email_with_attachment(
        recipient=settings.ALERT_EMAIL_RECIPIENT,
        subject=subject,
        body=body,
        attachment_bytes=compressed_data,
        filename=filename,
    )

    # Delete exported rows
    db.query(LLMRequestLog).filter(LLMRequestLog.id.in_(log_ids)).delete(synchronize_session=False)
    db.commit()

    # Reclaim disk space
    try:
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.execute(text("VACUUM ANALYZE llm_request_logs;"))
    except Exception as vacuum_err:
        logger.warning("Vacuum failed: %s", vacuum_err)

    new_size_mb = get_database_size_mb(db)
    logger.info("Cleanup completed. New DB Size: %.2f MB", new_size_mb)

    return {
        "action": "completed",
        "archived_rows": len(logs_to_export),
        "previous_size_mb": current_size_mb,
        "new_size_mb": new_size_mb,
    }
