# llm-eol

Scrapes LLM model deprecation and retirement dates from provider documentation, matches them against your list of active models, maintains a persistent local database, and exports results to Google Sheets with risk-based colour coding.

## Before You Start (please read)

To run this on your own computer you need one file: **`credentials.json`**. This is the key that lets the script sign in to Google and write to our shared sheet. For security, everyone uses **their own** key — it is never passed around between people, and it is never committed to this project (git ignores it).

Two one-time steps:

1. **Create your own `credentials.json`.** Follow [Setup](#setup) steps 3–4 below: in a Google Cloud project turn on the Google Sheets and Drive APIs, create a service account, and download its JSON key. Save that file in the main folder of this project (the same folder as this README) and name it exactly `credentials.json`. The script finds it automatically.

2. **Get your key access to the shared sheet.** Open your `credentials.json` and copy the `client_email` value — it looks like `something@your-project.iam.gserviceaccount.com`. Send that address to Ben (bnguyen@studiosity.com), and he'll share the team sheet with it so your key is allowed to write. (You only do this once.)

**We all write to the same Google Sheet — on purpose.** The sheet is already set in the code, so everyone's results go to one shared place. Please **do not change the sheet ID** in `src/main.py` and **do not create your own sheet** — if we each used a different one we'd end up with scattered, out-of-date data instead of one view everyone can trust. Once your key has access, run the script whenever you like to keep the shared sheet current (see [Usage](#usage)).

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

1. **Scans our product repos** to work out which models to track (see below)
2. Scrapes each provider's deprecation/lifecycle page
3. Scrapes AWS Bedrock individual model card pages for rich metadata
4. Merges results into `data/models_db.json` — records are **never deleted** by a scrape run (models that disappear from a provider page keep their last-known data)
5. Prunes records whose shutdown date expired more than 1 year ago
6. Matches the full DB against the models found in step 1
7. Exports four Google Sheets tabs with colour-coded risk levels

## Which Models Get Tracked

The model list is **worked out automatically** — there is no hand-maintained list to keep in sync.

Every model our products can call has to be declared in that project's model config file, so that file is the source of truth. For each project the scanner reads that config, then searches the rest of that repo for each model name to see whether anything actually references it.

| Project | Model config | Read from |
|---|---|---|
| bellmere | `src/config/models.yaml` | your local checkout, whatever branch it's on |
| burley | `src/llm_config.json` | our own mirror of `main` |
| norval | `llm_config.json` | our own mirror of `main` |
| bordertown | `dev/llm_config.json` | our own mirror of `main` |

### It never disturbs anyone's work

You do **not** need to pull the other repos, and this tool will never do it for you.

For the mirrored projects it keeps a private **bare mirror** of each repo under `.cache/repos/` inside this project. A bare mirror is a read-only copy of the repo's history with no working files at all, which means scanning:

- never touches your own clone of that repo — not the files, not the branch, not uncommitted work, not even its `.git` folder
- always sees the latest `main` from the server
- works even if you have never cloned that repo

Nothing in this project ever runs `git pull`, `git checkout`, `git merge` or `git reset` on anything. The mirrors are shallow (a few MB each), are ignored by git, and are rebuilt automatically if you delete `.cache/`.

bellmere is the one exception: it is read from your **local checkout** so you can see what's on your current branch rather than `main`. That is still read-only — the tool only reads files and runs `git grep`. If you don't have bellmere cloned, it quietly falls back to the mirror of `main`.

Set `REFRESH_MIRRORS = False` in `src/scanner.py` to work fully offline from whatever the mirrors already hold.

Each model lands in one of three buckets. They answer **"where is this model referenced?"** — so the first two are both real usage, the label just says *which kind*:

| Usage | Meaning |
|---|---|
| **Production** | Referenced from production code or prompt config — it can serve real traffic |
| **Test only** | Referenced, but only from tests, experiments, dev scripts or reporting |
| **Config only** | Declared in the config, referenced nowhere else in the repo |

The scanner also records the **provider** each config declares (`azure`, `google`, `anthropic`, `mistral`, `bedrock`). That matters for two reasons: it tells you where a model is hosted even when no provider page mentions it, and it decides which date wins when the same model appears on several platforms — `claude-3-5-haiku` retires February 2026 on Anthropic's own API but July 2026 on Vertex AI, and only the platform you actually call is relevant.

> **"Config only" does not mean safe to ignore.** These projects pick models by *name* at runtime, so any declared model goes live by editing one prompt file — no code change, no deploy. That is exactly why we still track its end-of-life date.

To add a repo, change a config path, or point at a different branch, edit `PROJECTS` at the top of `src/scanner.py`. Projects you don't have cloned are skipped with a note rather than failing the run.

Searching uses `git grep`, so it only looks at files git tracks — virtualenvs and build output can't pollute the results, and no extra tools are needed. Config files themselves are excluded from the search (otherwise every model would look "used" just for being listed), and matches must be the **whole** model name, so `gemini-2.5-flash` is not reported as used on a line that says `gemini-2.5-flash-lite`.

Models used by something outside these four repos can be added to `EXTRA_MODELS` in `src/main.py`.

## Risk Levels

| Level | Condition | Colour |
|---|---|---|
| EXPIRED | Already past shutdown date | Muted rose |
| CRITICAL | ≤ 30 days remaining | Soft peach-orange |
| HIGH | ≤ 90 days remaining | Soft amber |
| MEDIUM | ≤ 180 days remaining | Pale yellow |
| LOW | > 180 days remaining | Soft mint |
| No EOL announced | The provider lists the model but has scheduled no retirement. Nothing to do yet. | Pale blue |
| Unknown | There **is** a date on the page but we couldn't read it — a parsing gap worth investigating | White |
| Not found | Model not on any provider page at all | Light grey |

`No EOL announced` and `Unknown` used to be lumped together as "Unknown", which made a definite answer ("AWS says this model has no EOL date") look like missing data.

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

Every model found by the repo scan. Unmatched models appear at the bottom in grey with "Not found" values so nothing is silently omitted.

| Column | Notes |
|---|---|
| Last Updated | Timestamp of the run (Australia/Melbourne timezone) |
| Our Model | The model identifier as declared in the project config |
| Scraped Model | Matched identifier from the provider page |
| Provider | |
| Scraped Shutdown Date | |
| Parsed Shutdown Date | |
| Days Remaining | |
| Risk Level | Colour-coded cell |
| Projects | Which of our products declare it, comma-separated |
| Provider (config) | The provider our own config declares — filled in even when the model isn't on any provider page |
| Usage | `Production` / `Test only` / `Config only` — filter on this |

### Model Usage (tab 3)

One row per model per project, so you can see exactly where each model comes from and what evidence there is for it being used.

| Column | Notes |
|---|---|
| Model | |
| Project | |
| Declared In Config | Always `Yes` — every row here came from a project's model config |
| Referenced In Code | `No` when the model is config-only |
| Usage | Colour-coded: mint = Used, yellow = Test/Experiment, grey = Config only |
| Evidence (file:line) | Where the reference was found, for the strongest hit |

### Bedrock Details (tab 4)

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

The model list is discovered automatically, so normally there is nothing to configure.

To change which repos are scanned, edit `PROJECTS` in `src/scanner.py`:

```python
PROJECTS = [
    {
        'name': 'burley',
        'remote': 'git@github.com:Studiosity/burley.git',
        'branch': 'main',
        'source': 'mirror',        # read our own bare mirror — safest
        'config': 'src/llm_config.json',
        'format': 'json_keys',     # or 'yaml_nested'
        'exclude': [...],          # other config files, not usage
    },
    {
        'name': 'bellmere',
        'source': 'worktree',      # read a local checkout as it is on disk
        'path': '../bellmere',
        'remote': '...',           # still used if the checkout is missing
        'branch': 'main',
        ...
    },
]
```

Set `REFRESH_MIRRORS = False` in the same file to scan fully offline.

For a model used outside these repos, add it to `EXTRA_MODELS` in `src/main.py`.

**Model matching rules** (applied in order):
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
  main.py                    ← entry point: SPREADSHEET_ID, EXTRA_MODELS, run order
  scanner.py                 ← PROJECTS: scan our repos for declared + used models
  utils.py                   ← get_html, parse_shutdown_date, calculate_risk_info
  checker.py                 ← check_my_models, Bedrock geo-prefix matching
  sheets.py                  ← Google Sheets export (4 tabs)
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
