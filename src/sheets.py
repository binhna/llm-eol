import os
import pytz
from datetime import datetime
from utils import calculate_risk_info


def _get_or_create_worksheet(spreadsheet, title, index):
    """Return a worksheet by title, creating it (at given tab index) if it doesn't exist."""
    import gspread
    try:
        return spreadsheet.worksheet(title)
    except gspread.WorksheetNotFound:
        return spreadsheet.add_worksheet(title=title, rows=1000, cols=20, index=index)


def _write_sheet(spreadsheet, sheet, headers, rows, cell_colors, last_col_index):
    """
    Clear, write all data, and apply all formatting in a single batch_update call.

    cell_colors is one dict per data row, mapping a column index to a colour —
    so a row can highlight several cells (risk level, usage, staleness) rather
    than just one. Keeps API write requests to 3 per sheet regardless of row
    count, avoiding the 60-writes/min quota limit.
    """
    num_cols = last_col_index + 1
    sheet_id = sheet.id

    # 1. Clear existing content (1 request)
    sheet.clear()

    # 2. Write headers + all data rows in one call (1 request)
    all_values = [headers] + rows
    col_letter = chr(ord('A') + last_col_index)
    sheet.update(values=all_values, range_name=f'A1:{col_letter}{len(all_values)}')

    # 3. Build ALL formatting changes and send as a single batch_update (1 request)
    requests = []

    # Reset all formatting across the full worksheet first (handles leftover colors
    # from previous runs that had more rows than the current run)
    requests.append({
        'repeatCell': {
            'range': {
                'sheetId': sheet_id,
                'startRowIndex': 0,
                'startColumnIndex': 0,
            },
            'cell': {'userEnteredFormat': {}},
            'fields': 'userEnteredFormat',
        }
    })

    # Header: dark background, bold white text
    requests.append({
        'repeatCell': {
            'range': {
                'sheetId': sheet_id,
                'startRowIndex': 0, 'endRowIndex': 1,
                'startColumnIndex': 0, 'endColumnIndex': num_cols,
            },
            'cell': {'userEnteredFormat': {
                'backgroundColor': {'red': 0.2, 'green': 0.2, 'blue': 0.2},
                'textFormat': {
                    'bold': True,
                    'foregroundColor': {'red': 1.0, 'green': 1.0, 'blue': 1.0},
                },
            }},
            'fields': (
                'userEnteredFormat.backgroundColor,'
                'userEnteredFormat.textFormat.bold,'
                'userEnteredFormat.textFormat.foregroundColor'
            ),
        }
    })

    # Colour individual cells: one repeatCell per (row, column) that needs it
    for i, colors in enumerate(cell_colors):
        if not colors:
            continue
        row_idx = i + 1  # 0-based; row 0 is the header
        for col_idx, color in sorted(colors.items()):
            requests.append({
                'repeatCell': {
                    'range': {
                        'sheetId': sheet_id,
                        'startRowIndex': row_idx, 'endRowIndex': row_idx + 1,
                        'startColumnIndex': col_idx, 'endColumnIndex': col_idx + 1,
                    },
                    'cell': {'userEnteredFormat': {'backgroundColor': color}},
                    'fields': 'userEnteredFormat.backgroundColor',
                }
            })

    if requests:
        spreadsheet.batch_update({'requests': requests})


# Usage-status colours for the 'Model Usage' tab and the Usage column
_USAGE_COLORS = {
    'Production':  {'red': 0.76, 'green': 0.93, 'blue': 0.76},  # soft mint
    'Test only':   {'red': 1.0,  'green': 0.97, 'blue': 0.78},  # pale yellow
    'Config only': {'red': 0.88, 'green': 0.88, 'blue': 0.88},  # light grey
}
_NEUTRAL_COLOR = {'red': 1.0, 'green': 1.0, 'blue': 1.0}

# Light pink: this row's figure was NOT re-confirmed by the provider on this run,
# so it is a last-known value. The most important warning on the sheet.
_STALE_COLOR = {'red': 1.0, 'green': 0.85, 'blue': 0.90}


