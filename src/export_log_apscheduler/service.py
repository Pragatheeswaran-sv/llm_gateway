import io
import logging
from datetime import datetime, timedelta, timezone

import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session

from src.config import settings
from src.conversation.models import LLMRequestLog
from src.database import engine
from src.export_log_apscheduler.email import send_email_with_attachment

logger = logging.getLogger(__name__)


def export_and_clean_logs(db: Session) -> dict:
    """
    Export the last EXPORT_INTERVAL_HOURS of logs to CSV, email it, then delete
    the exported rows using a temp-table JOIN for safety.

    Flow:
      1. Query success/failed logs from the last 6 hours → DataFrame
      2. Export DataFrame to CSV in-memory
      3. Send email with CSV as attachment
      4. Create a temp table (log_ids only) from the DataFrame
      5. DELETE logs by JOIN on log_id between actual table and temp table
    """
    since = datetime.now(timezone.utc) - timedelta(hours=settings.EXPORT_INTERVAL_HOURS)

    logger.info("Fetching logs created after %s (last %d hours).", since.isoformat(), settings.EXPORT_INTERVAL_HOURS)

    logs = (
        db.query(LLMRequestLog)
        .filter(LLMRequestLog.created_at >= since)
        .order_by(LLMRequestLog.created_at.asc())
        .all()
    )

    if not logs:
        logger.info("No logs found in the last %d hours. Skipping export.", settings.EXPORT_INTERVAL_HOURS)
        return {
            "action": "skipped",
            "reason": f"No logs found in the last {settings.EXPORT_INTERVAL_HOURS} hours.",
            "exported_rows": 0,
        }

    # ── Step 1: Build DataFrame ──────────────────────────────────────────────
    records = []
    for log in logs:
        records.append({
            "log_id": str(log.id),
            "request_id": str(log.request_id) if log.request_id else "",
            "provider": log.provider or "",
            "model_name": log.model_name or "",
            "status": log.status or "",
            "http_status_code": log.http_status_code or "",
            "total_attempts": log.total_attempts or 1,
            "prompt_tokens": log.prompt_tokens or "",
            "completion_tokens": log.completion_tokens or "",
            "total_tokens": log.total_tokens or "",
            "duration_ms": log.duration_ms or "",
            "user_prompt": log.user_prompt or "",
            "dbml_query": log.dbml_query or "",
            "summary": log.summary or "",
            "is_fallback_mode": log.is_fallback_mode,
            "failed_attempts": str(log.failed_attempts) if log.failed_attempts else "",
            "started_at": log.started_at.isoformat() if log.started_at else "",
            "completed_at": log.completed_at.isoformat() if log.completed_at else "",
            "created_at": log.created_at.isoformat() if log.created_at else "",
        })

    df = pd.DataFrame(records)
    logger.info("Built DataFrame with %d rows.", len(df))

    # ── Step 2: Export DataFrame → CSV (in-memory) ──────────────────────────
    csv_buffer = io.StringIO()
    df.to_csv(csv_buffer, index=False)
    csv_bytes = csv_buffer.getvalue().encode("utf-8")

    filename = (
        f"llm_request_logs_{since.strftime('%Y%m%d_%H%M%S')}"
        f"_to_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.csv"
    )

    # ── Step 3: Send email with CSV as attachment ────────────────────────────
    subject = f"[LLM Gateway] Log Export – {len(df)} records ({since.strftime('%Y-%m-%d %H:%M')} UTC)"
    body = (
        f"LLM Gateway – Scheduled Log Export\n"
        f"-------------------------------------\n"
        f"Export Window : Last {settings.EXPORT_INTERVAL_HOURS} hours\n"
        f"From          : {since.strftime('%Y-%m-%d %H:%M:%S')} UTC\n"
        f"To            : {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC\n"
        f"Exported Rows : {len(df)}\n"
        f"File          : {filename}\n\n"
        f"The exported log file is attached as a CSV."
    )

    email_sent = send_email_with_attachment(
        recipient=settings.ALERT_EMAIL_RECIPIENT,
        subject=subject,
        body=body,
        attachment_bytes=csv_bytes,
        filename=filename,
    )

    if not email_sent:
        logger.error("Email sending failed. Skipping delete to prevent data loss.")
        return {
            "action": "failed",
            "reason": "Email could not be sent. Rows were NOT deleted.",
            "exported_rows": len(df),
        }

    logger.info("Email sent successfully. Proceeding to delete exported rows.")

    # ── Step 4: Create temp table with log_ids from DataFrame ────────────────
    log_ids = df["log_id"].tolist()

    try:
        with engine.connect() as conn:
            with conn.begin():
                # Create temp table for this session
                conn.execute(text("""
                    CREATE TEMP TABLE IF NOT EXISTS temp_export_log_ids (
                        log_id UUID PRIMARY KEY
                    ) ON COMMIT DROP
                """))

                # Bulk-insert the log_ids into the temp table
                conn.execute(
                    text("INSERT INTO temp_export_log_ids (log_id) VALUES (:log_id)"),
                    [{"log_id": lid} for lid in log_ids],
                )

                # ── Step 5: DELETE using JOIN with temp table ────────────────
                delete_result = conn.execute(text("""
                    DELETE FROM llm_request_logs l
                    USING temp_export_log_ids t
                    WHERE l.id = t.log_id::uuid
                """))

                deleted_count = delete_result.rowcount
                logger.info("Deleted %d rows via temp-table JOIN.", deleted_count)

        # Reclaim disk space (runs outside the transaction)
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.execute(text("VACUUM ANALYZE llm_request_logs;"))
            logger.info("VACUUM ANALYZE completed.")

    except Exception as exc:
        logger.exception("Error during delete/vacuum step: %s", exc)
        return {
            "action": "partial",
            "reason": f"Email sent but delete failed: {exc}",
            "exported_rows": len(df),
            "deleted_rows": 0,
        }

    return {
        "action": "completed",
        "export_window_hours": settings.EXPORT_INTERVAL_HOURS,
        "since": since.isoformat(),
        "exported_rows": len(df),
        "deleted_rows": deleted_count,
        "file": filename,
    }
