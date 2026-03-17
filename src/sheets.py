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

    # Risk Level cell only: color just that one column per row
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


def export_to_google_sheets(all_deprecations, deprecation_matches, spreadsheet_id):
    """
    Export to Google Sheets with two tabs:
      - 'All Models'       : every model scraped from all provider pages
      - 'Interested Models': only models that matched your my_used_models list

    Args:
        all_deprecations:    Full list returned by parse_all_deprecations()
        deprecation_matches: Filtered list returned by check_my_models()
        spreadsheet_id:      Google Sheets ID (from the URL: /spreadsheets/d/<ID>/edit)

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
        # Columns A-H (indices 0-7); Risk Level = col G (index 6)
        all_sheet = _get_or_create_worksheet(spreadsheet, 'All Models', index=0)
        all_headers = [
            'Provider', 'Model', 'Lifecycle Stage',
            'Scraped Shutdown Date', 'Parsed Shutdown Date',
            'Days Remaining', 'Risk Level', 'Source URL',
        ]
        all_rows, all_colors = [], []
        for item in all_deprecations:
            parsed_date, days_remaining, risk_level, color = calculate_risk_info(item['shutdown_date'])
            all_rows.append([
                item['provider'],
                item['model'],
                item.get('lifecycle_stage', ''),
                item['shutdown_date'],
                parsed_date if days_remaining != 'N/A' else 'N/A',
                str(days_remaining),
                risk_level,
                item.get('source_url', ''),
            ])
            all_colors.append(color)
        _write_sheet(spreadsheet, all_sheet, all_headers, all_rows, all_colors,
                     last_col_index=7, risk_col_index=6)
        print(f"  'All Models' sheet updated: {len(all_rows)} models across all providers")

        # ── Sheet 2: Interested Models ───────────────────────────────────────
        # Columns A-H (indices 0-7); Risk Level = col H (index 7)
        interested_sheet = _get_or_create_worksheet(spreadsheet, 'Interested Models', index=1)
        interested_headers = [
            'Last Updated', 'Our Model', 'Scraped Model', 'Provider',
            'Scraped Shutdown Date', 'Parsed Shutdown Date',
            'Days Remaining', 'Risk Level',
        ]
        interested_rows, interested_colors = [], []
        for row in deprecation_matches:
            parsed_date, days_remaining, risk_level, color = calculate_risk_info(row['Shutdown Date'])
            interested_rows.append([
                last_updated,
                row['Our Model'],
                row['Scraped Model'],
                row['Provider'],
                row['Shutdown Date'],
                parsed_date if days_remaining != 'N/A' else 'N/A',
                str(days_remaining),
                risk_level,
            ])
            interested_colors.append(color)
        _write_sheet(spreadsheet, interested_sheet, interested_headers, interested_rows, interested_colors,
                     last_col_index=7, risk_col_index=7)
        print(f"  'Interested Models' sheet updated: {len(interested_rows)} matched model(s)")

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
