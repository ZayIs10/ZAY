"""
Gen Z Automation - Scheduler
Runs the full research → publish pipeline 3 times a day.

Schedule (configurable in research_config.json → "schedule"):
  - 08:00  Research finds 1 topic → writes to Sheet
  - 08:30  Publisher picks it up → generates image → posts to Instagram

  - 13:00  Research finds 1 topic → writes to Sheet
  - 13:30  Publisher picks it up → generates image → posts to Instagram

  - 18:00  Research finds 1 topic → writes to Sheet
  - 18:30  Publisher picks it up → generates image → posts to Instagram

Usage:
  python scheduler.py

Keep this terminal open, or run via Windows Task Scheduler.
Press Ctrl+C to stop.
"""

import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime

import schedule

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
RESEARCH_DIR  = os.path.join(BASE_DIR, "..", "research")
PYTHON        = sys.executable
CONFIG_PATH   = os.path.join(BASE_DIR, "..", "research", "research_config.json")

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

os.makedirs(os.path.join(BASE_DIR, "..", "logs"), exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [SCHEDULER] %(message)s",
    handlers=[
        logging.FileHandler(
            os.path.join(BASE_DIR, "..", "logs", "scheduler_log.txt"),
            encoding="utf-8"
        ),
        logging.StreamHandler(sys.stdout),
    ],
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_schedule_config() -> dict:
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f).get("schedule", {})
    except Exception as e:
        logging.warning(f"Could not load schedule config: {e}. Using defaults.")
        return {}


def run_script(script_path: str, cwd: str, extra_args: list = None) -> int:
    """Run a Python script and return exit code."""
    cmd = [PYTHON, script_path] + (extra_args or [])
    logging.info(f"Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=cwd)
    if result.returncode == 0:
        logging.info(f"  ✓ Completed successfully.")
    else:
        logging.error(f"  ✗ Exited with code {result.returncode}.")
    return result.returncode


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------

def run_research_one_topic():
    """Research phase: find 1 fresh topic and write to Google Sheets."""
    logging.info("=== RESEARCH JOB starting ===")
    script = os.path.join(RESEARCH_DIR, "research.py")
    code = run_script(script, cwd=RESEARCH_DIR, extra_args=["--count", "1"])
    if code != 0:
        logging.error("Research job failed. Publisher will still run and pick up any existing Ready rows.")


def run_publisher():
    """Publisher phase: pick up next Ready row from Sheet and post to Instagram."""
    logging.info("=== PUBLISHER JOB starting ===")
    script = os.path.join(BASE_DIR, "post_generator.py")
    code = run_script(script, cwd=BASE_DIR)
    if code != 0:
        logging.error("Publisher job failed. Check logs for details.")


# ---------------------------------------------------------------------------
# Schedule setup
# ---------------------------------------------------------------------------

def setup_schedule(cfg: dict):
    """
    Three research+publish cycles per day.
    Times are read from config, with defaults: 08:00/13:00/18:00 for research,
    publisher runs 30 min after each.
    """
    # Load configurable times (or use defaults)
    post_times = cfg.get("post_times", ["08:00", "13:00", "18:00"])

    # Calculate publisher times = research time + 30 minutes
    def add_30(t: str) -> str:
        h, m = map(int, t.split(":"))
        m += 30
        h += m // 60
        m = m % 60
        h = h % 24
        return f"{h:02d}:{m:02d}"

    for research_time in post_times:
        publish_time = add_30(research_time)
        schedule.every().day.at(research_time).do(run_research_one_topic)
        schedule.every().day.at(publish_time).do(run_publisher)
        logging.info(f"  Cycle: Research {research_time} → Publish {publish_time}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    logging.info("=== Gen Z Automation Scheduler starting ===")
    logging.info(f"Python: {PYTHON}")
    logging.info(f"Base dir: {BASE_DIR}")

    cfg = load_schedule_config()
    setup_schedule(cfg)

    # Show full schedule
    logging.info("--- Scheduled jobs ---")
    for job in schedule.jobs:
        logging.info(f"  {job}")

    next_run = schedule.next_run()
    logging.info(f"Next job runs at: {next_run}")
    logging.info("Scheduler running. Press Ctrl+C to stop.")

    try:
        while True:
            schedule.run_pending()
            time.sleep(30)  # check every 30 seconds
    except KeyboardInterrupt:
        logging.info("Scheduler stopped by user.")


if __name__ == "__main__":
    main()
