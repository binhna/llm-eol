# Changelog

All notable changes to this project are documented here.
Entries are in reverse-chronological order. Each entry explains *what* changed and *why*.

---

## 2026-03-31

### Persistent database, unmatched model visibility, Bedrock model card metadata

#### Problem
Each run replaced the entire Google Sheet with fresh scrape data. Models that
moved between tables (e.g. Bedrock moving a model from Active → Legacy) or
disappeared temporarily from a provider page were silently lost. Models in
`MY_MODELS` that had no match in any provider page were also silently omitted
from the output.

Additionally, AWS Bedrock's individual model card pages contain rich metadata
(context window, max tokens, modalities, geo inference IDs) that was not being
captured at all.

#### Changes

**Persistent local database (`src/database.py`, `data/models_db.json`)**
- Added `data/models_db.json` as a persistent model store keyed by `"{provider}|{model}"`
- `merge_scraped()` updates lifecycle fields (shutdown date, lifecycle stage, source URL) and advances `last_seen` for records present in the scrape; records absent from the scrape are kept unchanged — their `last_seen` simply stops advancing
- `merge_card_metadata()` writes Bedrock model card fields without touching lifecycle fields
- `cleanup_expired()` removes records whose shutdown date passed more than 1 year ago, keeping the DB and sheet from growing indefinitely
- `get_all_records()` returns all DB records sorted by provider then model
- `data/models_db.json` is safe to commit (no secrets); `credentials.json` is the only file that must stay out of version control — `.gitignore` updated accordingly

**Unmatched models shown in Interested Models tab (`src/checker.py`, `src/sheets.py`)**
- `check_my_models()` now returns `(matches, unmatched)` — a second list of model identifiers from `MY_MODELS` that had zero matches
- Unmatched models appear at the bottom of the Interested Models sheet as light-grey rows with "Not found" values so nothing is ever silently omitted

**AWS Bedrock model card metadata (`src/parsers/bedrock_model_cards.py`)**
- `scrape_bedrock_model_cards()` auto-discovers all card URLs by walking `model-cards.html` → per-provider index pages → individual card pages (~100 pages across 17 providers)
- Extracts per model: `model_id`, `context_window`, `max_output_tokens`, `input_modalities`, `output_modalities`, `knowledge_cutoff`, `geo_inference_ids`, `model_card_url`
- Data is stored in the DB via `merge_card_metadata()` and shown in a new **Bedrock Details** sheet (tab 3)

**All Models sheet gains `First Seen` / `Last Seen` columns**
- `First Seen`: date the model was first added to the local DB
- `Last Seen`: date the model was last confirmed by a live scrape — stale values here indicate a model may have been removed from a provider page

---

## 2026-03-30

### Refactor: split single file into modules

#### Problem
`src/main.py` contained all parsers, matching logic, date utilities, and the
Sheets export in one ~700-line file. Adding a new provider or modifying the
Sheets layout meant navigating the entire file.

#### Changes
- `src/utils.py` — `get_html`, `parse_shutdown_date`, `calculate_risk_info`
- `src/checker.py` — `check_my_models` and the Bedrock geo-prefix matching regex
- `src/sheets.py` — all Google Sheets logic (`_write_sheet`, `_get_or_create_worksheet`, `export_to_google_sheets`)
- `src/parsers/` — one file per provider plus `__init__.py` for `parse_all_deprecations`
- `src/main.py` — reduced to configuration (`MY_MODELS`, `SPREADSHEET_ID`) and the 5-step run sequence

---

### AWS Bedrock lifecycle parser added

Scrapes the three lifecycle tables from `model-lifecycle.html`:
- **Active** — uses the `Model ID` column; "No sooner than X" EOL date prefix is stripped
- **Legacy** — distinguished from EOL by presence of the "Public extended access date" column
- **EOL** — concrete retirement dates

For models appearing multiple times (different region-specific EOL dates), the earliest date is kept.
Each record carries a `lifecycle_stage` field (`Active` / `Legacy` / `EOL`) shown as a column in the All Models sheet.

---

### Bedrock cross-region inference prefix matching

AWS Bedrock cross-region inference profile IDs carry a geo prefix
(`us.`, `eu.`, `ap.`, `apac.`, `au.`, `ca.`, `jp.`, `global.`, `us-gov.`)
not present in the scraped model IDs. Rule 4 in `check_my_models` strips this
prefix before comparing, so `us.meta.llama3-3-70b-instruct-v1:0` correctly
matches `meta.llama3-3-70b-instruct-v1:0`.

---

### Two-sheet export (All Models + Interested Models)

Previously the sheet only showed models that matched `MY_MODELS`. Now:
- **All Models** (tab 1) shows every record from every provider — useful for
  discovering models before adding them to `MY_MODELS`
- **Interested Models** (tab 2) shows only the matched subset
- The export runs on every execution, not only when matches exist

---

### Spreadsheet identified by ID instead of name

Changed from `client.open(name)` to `client.open_by_key(id)`. Spreadsheet
names can be duplicated or renamed; IDs are permanent.
`SPREADSHEET_ID` is configured at the top of `src/main.py`.

---

### Quota fix: batch all formatting into one API call

The original code called `sheet.format()` once per data row inside a loop.
For a sheet with 80+ rows this exceeded the 60-writes/minute quota.
`_write_sheet` now sends a single `spreadsheet.batch_update()` containing all
`repeatCell` requests (format reset, header styling, per-row risk colour),
reducing writes to 3 per sheet tab regardless of row count.

---

### Date parsing improvements

`parse_shutdown_date` was extended to handle:
- **Region qualifiers in parentheses** — `"July 7, 2026 (us-west-2 and us-east-2 Regions)"` had the region text confuse the fuzzy parser; parenthetical content is now stripped first
- **Month-name dates** — `"July 7, 2026"`, `"Mar 1, 2026"`, `"January 15th, 2026"` are extracted with an explicit regex before the fuzzy fallback
- **Multi-date Azure strings** — the earliest YYYY-MM-DD date is returned

---

### Risk colour made less intense, scoped to Risk Level cell only

Previously the entire row was coloured with high-contrast solid colours.
Changed to colour only the Risk Level cell, and softened the palette to
muted pastels (rose, peach-orange, amber, pale yellow, soft mint).

---

## Initial release

- Scrapes Google Gemini, OpenAI, Azure OpenAI, Anthropic, Vertex AI
- Matches against a user-supplied model list with fuzzy rules
- Exports matched models to Google Sheets with risk-based colouring
- Console report with days remaining and risk level
