import logging

from apscheduler.schedulers.background import BackgroundScheduler

from src.config import settings
from src.database import SessionLocal
from src.export_log_apscheduler.service import export_and_clean_logs

logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler()


def run_scheduled_export_job():
    """Export & delete the last N hours of logs on every scheduler tick."""
    logger.info("Running scheduled log export job (last %d hours)...", settings.EXPORT_INTERVAL_HOURS)
    db = SessionLocal()
    try:
        result = export_and_clean_logs(db)
        logger.info("Scheduled export job finished: %s", result)
    except Exception as exc:
        logger.exception("Error in scheduled export job: %s", exc)
    finally:
        db.close()


def start_scheduler():
    """Initialize and start the background APScheduler."""
    try:
        if not scheduler.running:
            scheduler.add_job(
                run_scheduled_export_job,
                "interval",
                hours=settings.EXPORT_INTERVAL_HOURS,
                id="log_export_job",
                replace_existing=True,
            )
            scheduler.start()
            logger.info("APScheduler started: exporting logs every %d hours.", settings.EXPORT_INTERVAL_HOURS)
    except Exception as e:
        logger.error("Failed to start the background scheduler: %s", e)


def shutdown_scheduler():
    """Shutdown the APScheduler background process."""
    try:
        if scheduler.running:
            scheduler.shutdown(wait=False)
            logger.info("APScheduler shutdown completed.")
    except Exception as e:
        logger.error("Failed to cleanly shutdown the background scheduler: %s", e)
