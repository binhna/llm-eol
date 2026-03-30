from parsers import parse_all_deprecations
from checker import check_my_models
from sheets import export_to_google_sheets

# ── Google Sheet to write results into ───────────────────────────────────────
# Open the sheet in your browser and copy the ID from the URL:
#   https://docs.google.com/spreadsheets/d/<SPREADSHEET_ID>/edit
SPREADSHEET_ID = '1zkXpUiVxZcZ9rmCD-Qc6C6oFky966vNMGvK0S1cYy6c'

# ── Models your application currently uses ───────────────────────────────────
# Add or remove model identifiers here. Supports:
#   - Direct model IDs (e.g. "gpt-4o-mini", "anthropic.claude-3-haiku-20240307-v1:0")
#   - Bedrock cross-region prefixes (e.g. "us.meta.llama3-3-70b-instruct-v1:0")
#   - Version-appended IDs (e.g. "claude-3-haiku@20240307")
MY_MODELS = [
    "chatgpt-4o-latest",
    "gpt-4o-mini",
    "claude-3-opus",
    "gpt-4o-2024-05-13",
    "gpt-5-nano",
    "gpt-5-mini",
    "gpt-5",
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "claude-sonnet-4",
    "claude-3-7-sonnet",
    "claude-3-5-haiku",
    "mistral-large-2411",
    "mistral-small-2503",
    "mistral-ocr-2505",
    "us.meta.llama3-3-70b-instruct-v1:0",
    "global.anthropic.claude-haiku-4-5-20251001-v1:0",
    "us.mistral.pixtral-large-2502-v1:0",
    "eu.mistral.pixtral-large-2502-v1:0",
    "us.deepseek.r1-v1:0",
    "openai.gpt-oss-120b-1:0",
    "us.meta.llama3-2-90b-instruct-v1:0",
    "mistral.magistral-small-2509",
    "google.gemma-3-27b-it",
    "openai.gpt-oss-20b-1:0",
    "us.amazon.nova-2-lite-v1:0",
    "qwen.qwen3-235b-a22b-2507-v1:0",
]

if __name__ == "__main__":
    # 1. Scrape all provider deprecation pages
    all_deprecations = parse_all_deprecations()

    # 2. Match against your model list
    matches, unmatched = check_my_models(MY_MODELS, all_deprecations)

    # 3. Export to Google Sheets (All Models tab always updated)
    export_to_google_sheets(all_deprecations, matches, unmatched, SPREADSHEET_ID)
