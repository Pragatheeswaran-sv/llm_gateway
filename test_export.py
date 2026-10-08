import sys
import logging

# Write to a log file since stdout seems to be swallowed
log_file = open("export_test_output.log", "w")

def log(msg):
    log_file.write(msg + "\n")
    log_file.flush()
    print(msg, flush=True)

log("Step 1: Starting test...")

try:
    sys.path.insert(0, '.')
    log("Step 2: Importing config...")
    from src.config import settings
    log(f"  SMTP_USER={settings.SMTP_USER}")
    log(f"  ALERT_EMAIL_RECIPIENT={settings.ALERT_EMAIL_RECIPIENT}")

    log("Step 3: Importing database...")
    from src.database import SessionLocal

    log("Step 4: Importing service...")
    from src.export_log_apscheduler.service import export_and_clean_logs

    log("Step 5: Running export_and_clean_logs...")
    db = SessionLocal()
    try:
        result = export_and_clean_logs(db)
        log(f"RESULT: {result}")
    except Exception as e:
        import traceback
        log(f"ERROR in export: {e}")
        log(traceback.format_exc())
    finally:
        db.close()

except Exception as e:
    import traceback
    log(f"IMPORT ERROR: {e}")
    log(traceback.format_exc())

log("Done.")
log_file.close()
