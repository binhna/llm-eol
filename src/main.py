from parsers import parse_all_deprecations
from parsers.bedrock_model_cards import scrape_bedrock_model_cards
from checker import check_my_models
from scanner import scan_projects, models_to_track
from sheets import export_to_google_sheets
from database import load_db, save_db, merge_scraped, merge_card_metadata, cleanup_expired, get_all_records

# ── Google Sheet to write results into ───────────────────────────────────────
# Open the sheet in your browser and copy the ID from the URL:
#   https://docs.google.com/spreadsheets/d/<SPREADSHEET_ID>/edit
SPREADSHEET_ID = '1zkXpUiVxZcZ9rmCD-Qc6C6oFky966vNMGvK0S1cYy6c'

# ── Extra models to track ────────────────────────────────────────────────────
# The list of models to track is discovered automatically by scanning our
# product repos — see PROJECTS in src/scanner.py to add or change a repo.
# Only add a model here if it is NOT declared in any of those projects, e.g.
# one used by a service outside them. Supports:
#   - Direct model IDs (e.g. "gpt-4o-mini", "anthropic.claude-3-haiku-20240307-v1:0")
#   - Bedrock cross-region prefixes (e.g. "us.meta.llama3-3-70b-instruct-v1:0")
#   - Version-appended IDs (e.g. "claude-3-haiku@20240307")
EXTRA_MODELS = []

if __name__ == "__main__":
    # 1. Work out which models we need to track by scanning our product repos:
    #    read each project's model config, then search that codebase to see
    #    which of those models are actually referenced.
    scan = scan_projects()
    my_models = models_to_track(scan, EXTRA_MODELS)

    # 2. Scrape all provider deprecation pages
    scraped = parse_all_deprecations()

    # 3. Scrape Bedrock model card metadata (context window, modalities, etc.)
    card_metadata = scrape_bedrock_model_cards()

    # 4. Merge everything into the persistent DB and prune old entries
    db = load_db()
    db = merge_scraped(db, scraped)
    db = merge_card_metadata(db, card_metadata)
    db, removed = cleanup_expired(db, days_threshold=365)
    if removed:
        print(f"  Pruned {removed} record(s) expired more than 1 year ago")
    save_db(db)
    all_records = get_all_records(db)

    # 5. Match the models we found against the scraped end-of-life data.
    #    The provider each project declares decides which platform's date wins
    #    when a model appears on more than one (e.g. Claude on Vertex vs direct).
    model_providers = {
        model: (entry.get('providers') or [''])[0]
        for model, entry in scan['models'].items()
    }
    matches, unmatched = check_my_models(my_models, all_records, model_providers)

    # 6. Export to Google Sheets (All Models always reflects full DB)
    export_to_google_sheets(all_records, matches, unmatched, SPREADSHEET_ID, scan)
