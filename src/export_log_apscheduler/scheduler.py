import logging
from apscheduler.schedulers.background import BackgroundScheduler
from src.database import SessionLocal
from src.export_log_apscheduler.service import export_and_clean_logs

logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler()


def run_scheduled_export_job():
    """Periodically check database size and run log export/cleanup if threshold reached."""
    logger.info("Executing scheduled DB size check & export job...")
    db = SessionLocal()
    try:
        result = export_and_clean_logs(db, force=False)
        logger.info("Scheduled DB export job finished: %s", result)
    except Exception as exc:
        logger.exception("Error executing DB export job: %s", exc)
    finally:
        db.close()


def start_scheduler():
    """Initialize and start the background APScheduler."""
    if not scheduler.running:
        # Run every 6 hours
        scheduler.add_job(
            run_scheduled_export_job,
            "interval",
            hours=6,
            id="db_log_export_job",
            replace_existing=True,
        )
        scheduler.start()
        logger.info("APScheduler started: Monitoring DB size every 6 hours.")


def shutdown_scheduler():
    """Shutdown APScheduler background process."""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("APScheduler shutdown completed.")
