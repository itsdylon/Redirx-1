"""
Trial expiry cron job.

Run via Render Cron Job:
  Schedule: 0 3 * * * (3 AM UTC daily)
  Command:  python -m backend.services.trial_cron
"""
import sys
import os
import logging

# Setup path
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
SRC_DIR = os.path.join(BASE_DIR, "src")
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, SRC_DIR)

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)


def main():
    from backend.services.trial_service import TrialService

    logger.info("Starting trial expiry cron job")
    service = TrialService()
    count = service.expire_trials()
    logger.info(f"Trial expiry complete: {count} user(s) downgraded")


if __name__ == '__main__':
    main()
