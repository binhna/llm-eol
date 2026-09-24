# Moving llm-eol into millmerran

This is the brief for whoever moves this project into the `millmerran` repo and
turns it into a scheduled job. It says what we want, what to set up, which
permissions it needs, how to test it, and the lessons already paid for, so
they don't have to be learned twice.

## Goal

Every night, without anyone running anything:

1. Find which LLM models our products declare and use (scan their repos).
2. Read every provider's retirement dates from their official documentation.
3. Keep a history of those dates in git.
4. Post to Slack **only** when something matters:
   - a tracked model's retirement date changed (X → Y),
   - a tracked model got a retirement date for the first time (for example, a Bedrock model entered Legacy),
   - a provider scrape returned nothing (the scraper is probably broken),
   - **every Monday:** production models with a fixed retirement date in the next 90 days.

   If there is nothing to say, the job stays silent.

The Google Sheet can stay as a view, or be dropped in favour of Slack plus the
committed history. That is a team decision, not a technical one.

## Where things go in millmerran

| What | Where | Why |
|---|---|---|
| This project's `src/` | `scripts/catalogue/src/` | **Not** in `src/millmerran/`. The wheel only packages `src/millmerran` (`pyproject.toml`), and these dependencies (pandas, bs4, lxml, gspread) must not reach every product that installs millmerran. |
| `requirements.txt` | `scripts/catalogue/requirements.txt` | Installed by the workflow only. |
| The database | `catalogue/snapshots/models_db.json`, committed | Git history *is* the change log, and it's what the Slack diff compares against. |
| The workflow | `.github/workflows/catalogue-sync.yaml` | New; millmerran has no scheduled workflow today. |

Paths to update after the move, because both are relative to the source file:
- `DB_PATH` in `src/database.py` (currently `<project>/data/models_db.json`) → point it at `catalogue/snapshots/models_db.json`.
- `MIRROR_DIR` in `src/scanner.py` (`.cache/repos`) is fine anywhere, but add it to millmerran's `.gitignore`.

## The workflow

```yaml
name: catalogue-sync
on:
  schedule:
    - cron: '0 18 * * *'        # 04:00 AEST (05:00 AEDT in summer)
  workflow_dispatch: {}          # manual run button

permissions:
  contents: write                # commit the snapshot back to the repo
  id-token: write                # only if using the AWS role (see step 4 below)

jobs:
  sync:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.12' }     # same as millmerran CI; 3.9+ required (zoneinfo)
      - run: pip install -r scripts/catalogue/requirements.txt
      - name: Let git read the product repos over HTTPS
        run: git config --global url."https://x-access-token:${{ secrets.CATALOGUE_REPO_READ_TOKEN }}@github.com/".insteadOf "git@github.com:"
      - name: Google key (only if keeping the sheet)
        run: echo '${{ secrets.GOOGLE_SHEETS_KEY_JSON }}' > "$RUNNER_TEMP/gsa.json"
      - run: python scripts/catalogue/src/main.py
        env:
          GOOGLE_CREDENTIALS_FILE: ${{ runner.temp }}/gsa.json
      - run: python scripts/catalogue/src/notify.py     # still to be written, see below
        env:
          SLACK_WEBHOOK_URL: ${{ secrets.SLACK_WEBHOOK_URL }}
      - name: Commit the snapshot
        run: |
          git config user.name  "catalogue-sync"
          git config user.email "catalogue-sync@users.noreply.github.com"
          git add catalogue/snapshots/models_db.json
          git diff --cached --quiet || git commit -m "catalogue snapshot $(date -u +%F)"
          git push
```

The `insteadOf` line matters: `PROJECTS` in `src/scanner.py` uses SSH URLs
(`git@github.com:Studiosity/...`), which work on a laptop but not in Actions.
The config line rewrites them to HTTPS with the token, with no code change.

## Permissions and secrets

| Need | How | Notes |
|---|---|---|
| **Read the product repos** | Fine-grained PAT or GitHub App with **read-only Contents** on the repos, stored as `CATALOGUE_REPO_READ_TOKEN` | The built-in `GITHUB_TOKEN` can only read millmerran itself. |
| **Commit the snapshot to millmerran** | `permissions: contents: write` in the workflow | If `main` is branch-protected (check this first), a direct push from Actions will be rejected. Either allow the bot to bypass, commit to a `catalogue-snapshots` branch instead, or open a PR each night. |
| **Slack** | Incoming webhook URL as `SLACK_WEBHOOK_URL` | millmerran has no Slack setup today. |
| **Google Sheet** (optional) | Service-account JSON as `GOOGLE_SHEETS_KEY_JSON`; the account needs **Editor** on the sheet | Skip entirely if the sheet is dropped. |
| **AWS** (later, optional) | Add `bedrock:ListFoundationModels` to `millmerran-gh-actions` and use the existing OIDC role (`id-token: write`) | Only for cross-checking Bedrock status via the API. The scrapers already give status and dates without AWS access. |

