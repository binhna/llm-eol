# llm-eol

Tracks LLM model deprecation and retirement dates, matches them to models used by our products, and updates the shared Google Sheet with risk levels.

## Start here

### Requirements

- Python 3.9 or later
- Git
- Access to the shared Google Sheet
- **A Google service-account key saved as `credentials.json`**

### First-time setup

1. Install the dependencies from the project root:

   ```bash
   pip install -r requirements.txt
   ```

2. In [Google Cloud Console](https://console.cloud.google.com/), enable the **Google Sheets API** and **Google Drive API**.

3. Create a service account and download a JSON key. Save it in this project's root folder as `credentials.json`.

   You can use another path by setting `GOOGLE_CREDENTIALS_FILE`.

4. Open `credentials.json` and send its `client_email` to Ben (bnguyen@studiosity.com). The service account must be given Editor access to the shared sheet. This is a one-time step.

> Never commit `credentials.json`. It is ignored by Git and must remain private.

### Run

Run from the project root:

```bash
python src/main.py
```

The script scans product repositories, scrapes provider documentation, updates `data/models_db.json`, and exports four tabs to the shared Google Sheet. The sheet ID is already configured in `src/main.py`; do not change it or create a separate sheet.

A full run takes a few minutes, mostly spent reading provider documentation.

To skip that and send the data you already have straight to the sheet:

```bash
python src/main.py --export-only
```

This reuses the last scrape stored in `data/models_db.json` and finishes in seconds. Use it to retry after a failed upload — Google returning a `503`, for example — since the scraped data is saved before the upload step and is not lost. It still scans the product repositories, so the model list stays current; it just does not contact the provider websites.

## What it tracks

The model list is discovered automatically from product repositories. A model is included when it is declared in a project's model config, even if it is not referenced elsewhere.

| Project | Model config | Source |
|---|---|---|
| bellmere | `src/config/models.yaml` | Private mirror of `main` |
| burley | `src/llm_config.json` | Private mirror of `main` |
| norval | `llm_config.json` | Private mirror of `main` |
| bordertown | `dev/llm_config.json` | Private mirror of `main` |

Models used outside these repositories can be added to `EXTRA_MODELS` in `src/main.py`.

Supported provider sources are Google Gemini, OpenAI, Azure OpenAI, Anthropic, Vertex AI (partner models and Google's own Gemini model versions), AWS Bedrock, and Mistral. For Gemini models, the Vertex AI dates are used first, because our products call Gemini through Vertex AI and its dates can differ from the Gemini API's. For Bedrock, a model's status comes from its model card, and its EOL date comes from AWS's Legacy list (or the card's EOL field once AWS fills it in). Bedrock model cards are also scraped for context limits, modalities, knowledge cutoff, and cross-region inference IDs.

## Results

The Google Sheet contains four tabs:

- **All Models:** every model in the local database.
- **Interested Models:** models found in our product repositories, matched to provider data.
- **Model Usage:** each model's project, usage classification, and evidence file.
- **Bedrock Details:** extra metadata from AWS model cards.

Risk levels are based on the shutdown date:

| Level | Meaning |
|---|---|
| EXPIRED | Shutdown date has passed |
| CRITICAL | 30 days or less remain |
| HIGH | 31 to 90 days remain |
| MEDIUM | 91 to 180 days remain |
| LOW | More than 180 days remain |
| No EOL announced | The provider lists the model but has no retirement date. This includes dates given only as a floor — "no sooner than X" (AWS), "not sooner than X" (Anthropic), "no earlier than X" (Azure), "X or later" (Google) — because every provider defines those as a minimum availability promise, not a retirement. |
| Unknown | A date exists but could not be parsed |
| Not found | The model was not found on a provider page |

### Data warnings

Check the **Data Warning** column before acting on a date:

- Blank means the provider listed the model during the latest run.
- `COULD NOT READ ... PAGE` means the parser likely broke after a provider page change. The old date is not trustworthy.
- `Provider page no longer lists this model` means the date is retained from the last successful scrape.
- `Not listed on any provider page` means no date was found.

Warning rows are highlighted pink. Open the **Source URL** to verify any date manually.

## How scanning works

For each project, the scanner reads the model config and searches tracked files with `git grep`:

- **Production:** referenced outside known test or development paths.
- **Test only:** referenced only in tests, experiments, development scripts, or similar paths.
- **Config only:** declared in the config but not referenced elsewhere.

This classification is based on folder and file naming conventions. It is a useful indication, not a guarantee. Always check the **Evidence** column. Unknown folders are treated as production so live models are less likely to be missed.

The scanner records the provider declared by each project. When the same model appears on multiple platforms, that provider determines which date is relevant.

## Repository safety and offline mode

Mirrored projects are stored as private, read-only bare mirrors under `.cache/repos/`. The scanner does not change your other clones, branches, files, or Git history. Projects you do not have locally are skipped or read from their mirror.

By default, every project is read from a private mirror of `main`. This gives everyone the same, repeatable results and keeps local branches and uncommitted changes out of the shared report.

To skip network refreshes and use existing mirrors, set this in `src/scanner.py`:

```python
REFRESH_MIRRORS = False
```

To add or change a scanned repository, edit `PROJECTS` in `src/scanner.py`.

## Local database

`data/models_db.json` is the operational source of truth. It contains public scraped data and is safe to commit.

- Records are merged, not replaced.
- Models missing from a provider page are retained with their last-seen date.
- Records expired for more than one year are pruned automatically.
- The Google Sheet is an output view of this database.

## Moving to millmerran

See [docs/MILLMERRAN_MIGRATION.md](docs/MILLMERRAN_MIGRATION.md) for the plan to run this nightly from millmerran: where the code goes, the workflow, the permissions and secrets it needs, how to test it, and lessons to keep.

## Project layout

```text
src/main.py                    Entry point and sheet configuration
src/scanner.py                 Finds declared and referenced models
src/checker.py                 Matches models to provider records
src/sheets.py                  Writes the four Google Sheet tabs
src/database.py                Loads, merges, and cleans the local database
src/utils.py                   Fetching, date parsing, and risk calculation
src/parsers/                   Provider and Bedrock model-card parsers
data/models_db.json            Persistent scraped model data
```