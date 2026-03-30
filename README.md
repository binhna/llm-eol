# llm-eol

Scrapes LLM model deprecation and retirement dates from provider documentation, matches them against your list of active models, maintains a persistent local database, and exports results to Google Sheets with risk-based colour coding.

## Supported Providers

| Provider | Source page |
|---|---|
| Google Gemini | ai.google.dev/gemini-api/docs/deprecations |
| OpenAI | developers.openai.com/api/docs/deprecations |
| Azure OpenAI | learn.microsoft.com — model retirements |
| Anthropic | platform.claude.com/docs/about-claude/model-deprecations |
| Vertex AI | docs.cloud.google.com — partner models deprecations |
| AWS Bedrock | docs.aws.amazon.com/bedrock/latest/userguide/model-lifecycle.html |

AWS Bedrock model card pages (context window, modalities, geo inference IDs, etc.) are also scraped from the provider-specific card hierarchy under `model-cards.html`.

## How It Works

1. Scrapes each provider's deprecation/lifecycle page
2. Scrapes AWS Bedrock individual model card pages for rich metadata
3. Merges results into `data/models_db.json` — records are **never deleted** by a scrape run (models that disappear from a provider page keep their last-known data)
4. Prunes records whose shutdown date expired more than 1 year ago
5. Matches the full DB against `MY_MODELS` in `src/main.py`
6. Exports three Google Sheets tabs with colour-coded risk levels

## Risk Levels

| Level | Condition | Colour |
|---|---|---|
| EXPIRED | Already past shutdown date | Muted rose |
| CRITICAL | ≤ 30 days remaining | Soft peach-orange |
| HIGH | ≤ 90 days remaining | Soft amber |
| MEDIUM | ≤ 180 days remaining | Pale yellow |
| LOW | > 180 days remaining | Soft mint |
| Unknown | Date could not be parsed | White |
| Not found | Model not in any provider page | Light grey |

## Google Sheets Output

### All Models (tab 1)

Every record in the local DB, across all providers.

| Column | Notes |
|---|---|
| Provider | |
| Model | Model ID or human-readable name |
| Lifecycle Stage | `Active` / `Legacy` / `EOL` — AWS Bedrock only |
| Scraped Shutdown Date | Raw string from the provider page |
| Parsed Shutdown Date | Normalised to `YYYY-MM-DD`; `N/A` if unparseable |
| Days Remaining | Integer; negative = already expired |
| Risk Level | See table above; colour-coded cell |
| Source URL | Direct link to the provider page |
| First Seen | Date the record was first added to the local DB |
| Last Seen | Date the record was last confirmed by a scrape run |

### Interested Models (tab 2)

Only models from `MY_MODELS`. Unmatched models appear at the bottom in grey with "Not found" values so nothing is silently omitted.

| Column | Notes |
|---|---|
| Last Updated | Timestamp of the run (Australia/Melbourne timezone) |
| Our Model | Exactly as written in `MY_MODELS` |
| Scraped Model | Matched identifier from the provider page |
| Provider | |
| Scraped Shutdown Date | |
| Parsed Shutdown Date | |
| Days Remaining | |
| Risk Level | Colour-coded cell |

### Bedrock Details (tab 3)

AWS Bedrock models that have model card metadata. Only shown when card data is available.

| Column | Notes |
|---|---|
| Model ID | |
| Lifecycle Stage | |
| Context Window | Tokens (integer) |
| Max Output Tokens | Tokens (integer) |
| Input Modalities | Comma-separated list |
| Output Modalities | Comma-separated list |
| Knowledge Cutoff | |
| Geo Inference IDs | Comma-separated cross-region inference profile IDs |
| Model Card URL | Direct link to the AWS Bedrock model card page |

## Configuration

Edit `src/main.py`:

```python
# Target Google Sheet
SPREADSHEET_ID = 'your-sheet-id-from-url'

# Models your application uses
MY_MODELS = [
    "gpt-4o-mini",
    "anthropic.claude-3-haiku-20240307-v1:0",
    "us.meta.llama3-3-70b-instruct-v1:0",   # Bedrock cross-region prefix stripped automatically
    ...
]
```

**`MY_MODELS` matching rules** (applied in order):
1. Exact match
2. Scraped model has appended version info — `gpt-4o` matches `gpt-4o (2024-05-13)`
3. User model has appended version tag — `claude-3-haiku@20240307` matches `claude-3-haiku`
4. AWS Bedrock cross-region prefix stripped — `us.meta.llama3-...` matches `meta.llama3-...`
   Supported prefixes: `us.` `eu.` `ap.` `apac.` `au.` `ca.` `jp.` `global.` `us-gov.`

## Usage

```bash
python src/main.py
```

Run from the project root. The `src/` directory is automatically on the Python path.

## Setup

### 1. Python Version

Python **3.8 or later** is required.

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

### 3. Enable Google Sheets API

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create or select a project
3. Enable the **Google Sheets API** and **Google Drive API**

### 4. Create a Service Account

1. Navigate to **IAM & Admin > Service Accounts**
2. Click **Create Service Account** (e.g. `llm-eol-tracker`)
3. Click the account → **Keys** tab → **Add Key > Create New Key > JSON**
4. Save the downloaded file as `credentials.json` in the project root, or set `GOOGLE_CREDENTIALS_FILE` to its path

> `credentials.json` is listed in `.gitignore` and must never be committed.

### 5. Share the Spreadsheet

Copy the service account email from `credentials.json` and share your target Google Sheet with it (Editor access).

## Local Database

Model records are persisted to `data/models_db.json`. This file:

- Is safe to commit — it contains no secrets, only scraped public data
- Grows incrementally — records are merged in on each run, never bulk-replaced
- Retains records that disappear from provider pages, with `last_seen` showing when they were last confirmed
- Has expired entries (shutdown date > 1 year ago) pruned automatically on each run

The Google Sheet is a **human-readable view** of the database, not a replacement for it. It is output-only; the JSON file is the operational source of truth.

## Project Layout

```
src/
  main.py                    ← entry point: MY_MODELS, SPREADSHEET_ID, run order
  utils.py                   ← get_html, parse_shutdown_date, calculate_risk_info
  checker.py                 ← check_my_models, Bedrock geo-prefix matching
  sheets.py                  ← Google Sheets export (3 tabs)
  database.py                ← load/save/merge/cleanup for data/models_db.json
  parsers/
    __init__.py              ← parse_all_deprecations (calls all parsers, deduplicates)
    google_gemini.py
    openai.py
    azure_openai.py
    anthropic.py
    vertex_ai.py
    bedrock.py               ← lifecycle page (Active / Legacy / EOL tables)
    bedrock_model_cards.py   ← model card pages (context window, modalities, etc.)
data/
  models_db.json             ← persistent model database (auto-created on first run)
```