def _usage_lookup(scan):
    """
    Build {model: (projects, providers, usage_status)} from a scanner result,
    each as a display-ready string. Returns {} when no scan was supplied.
    """
    if not scan or not scan.get('models'):
        return {}
    out = {}
    for model, entry in scan['models'].items():
        projects = ', '.join(sorted(entry.get('declared_in', [])))
        providers = ', '.join(sorted(entry.get('providers', [])))
        out[model] = (projects, providers, entry.get('status', ''))
    return out


def export_to_google_sheets(all_deprecations, deprecation_matches, unmatched_models,
                            spreadsheet_id, scan=None, scrape_stats=None):
    """
    Export to Google Sheets with up to four tabs:
      - 'All Models'       : every model scraped from all provider pages
      - 'Interested Models': the models we track, with Projects + Usage columns
      - 'Model Usage'      : per-project config-vs-code breakdown (needs `scan`)
      - 'Bedrock Details'  : AWS Bedrock model-card metadata

    Args:
        all_deprecations:    Full list returned by parse_all_deprecations()
        deprecation_matches: Filtered list returned by check_my_models()
        spreadsheet_id:      Google Sheets ID (from the URL: /spreadsheets/d/<ID>/edit)
        scan:                Optional result from scanner.scan_projects(), used to
                             fill the Projects/Usage columns and the Model Usage tab
        scrape_stats:        Optional {provider: record_count} from
                             parse_all_deprecations(). A count of 0 means that
                             provider's scrape failed, so its rows are flagged
                             pink as last-known rather than current values.

    Setup:
        1. pip install gspread google-auth
        2. Enable Google Sheets API + Google Drive API in Google Cloud Console
        3. Create a service account, download credentials JSON
        4. Save as 'credentials.json' in the project root, or set
           GOOGLE_CREDENTIALS_FILE env var to its path
        5. Share the spreadsheet with the service account email (Editor access)
    """
    try:
        import gspread
        from google.oauth2.service_account import Credentials

        scope = [
            'https://spreadsheets.google.com/feeds',
            'https://www.googleapis.com/auth/drive',
        ]
        credentials_file = os.environ.get('GOOGLE_CREDENTIALS_FILE', 'credentials.json')
        creds = Credentials.from_service_account_file(credentials_file, scopes=scope)
        client = gspread.authorize(creds)

        melbourne_tz = pytz.timezone('Australia/Melbourne')
        last_updated = datetime.now(melbourne_tz).strftime('%Y-%m-%d %H:%M:%S %Z')

        spreadsheet = client.open_by_key(spreadsheet_id)

        # ── Sheet 1: All Models ──────────────────────────────────────────────
        # Columns A-J (indices 0-9); Risk Level = col G (index 6)
        all_sheet = _get_or_create_worksheet(spreadsheet, 'All Models', index=0)
        all_headers = [
            'Provider', 'Model', 'Lifecycle Stage',
            'Scraped Shutdown Date', 'Parsed Shutdown Date',
            'Days Remaining', 'Risk Level', 'Source URL',
            'First Seen', 'Last Seen',
        ]
        all_rows, all_colors = [], []
        for item in all_deprecations:
            parsed_date, days_remaining, risk_level, color = calculate_risk_info(item.get('shutdown_date', ''))
            all_rows.append([
                item.get('provider', ''),
                item.get('model', ''),
                item.get('lifecycle_stage', ''),
                item.get('shutdown_date', ''),
                parsed_date if days_remaining != 'N/A' else 'N/A',
                str(days_remaining),
                risk_level,
                item.get('source_url', ''),
                item.get('first_seen', ''),
                item.get('last_seen', ''),
            ])
            all_colors.append({6: color})  # colour the Risk Level cell
        _write_sheet(spreadsheet, all_sheet, all_headers, all_rows, all_colors,
                     last_col_index=9)
        print(f"  'All Models' sheet updated: {len(all_rows)} models across all providers")

        # ── Sheet 2: Interested Models ───────────────────────────────────────
        # Columns A-J (indices 0-9); Risk Level = col H (index 7)
        # 'Projects' and 'Usage' come from the repo scan so you can filter by
        # which product uses a model, and by whether it is actually used.
        usage = _usage_lookup(scan)
        interested_sheet = _get_or_create_worksheet(spreadsheet, 'Interested Models', index=1)
        interested_headers = [
            'Last Updated', 'Our Model', 'Scraped Model', 'Provider (scraped)',
            'Scraped Shutdown Date', 'Parsed Shutdown Date',
            'Days Remaining', 'Risk Level',
            'Projects', 'Provider (config)', 'Usage',
            'Data Warning', 'Source URL',
        ]
        today = datetime.now(melbourne_tz).strftime('%Y-%m-%d')
        scrape_stats = scrape_stats or {}
        # Providers whose scrape returned nothing this run — their models are all
        # showing last-known values.
        failed_providers = {p for p, n in scrape_stats.items() if n == 0}

        # Column indexes used for cell colouring
        _LAST_UPDATED_COL, _RISK_COL, _USAGE_COL, _WARNING_COL = 0, 7, 10, 11

        interested_rows, interested_colors = [], []
        stale_count = 0
        for row in deprecation_matches:
            parsed_date, days_remaining, risk_level, color = calculate_risk_info(row['Shutdown Date'])
            projects, providers, usage_status = usage.get(row['Our Model'], ('', '', ''))

            # Only say something when there IS something wrong. A warning column
            # that fires on every row is noise nobody reads, so the normal case
            # — the provider page still listed this model today — stays blank.
            seen = row.get('Last Seen', '')
            provider = row.get('Provider', '')
            if provider in failed_providers:
                warning = f'COULD NOT READ {provider} PAGE — date below is old, do not trust it'
                is_stale = True
            elif seen == today:
                warning = ''
                is_stale = False
            elif seen:
                warning = f'Provider page no longer lists this model — date unchanged since {seen}'
                is_stale = True
            else:
                warning = 'Never confirmed against a provider page'
                is_stale = True
            if is_stale:
                stale_count += 1

            interested_rows.append([
                last_updated,
                row['Our Model'],
                row['Scraped Model'],
                provider,
                row['Shutdown Date'],
                parsed_date if days_remaining != 'N/A' else 'N/A',
                str(days_remaining),
                risk_level,
                projects,
                providers,
                usage_status,
                warning,
                row.get('Source URL', ''),
            ])
            colors = {
                _RISK_COL: color,
                _USAGE_COL: _USAGE_COLORS.get(usage_status, _NEUTRAL_COLOR),
            }
            if is_stale:
                # Pink on both the timestamp and the reason, so a stale row is
                # obvious at a glance no matter which column you're reading.
                colors[_LAST_UPDATED_COL] = _STALE_COLOR
                colors[_WARNING_COL] = _STALE_COLOR
            interested_colors.append(colors)

        # Append unmatched models as grey rows so they're visible but clearly
        # unfound. 'Provider (config)' still tells us where they're hosted.
        _NOT_FOUND_COLOR = {'red': 0.88, 'green': 0.88, 'blue': 0.88}
        for model in unmatched_models:
            projects, providers, usage_status = usage.get(model, ('', '', ''))
            interested_rows.append([
                last_updated, model, '', '', 'Not found', 'N/A', 'N/A', 'Not found',
                projects, providers, usage_status,
                'Not listed on any provider page — no date available', '',
            ])
            interested_colors.append({
                _RISK_COL: _NOT_FOUND_COLOR,
                _USAGE_COL: _USAGE_COLORS.get(usage_status, _NEUTRAL_COLOR),
            })

        _write_sheet(spreadsheet, interested_sheet, interested_headers, interested_rows,
                     interested_colors, last_col_index=12)
        print(f"  'Interested Models' sheet updated: {len(deprecation_matches)} matched, {len(unmatched_models)} not found")
        if stale_count:
            print(f"  !! {stale_count} row(s) highlighted PINK — the provider did not")
            print(f"  !! re-confirm these on this run, so they are last-known values.")
            if failed_providers:
                print(f"  !! Scrape returned nothing for: {', '.join(sorted(failed_providers))}")

        # ── Sheet 3: Model Usage ─────────────────────────────────────────────
        # One row per model per project: is it declared, is it referenced, and
        # where. Only written when a scan was supplied.
        next_index = 2
        if scan and scan.get('rows'):
            usage_sheet = _get_or_create_worksheet(spreadsheet, 'Model Usage', index=next_index)
            next_index += 1
            usage_headers = [
                'Model', 'Project', 'Provider (config)', 'Declared In Config',
                'Referenced In Code', 'Usage', 'Evidence (file:line)',
            ]
            usage_rows, usage_colors = [], []
            for r in sorted(scan['rows'], key=lambda x: (x['model'], x['project'])):
                status = r.get('status', '')
                usage_rows.append([
                    r.get('model', ''),
                    r.get('project', ''),
                    r.get('provider', ''),
                    'Yes',  # every row here came from a project's model config
                    'No' if status == 'Config only' else 'Yes',
                    status,
                    r.get('evidence', ''),
                ])
                usage_colors.append({5: _USAGE_COLORS.get(status, _NEUTRAL_COLOR)})
            _write_sheet(spreadsheet, usage_sheet, usage_headers, usage_rows, usage_colors,
                         last_col_index=6)
            skipped = scan.get('skipped') or []
            note = f", {len(skipped)} project(s) skipped" if skipped else ''
            print(f"  'Model Usage' sheet updated: {len(usage_rows)} model/project rows{note}")

        # ── Sheet 4: Bedrock Details ─────────────────────────────────────────
        # Only rows from AWS Bedrock that have model-card metadata
        bedrock_rows_with_meta = [
            r for r in all_deprecations
            if r.get('provider') == 'AWS Bedrock' and r.get('model_card_url')
        ]
        if bedrock_rows_with_meta:
            details_sheet = _get_or_create_worksheet(spreadsheet, 'Bedrock Details', index=next_index)
            details_headers = [
                'Model ID', 'Lifecycle Stage',
                'Context Window', 'Max Output Tokens',
                'Input Modalities', 'Output Modalities',
                'Knowledge Cutoff', 'Geo Inference IDs',
                'Model Card URL',
            ]
            details_rows, details_colors = [], []
            _neutral = {'red': 1.0, 'green': 1.0, 'blue': 1.0}
            for item in bedrock_rows_with_meta:
                details_rows.append([
                    item.get('model', ''),
                    item.get('lifecycle_stage', ''),
                    item.get('context_window', ''),
                    item.get('max_output_tokens', ''),
                    ', '.join(item['input_modalities']) if item.get('input_modalities') else '',
                    ', '.join(item['output_modalities']) if item.get('output_modalities') else '',
                    item.get('knowledge_cutoff', ''),
                    ', '.join(item['geo_inference_ids']) if item.get('geo_inference_ids') else '',
                    item.get('model_card_url', ''),
                ])
                details_colors.append({})  # no cells need colouring on this tab
            _write_sheet(spreadsheet, details_sheet, details_headers, details_rows, details_colors,
                         last_col_index=8)
            print(f"  'Bedrock Details' sheet updated: {len(details_rows)} models with card metadata")

        print(f"\n  Successfully exported to Google Sheets!")
        print(f"  Last Updated: {last_updated}")
        print(f"  Risk Levels: EXPIRED | CRITICAL <=30d | HIGH <=90d | MEDIUM <=180d | LOW >180d")

    except ImportError as e:
        print("\n  Google Sheets export skipped: missing dependencies")
        print(f"  Install with: pip install gspread google-auth")
        print(f"  Error: {e}")
    except FileNotFoundError:
        print("\n  Google Sheets export skipped: credentials file not found")
        print("  Save your service account JSON as 'credentials.json', or set")
        print("  GOOGLE_CREDENTIALS_FILE to its path.")
    except Exception as e:
        print(f"\n  Google Sheets export failed: {e}")
