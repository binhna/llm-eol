from parsers import parse_all_deprecations
from parsers.bedrock_model_cards import scrape_bedrock_model_cards
from checker import check_my_models
from sheets import export_to_google_sheets
from database import load_db, save_db, merge_scraped, merge_card_metadata, cleanup_expired, get_all_records

# ── Google Sheet to write results into ───────────────────────────────────────
# Open the sheet in your browser and copy the ID from the URL:
#   https://docs.google.com/spreadsheets/d/<SPREADSHEET_ID>/edit
SPREADSHEET_ID = '1zkXpUiVxZcZ9rmCD-Qc6C6oFky966vNMGvK0S1cYy6c'

# ── Models your application currently uses ───────────────────────────────────
# Add or remove model identifiers here. Supports:
#   - Direct model IDs (e.g. "gpt-4o-mini", "anthropic.claude-3-haiku-20240307-v1:0")
#   - Bedrock cross-region prefixes (e.g. "us.meta.llama3-3-70b-instruct-v1:0")
#   - Version-appended IDs (e.g. "claude-3-haiku@20240307")
# Compiled from a scan of our four products. Trailing comment shows which
# products use each model: BE=bellmere, BU=burley, NO=norval, BO=bordertown.
MY_MODELS = [
    # ── Azure OpenAI ──────────────────────────────────────────────────────────
    "gpt-4o-mini",                 # BE, BU, BO
    "gpt-4o-2024-05-13",           # BE, BU, BO
    "gpt-5-nano",                  # BE, BU, NO, BO
    "gpt-5-mini",                  # BE, BU, NO, BO
    "gpt-5",                       # BE, BU, NO, BO
    "gpt-5.4-nano",                # BE
    "gpt-5.5",                     # NO
    "text-embedding-ada-002",      # BU (embeddings)

    # ── Anthropic Claude (via Vertex AI) ──────────────────────────────────────
    "claude-sonnet-4",             # BE, BO
    "claude-3-7-sonnet",           # BE, BO
    "claude-3-5-haiku",            # BE, BO

    # ── Google Gemini (via Vertex AI) ─────────────────────────────────────────
    "gemini-2.0-flash",            # BE, BO
    "gemini-2.0-flash-lite",       # BE, BO
    "gemini-2.5-flash",            # BE, BO
    "gemini-2.5-flash-lite",       # BE, BU, NO, BO
    "gemini-3.1-flash-lite",       # BE
    "gemini-3.5-flash",            # NO

    # ── Mistral (via Vertex AI) ───────────────────────────────────────────────
    "mistral-large-2411",          # BE, BO
    "mistral-small-2503",          # BE, BO
    "mistral-ocr-2505",            # BE, BO

    # ── AWS Bedrock (cross-region inference IDs) ──────────────────────────────
    "global.anthropic.claude-haiku-4-5-20251001-v1:0",  # BE, NO
    "global.anthropic.claude-sonnet-4-6",               # BE
    "us.meta.llama3-3-70b-instruct-v1:0",               # BE, NO
    "us.meta.llama3-2-90b-instruct-v1:0",               # BE, NO
    "us.mistral.pixtral-large-2502-v1:0",               # BE, NO
    "eu.mistral.pixtral-large-2502-v1:0",               # BE, NO
    "mistral.magistral-small-2509",                     # BE, NO
    "us.deepseek.r1-v1:0",                              # BE, NO
    "openai.gpt-oss-120b-1:0",                          # BE, NO
    "openai.gpt-oss-20b-1:0",                           # BE, NO
    "google.gemma-3-27b-it",                            # BE, NO
    "us.amazon.nova-2-lite-v1:0",                       # BE, NO
    "qwen.qwen3-235b-a22b-2507-v1:0",                   # BE, NO
    "qwen.qwen3-next-80b-a3b",                          # BE
    "minimax.minimax-m2.5",                             # BE
    "moonshotai.kimi-k2.5",                             # BE
    "zai.glm-4.7-flash",                                # BE
]

if __name__ == "__main__":
    # 1. Scrape all provider deprecation pages
    scraped = parse_all_deprecations()

    # 2. Scrape Bedrock model card metadata (context window, modalities, etc.)
    card_metadata = scrape_bedrock_model_cards()

    # 3. Merge everything into the persistent DB and prune old entries
    db = load_db()
    db = merge_scraped(db, scraped)
    db = merge_card_metadata(db, card_metadata)
    db, removed = cleanup_expired(db, days_threshold=365)
    if removed:
        print(f"  Pruned {removed} record(s) expired more than 1 year ago")
    save_db(db)
    all_records = get_all_records(db)

    # 4. Match against your model list
    matches, unmatched = check_my_models(MY_MODELS, all_records)

    # 5. Export to Google Sheets (All Models always reflects full DB)
    export_to_google_sheets(all_records, matches, unmatched, SPREADSHEET_ID)
