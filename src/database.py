"""
Persistent model database stored as data/models_db.json.

Each run merges freshly-scraped records INTO the DB rather than replacing it,
so models that disappear from a provider page (e.g. Bedrock moving a model
between lifecycle tables) are never silently lost.

DB structure  (key = "{provider}|{model}"):
{
  "AWS Bedrock|amazon.nova-lite-v1:0": {
    "provider":        "AWS Bedrock",
    "model":           "amazon.nova-lite-v1:0",
    "shutdown_date":   "12/4/2025",
    "lifecycle_stage": "Active",
    "source_url":      "https://...",
    "first_seen":      "2026-03-30",
    "last_seen":       "2026-03-30",
    // optional model-card extras (Bedrock only):
    "context_window":     300000,
    "max_output_tokens":  5000,
    "input_modalities":   ["Text", "Image", "Video"],
    "output_modalities":  ["Text"],
    "knowledge_cutoff":   "October 2024",
    "geo_inference_ids":  ["us.amazon.nova-lite-v1:0", "eu.amazon.nova-lite-v1:0"],
    "model_card_url":     "https://..."
  }
}
"""

import json
from datetime import datetime, timedelta
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / 'data' / 'models_db.json'

# Fields updated from the lifecycle scrape every run
_LIFECYCLE_FIELDS = {'shutdown_date', 'lifecycle_stage', 'source_url'}

# Fields updated from model-card scrape (only written when present, never cleared)
_CARD_FIELDS = {
    'context_window', 'max_output_tokens',
    'input_modalities', 'output_modalities',
    'knowledge_cutoff', 'geo_inference_ids', 'model_card_url',
}


def load_db() -> dict:
    if not DB_PATH.exists():
        return {}
    with open(DB_PATH, encoding='utf-8') as f:
        return json.load(f)


def save_db(db: dict) -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(DB_PATH, 'w', encoding='utf-8') as f:
        json.dump(db, f, indent=2, ensure_ascii=False)


def merge_scraped(db: dict, scraped_records: list) -> dict:
    """
    Merge a fresh list of scraped records into the DB.

    - Existing record: update lifecycle fields + last_seen; preserve first_seen
      and any model-card metadata already stored.
    - New record: add with first_seen = last_seen = today.
    - Records absent from this scrape: untouched (last_seen stays old).
    """
    today = datetime.now().strftime('%Y-%m-%d')
    for record in scraped_records:
        key = f"{record['provider']}|{record['model']}"
        if key in db:
            for field in _LIFECYCLE_FIELDS:
                if field in record:
                    db[key][field] = record[field]
            db[key]['last_seen'] = today
        else:
            db[key] = {**record, 'first_seen': today, 'last_seen': today}
    return db


def merge_card_metadata(db: dict, card_records: list) -> dict:
    """
    Merge Bedrock model-card metadata into existing DB entries.
    Only updates card fields; never touches lifecycle fields.
    If the model ID isn't in the DB yet, adds a skeleton entry.
    """
    today = datetime.now().strftime('%Y-%m-%d')
    for record in card_records:
        key = f"AWS Bedrock|{record['model_id']}"
        if key not in db:
            db[key] = {
                'provider': 'AWS Bedrock',
                'model': record['model_id'],
                'shutdown_date': '',
                'source_url': record.get('model_card_url', ''),
                'first_seen': today,
                'last_seen': today,
            }
        for field in _CARD_FIELDS:
            if field in record and record[field]:
                db[key][field] = record[field]
    return db


def cleanup_expired(db: dict, days_threshold: int = 365) -> tuple:
    """
    Remove records whose shutdown date expired more than `days_threshold` days ago.
    Default threshold is 1 year — keeps the DB and the All Models sheet from
    accumulating obsolete entries indefinitely.

    Returns (updated_db, number_of_removed_records).
    """
    from utils import parse_shutdown_date
    cutoff = datetime.now() - timedelta(days=days_threshold)
    to_remove = []
    for key, record in db.items():
        date_str = record.get('shutdown_date', '')
        if not date_str:
            continue
        parsed = parse_shutdown_date(date_str)
        if parsed and parsed.replace(tzinfo=None) < cutoff:
            to_remove.append(key)
    for key in to_remove:
        del db[key]
    return db, len(to_remove)


def get_all_records(db: dict) -> list:
    """Return all DB records as a flat list, sorted by provider then model."""
    return sorted(db.values(), key=lambda r: (r.get('provider', ''), r.get('model', '')))
