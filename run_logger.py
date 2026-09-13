"""
Durable per-run logging (DESIGN.md §7). Writes one JSON line per run today;
swap in the Notion "Automation Runs" database call below once a destination
page/database is chosen (the Notion connector is already available to this
account, so this is a small follow-up, not a new integration).
"""
import datetime
import json

import config


def log_run(stats: dict):
    entry = {"timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(), **stats}
    with open(config.RUN_LOG_PATH, "a") as f:
        f.write(json.dumps(entry) + "\n")

    # --- Notion upgrade path (disabled until a database is picked) -------
    # from notion_client import Client  # or the account's Notion MCP tool
    # notion.pages.create(parent={"database_id": AUTOMATION_RUNS_DB_ID}, properties={
    #     "Timestamp": {"date": {"start": entry["timestamp"]}},
    #     "Records processed": {"number": stats["records_processed"]},
    #     "LinkedIn written": {"number": stats["linkedin_written"]},
    #     "Locations written": {"number": stats["locations_written"]},
    #     "Flagged for review": {"number": stats["flagged_for_review"]},
    #     "Errors": {"rich_text": [{"text": {"content": "; ".join(stats["errors"])[:2000]}}]},
    # })
