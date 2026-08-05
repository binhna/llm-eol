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


def _write_sheet(spreadsheet, sheet, headers, rows, row_colors, last_col_index, risk_col_index):
    """
    Clear, write all data, and apply all formatting in a single batch_update call.
    Only the Risk Level cell is colored (not the whole row).
    Keeps API write requests to 3 per sheet regardless of row count, avoiding
    the 60-writes/min quota limit.
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

    # Risk Level cell only: color just that one column per row (skip if no risk column)
    if risk_col_index is not None:
        for i, color in enumerate(row_colors):
            row_idx = i + 1  # 0-based; row 0 is the header
            requests.append({
                'repeatCell': {
                    'range': {
                        'sheetId': sheet_id,
                        'startRowIndex': row_idx, 'endRowIndex': row_idx + 1,
                        'startColumnIndex': risk_col_index, 'endColumnIndex': risk_col_index + 1,
                    },
                    'cell': {'userEnteredFormat': {'backgroundColor': color}},
                    'fields': 'userEnteredFormat.backgroundColor',
                }
            })

    if requests:
        spreadsheet.batch_update({'requests': requests})


# Usage-status colours for the 'Model Usage' tab and the Usage column
_USAGE_COLORS = {
    'Used':            {'red': 0.76, 'green': 0.93, 'blue': 0.76},  # soft mint
    'Test/Experiment': {'red': 1.0,  'green': 0.97, 'blue': 0.78},  # pale yellow
    'Config only':     {'red': 0.88, 'green': 0.88, 'blue': 0.88},  # light grey
}
_NEUTRAL_COLOR = {'red': 1.0, 'green': 1.0, 'blue': 1.0}


def _usage_lookup(scan):
    """
    Build {model: (projects_string, usage_status)} from a scanner result.
    Returns an empty dict when no scan was supplied.
    """
    if not scan or not scan.get('models'):
        return {}
    out = {}
    for model, entry in scan['models'].items():
        projects = ', '.join(sorted(entry.get('declared_in', [])))
        out[model] = (projects, entry.get('status', ''))
    return out


def export_to_google_sheets(all_deprecations, deprecation_matches, unmatched_models,
                            spreadsheet_id, scan=None):
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
            all_colors.append(color)
        _write_sheet(spreadsheet, all_sheet, all_headers, all_rows, all_colors,
                     last_col_index=9, risk_col_index=6)
        print(f"  'All Models' sheet updated: {len(all_rows)} models across all providers")

        # ── Sheet 2: Interested Models ───────────────────────────────────────
        # Columns A-J (indices 0-9); Risk Level = col H (index 7)
        # 'Projects' and 'Usage' come from the repo scan so you can filter by
        # which product uses a model, and by whether it is actually used.
        usage = _usage_lookup(scan)
        interested_sheet = _get_or_create_worksheet(spreadsheet, 'Interested Models', index=1)
        interested_headers = [
            'Last Updated', 'Our Model', 'Scraped Model', 'Provider',
            'Scraped Shutdown Date', 'Parsed Shutdown Date',
            'Days Remaining', 'Risk Level', 'Projects', 'Usage',
        ]
        interested_rows, interested_colors = [], []
        for row in deprecation_matches:
            parsed_date, days_remaining, risk_level, color = calculate_risk_info(row['Shutdown Date'])
            projects, usage_status = usage.get(row['Our Model'], ('', ''))
            interested_rows.append([
                last_updated,
                row['Our Model'],
                row['Scraped Model'],
                row['Provider'],
                row['Shutdown Date'],
                parsed_date if days_remaining != 'N/A' else 'N/A',
                str(days_remaining),
                risk_level,
                projects,
                usage_status,
            ])
            interested_colors.append(color)
        # Append unmatched models as grey rows so they're visible but clearly unfound
        _NOT_FOUND_COLOR = {'red': 0.88, 'green': 0.88, 'blue': 0.88}
        for model in unmatched_models:
            projects, usage_status = usage.get(model, ('', ''))
            interested_rows.append([
                last_updated, model, '', '', 'Not found', 'N/A', 'N/A', 'Not found',
                projects, usage_status,
            ])
            interested_colors.append(_NOT_FOUND_COLOR)

        _write_sheet(spreadsheet, interested_sheet, interested_headers, interested_rows, interested_colors,
                     last_col_index=9, risk_col_index=7)
        print(f"  'Interested Models' sheet updated: {len(deprecation_matches)} matched, {len(unmatched_models)} not found")

        # ── Sheet 3: Model Usage ─────────────────────────────────────────────
        # One row per model per project: is it declared, is it referenced, and
        # where. Only written when a scan was supplied.
        next_index = 2
        if scan and scan.get('rows'):
            usage_sheet = _get_or_create_worksheet(spreadsheet, 'Model Usage', index=next_index)
            next_index += 1
            usage_headers = [
                'Model', 'Project', 'Declared In Config', 'Referenced In Code',
                'Usage', 'Evidence (file:line)',
            ]
            usage_rows, usage_colors = [], []
            for r in sorted(scan['rows'], key=lambda x: (x['model'], x['project'])):
                status = r.get('status', '')
                usage_rows.append([
                    r.get('model', ''),
                    r.get('project', ''),
                    'Yes',  # every row here came from a project's model config
                    'No' if status == 'Config only' else 'Yes',
                    status,
                    r.get('evidence', ''),
                ])
                usage_colors.append(_USAGE_COLORS.get(status, _NEUTRAL_COLOR))
            # Colour the Usage column (index 4)
            _write_sheet(spreadsheet, usage_sheet, usage_headers, usage_rows, usage_colors,
                         last_col_index=5, risk_col_index=4)
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
                details_colors.append(_neutral)
            # No risk column on this sheet — pass risk_col_index=None
            _write_sheet(spreadsheet, details_sheet, details_headers, details_rows, details_colors,
                         last_col_index=8, risk_col_index=None)
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