Product repos to grant read access to: bellmere, burley, norval, bordertown.
Also consider port-fairy, scoresby and grading-demo, which have model configs
but are **not yet in `PROJECTS`**. Check their config paths before adding them;
from the catalogue plan they are `.local/config/models.yaml` (port-fairy, a
copy of bellmere's), `src/scoresby/engine/llm/config/models.yaml` (scoresby),
and `llm_config.json` (grading-demo, location unconfirmed).

## Still to build: `notify.py`

About 60–80 lines. It compares the new `models_db.json` with the committed one
(`git show HEAD:catalogue/snapshots/models_db.json`) and posts one Slack
message. Reuse existing code rather than re-implementing it:

- `checker.check_my_models` + `scanner.scan_projects` → the tracked models and which platform's date applies;
- `utils.calculate_risk_info` → days remaining and risk level; it already treats floor dates (see below) as "no EOL";
- `parse_all_deprecations()` returns per-provider counts; 0 means a broken scraper.

Report, for tracked models only: changed `shutdown_date`, changed `lifecycle_stage`,
new models, and (Mondays, via `datetime.today().weekday() == 0`) production
models with CRITICAL/HIGH risk. Include each row's Source URL so people can check.

## How to test before trusting it

1. **Run locally from the new location:** `python scripts/catalogue/src/main.py --export-only`. It should finish in about 20 seconds and find about 41 models across 4 projects.
2. **Manual workflow run** (`workflow_dispatch`): check that the logs show `mirror of main` for every project (so repo access works), no `SCRAPE FAILED` (except Mistral until its parser is finished), and that a snapshot commit appears.
3. **Second manual run straight after:** no Slack message, no snapshot commit (nothing changed). This proves it stays silent.
4. **Force each alert once**, on a test branch:
   - edit one tracked model's `shutdown_date` in the committed snapshot and run → expect a "date changed" message;
   - set a production model's date to 30 days from now → expect it in the Monday list (or temporarily force the Monday branch);
   - point one parser's `SOURCE_URL` at a page with no tables → expect the "scrape failed" alert.
5. **Break the repo token on purpose** (wrong secret) → the run must say `could not mirror … check your git access`, not report zero models as if all was fine.

## Lessons already learned — don't undo these

- **"No sooner than" is not a retirement date.** AWS "EOL no sooner than", Anthropic "Not sooner than", Azure "no earlier than" and Google "X or later" are all minimum-availability promises. Treating them as dates once marked 11 Active models, including production ones, as EXPIRED. `utils._FLOOR_RE` handles this.
- **Bedrock: status from the card, dates from the Legacy list.** A model has a real EOL date only once AWS moves it to Legacy (`model-lifecycle-legacy.html`, or the card's "Model EOL date"). AWS gives at least 6 months' notice (45 days for a few models).
- **The platform decides the date.** The same model has different dates on different platforms: gemini-2.5-flash-lite has none on the Gemini API but 20 Oct 2026 on Vertex AI. `checker._CONFIG_PROVIDER_PREFERENCE` picks the platform each project's config says it uses.
- **Silent failures are the normal failure.** Anthropic renamed a column (4 months of stale data, no error). AWS moved its tables. AWS added a "Model lifecycle policy:" line that corrupted every status. The zero-record check catches the first two, but a scraper that returns *wrong* data looks healthy, so spot-check against the Source URL after any scraper change.
- **Write sheet text and colours in one request** (`sheets._write_sheet`), otherwise a failed second call leaves old colours under new text.
- **Ask servers for gzip** (`utils.get_html`). Some sites send brotli, which fails to decode in some environments.
- **Only scan `main`**, from private bare mirrors. It never touches anyone's checkout, and everyone gets the same result.

## Open items at the time of the move

- **Mistral scraper** (`src/parsers/mistral.py`, not yet committed): docs.mistral.ai renders its table as a heading-only table followed by a body-only table, with the heading `DeprecationRetirement` (no slash). Pair the two tables before looking up columns.
- **Vertex-hosted Mistral dates** (`mistral-large-2411`, `mistral-ocr-2505`, and `mistral-small-2503` on Vertex) are still not found anywhere.
- **Part 1 of the catalogue plan** (one packaged `models.yaml`): once it lands, point `PROJECTS` at the packaged catalogue plus repo overlays instead of four separate configs, and consider a `retires_no_sooner_than` field so floors aren't stored as `retires_on`.
