# llm-eol

A lightweight tool that scrapes LLM deprecation and retirement dates directly from provider documentation, checks them against your list of active models, and exports the results to Google Sheets with risk-based color coding.

## Supported Providers

| Provider | Source |
|---|---|
| Google Gemini | ai.google.dev/gemini-api/docs/deprecations |
| OpenAI | developers.openai.com/api/docs/deprecations |
| Azure OpenAI | learn.microsoft.com — model retirements |
| Anthropic | platform.claude.com/docs/about-claude/model-deprecations |
| Vertex AI | docs.cloud.google.com — partner models deprecations |
| AWS Bedrock | docs.aws.amazon.com/bedrock/latest/userguide/model-lifecycle.html |

## How It Works

1. Scrapes each provider's deprecation page for model names and shutdown dates
2. Matches them against the models listed in `my_used_models` inside `src/main.py`
3. Prints a report to the console with days remaining and a risk level
4. If any matches are found, exports the results to a Google Sheet with color-coded rows

**Risk levels:**

| Level | Condition | Color |
|---|---|---|
| EXPIRED | Already past shutdown date | Dark red |
| CRITICAL | ≤ 30 days remaining | Orange |
| HIGH | ≤ 90 days remaining | Yellow |
| MEDIUM | ≤ 180 days remaining | Light yellow |
| LOW | > 180 days remaining | Light green |

## Usage

Edit the `my_used_models` list in `src/main.py` with the models your application uses, then run:

```bash
python src/main.py
```

## Setup

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Enable Google Sheets API

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create or select a project
3. Enable the **Google Sheets API** and **Google Drive API**

### 3. Create a Service Account

1. Navigate to **IAM & Admin > Service Accounts**
2. Click **Create Service Account** and give it a name (e.g. `llm-eol-tracker`)

### 4. Generate Credentials

1. Click the service account → **Keys** tab
2. Select **Add Key > Create New Key > JSON**
3. Save the downloaded file as `credentials.json` in the project root
   - Alternatively, set the `GOOGLE_CREDENTIALS_FILE` environment variable to point to its path

### 5. Share Your Google Sheet

Copy the service account email from `credentials.json` and share your target Google Sheet with it (Editor access).

> **Note:** `credentials.json` contains sensitive credentials — make sure it is listed in `.gitignore` and never committed to version control.

### Export Format

The spreadsheet contains two tabs:

**All Models** — every model scraped from all provider pages:

`Provider` | `Model` | `Lifecycle Stage` | `Scraped Shutdown Date` | `Parsed Shutdown Date` | `Days Remaining` | `Risk Level` | `Source URL`

> `Lifecycle Stage` is only populated for AWS Bedrock rows: `Active`, `Legacy`, or `EOL`. All other providers leave it blank. This lets you filter out Bedrock models that only have a human-readable name (Legacy/EOL) rather than a machine-readable model ID (Active).

**Interested Models** — only models that matched your `my_used_models` list:

`Last Updated` | `Our Model` | `Scraped Model` | `Provider` | `Scraped Shutdown Date` | `Parsed Shutdown Date` | `Days Remaining` | `Risk Level`

`Parsed Shutdown Date` shows `N/A` when the raw date string could not be parsed into a date. Timestamps are recorded in the **Australia/Melbourne** timezone.

The export runs on every execution (not just when matches are found), so the **All Models** tab stays current even if none of your interested models are deprecated.
