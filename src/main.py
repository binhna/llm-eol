import os
import pandas as pd
import requests
from bs4 import BeautifulSoup
import re
import warnings
from io import StringIO
from datetime import datetime, timedelta
import pytz
from dateutil import parser as date_parser

# Suppress BeautifulSoup warnings about parser choice
warnings.filterwarnings('ignore', category=UserWarning, module='bs4')

# Regex that matches AWS Bedrock cross-region inference prefixes such as
# us., eu., ap., apac., au., ca., jp., global., us-gov.
_BEDROCK_GEO_PREFIX_RE = re.compile(
    r'^(?:global|us-gov|us|eu|ap|apac|au|ca|jp)\.',
    re.IGNORECASE,
)

def get_html(url):
    """Fetch HTML content with a standard User-Agent to prevent basic blocking."""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                      'AppleWebKit/537.36 (KHTML, like Gecko) '
                      'Chrome/120.0.0.0 Safari/537.36'
    }
    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()
    return response.text

def parse_google_gemini():
    """Parse deprecation data from Google Gemini API."""
    print("Parsing Google Gemini...")
    deprecations = []
    try:
        html = get_html("https://ai.google.dev/gemini-api/docs/deprecations")
        dfs = pd.read_html(StringIO(html))
        for df in dfs:
            if 'Model' in df.columns and 'Shutdown date' in df.columns:
                for _, row in df.iterrows():
                    model = str(row['Model']).strip()
                    date = str(row['Shutdown date']).strip()
                    if model and model.lower() != 'nan' and not model.startswith('Preview models'):
                        deprecations.append({'provider': 'Google Gemini', 'model': model, 'shutdown_date': date, 'source_url': 'https://ai.google.dev/gemini-api/docs/deprecations'})
    except Exception as e:
        print(f"  Failed to parse Gemini: {e}")
    return deprecations


def parse_openai():
    """Parse deprecation data from OpenAI API."""
    print("Parsing OpenAI...")
    deprecations = []
    try:
        html = get_html("https://developers.openai.com/api/docs/deprecations/")
        dfs = pd.read_html(StringIO(html))
        for df in dfs:
            model_col = next((c for c in ['Model / system', 'Deprecated model', 'Legacy model', 'System'] if c in df.columns), None)
            if model_col and 'Shutdown date' in df.columns:
                for _, row in df.iterrows():
                    model = str(row[model_col]).strip()
                    date = str(row['Shutdown date']).strip()
                    if model and model.lower() != 'nan':
                        deprecations.append({'provider': 'OpenAI', 'model': model, 'shutdown_date': date, 'source_url': 'https://developers.openai.com/api/docs/deprecations/'})
    except Exception as e:
        print(f"  Failed to parse OpenAI: {e}")
    return deprecations


def parse_azure_openai():
    """Parse deprecation data from Azure OpenAI."""
    print("Parsing Azure OpenAI...")
    deprecations = []
    try:
        html = get_html("https://learn.microsoft.com/en-us/azure/foundry/openai/concepts/model-retirements?tabs=text")
        dfs = pd.read_html(StringIO(html))
        for df in dfs:
            if 'Model Name' in df.columns and 'Retirement Date' in df.columns:
                for _, row in df.iterrows():
                    model = str(row['Model Name']).strip()
                    version = str(row.get('Model Version', row.get('Model Version1', ''))).strip()
                    date = str(row['Retirement Date']).strip()
                    
                    if model and model.lower() != 'nan':
                        full_model = f"{model} ({version})" if version and version.lower() != 'nan' else model
                        deprecations.append({'provider': 'Azure OpenAI', 'model': full_model, 'shutdown_date': date, 'source_url': 'https://learn.microsoft.com/en-us/azure/foundry/openai/concepts/model-retirements?tabs=text'})
    except Exception as e:
        print(f"  Failed to parse Azure OpenAI: {e}")
    return deprecations


def parse_anthropic():
    """Parse deprecation data from Anthropic Claude."""
    print("Parsing Anthropic Claude...")
    deprecations = []
    try:
        html = get_html("https://platform.claude.com/docs/en/about-claude/model-deprecations")
        dfs = pd.read_html(StringIO(html))
        for df in dfs:
            if 'API Model Name' in df.columns and 'Tentative Retirement Date' in df.columns:
                for _, row in df.iterrows():
                    model = str(row['API Model Name']).strip()
                    date = str(row['Tentative Retirement Date']).strip()
                    if model and model.lower() != 'nan':
                        deprecations.append({'provider': 'Anthropic', 'model': model, 'shutdown_date': date, 'source_url': 'https://platform.claude.com/docs/en/about-claude/model-deprecations'})
            elif 'Deprecated Model' in df.columns and 'Retirement Date' in df.columns:
                for _, row in df.iterrows():
                    model = str(row['Deprecated Model']).strip()
                    date = str(row['Retirement Date']).strip()
                    if model and model.lower() != 'nan':
                        deprecations.append({'provider': 'Anthropic', 'model': model, 'shutdown_date': date, 'source_url': 'https://platform.claude.com/docs/en/about-claude/model-deprecations'})
    except Exception as e:
        print(f"  Failed to parse Anthropic: {e}")
    return deprecations


def parse_vertex_ai():
    """Parse deprecation data from Vertex AI (natural text parsing)."""
    print("Parsing Vertex AI...")
    deprecations = []
    try:
        html = get_html("https://docs.cloud.google.com/vertex-ai/generative-ai/docs/deprecations/partner-models")
        soup = BeautifulSoup(html, 'html.parser')
        
        # Extract all text, separating elements with a space
        text = soup.get_text(separator=' ', strip=True)
        
        # Method 1: Split by "is deprecated as of" for standard deprecations with shutdown dates
        parts = re.split(r"is\s+deprecated\s+as\s+of", text, flags=re.IGNORECASE)
        
        for i in range(1, len(parts)):
            chunk = parts[i]
            
            # 1. Grab the shutdown date directly following the split phrase
            date_match = re.search(r"and\s+will\s+be\s+shut\s+down\s+on\s+(.*?)\.", chunk, re.IGNORECASE)
            
            # 2. Grab the actual 'Model ID' listed shortly below the intro paragraph
            # We restrict the search to the first 1000 characters so we don't accidentally grab a different model's ID.
            chunk_start = chunk[:1000]
            id_match = re.search(r"Model\s+ID\s+([a-zA-Z0-9\-\._]+)", chunk_start, re.IGNORECASE)
            
            if date_match and id_match:
                date = date_match.group(1).strip()
                model_id = id_match.group(1).strip()
                deprecations.append({'provider': 'Vertex AI', 'model': model_id, 'shutdown_date': date, 'source_url': 'https://docs.cloud.google.com/vertex-ai/generative-ai/docs/deprecations/partner-models'})
        
        # Method 2: Look for models with "Launch stage" showing "deprecated"
        # This catches models like claude-3-7-sonnet that use a different format
        # Pattern: Date at start, then look for Model ID and Launch stage deprecated within next 1000 chars
        date_pattern = r"((?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d+,\s+\d{4})\."
        for date_match in re.finditer(date_pattern, text):
            date = date_match.group(1)
            # Look ahead in the text after this date
            start_pos = date_match.end()
            chunk = text[start_pos:start_pos + 1500]
            
            # Check if this chunk has Model ID followed by Launch stage deprecated
            model_match = re.search(r"Model\s+ID\s+([a-zA-Z0-9\-\._]+)\s+Launch\s+stage\s+deprecated", chunk, re.IGNORECASE)
            if model_match:
                model_id = model_match.group(1).strip()
                # Avoid duplicates
                if not any(d['model'] == model_id and d['provider'] == 'Vertex AI' for d in deprecations):
                    deprecations.append({'provider': 'Vertex AI', 'model': model_id, 'shutdown_date': date, 'source_url': 'https://docs.cloud.google.com/vertex-ai/generative-ai/docs/deprecations/partner-models'})
    except Exception as e:
        print(f"  Failed to parse Vertex AI: {e}")
    return deprecations


def parse_bedrock():
    """
    Parse model lifecycle data from AWS Bedrock.

    Covers three lifecycle sections from the same page:
      - Active   : uses the 'Model ID' column; EOL dates are "No sooner than X" — the
                   prefix is stripped so the date parses correctly.
      - Legacy   : uses the 'Model version' name (no API ID available); EOL dates are
                   concrete. The same model can appear multiple times for different regions,
                   so we keep only the earliest EOL date.
      - EOL      : same structure as Legacy.
    """
    print("Parsing AWS Bedrock...")
    source_url = 'https://docs.aws.amazon.com/bedrock/latest/userguide/model-lifecycle.html'
    # model identifier -> earliest EOL date string found so far
    earliest: dict = {}

    def _keep_earliest(model, date_str):
        if not model or model.lower() == 'nan':
            return
        if model not in earliest:
            earliest[model] = date_str
            return
        existing = parse_shutdown_date(earliest[model])
        new = parse_shutdown_date(date_str)
        if existing and new and new < existing:
            earliest[model] = date_str

    try:
        html = get_html(source_url)
        dfs = pd.read_html(StringIO(html))

        for df in dfs:
            df.columns = [str(c).strip() for c in df.columns]

            # ── Active models table (has a "Model ID" column) ──────────────
            if 'Model ID' in df.columns and 'EOL date' in df.columns:
                for _, row in df.iterrows():
                    model_id = str(row['Model ID']).strip()
                    eol = str(row['EOL date']).strip()
                    # Strip "No sooner than " prefix
                    m = re.match(r'no sooner than\s+(.*)', eol, re.IGNORECASE)
                    if m:
                        date_part = m.group(1).strip()
                        # Skip "No sooner than launch date + 1 year" style — no concrete date
                        if re.search(r'launch date', date_part, re.IGNORECASE):
                            continue
                        eol = date_part
                    _keep_earliest(model_id, eol)

            # ── Legacy / EOL tables (have a "Model version" column) ────────
            elif 'Model version' in df.columns and 'EOL date' in df.columns:
                for _, row in df.iterrows():
                    model_name = str(row['Model version']).strip()
                    eol = str(row['EOL date']).strip()
                    _keep_earliest(model_name, eol)

    except Exception as e:
        print(f"  Failed to parse AWS Bedrock: {e}")
        return []

    return [
        {'provider': 'AWS Bedrock', 'model': model, 'shutdown_date': date, 'source_url': source_url}
        for model, date in earliest.items()
    ]


def parse_all_deprecations():
    """
    Parse deprecation data from all supported providers.
    
    Returns:
        list: Deduplicated list of deprecation records.
    """
    # Collect deprecations from all providers
    all_deprecations = []
    all_deprecations.extend(parse_google_gemini())
    all_deprecations.extend(parse_openai())
    all_deprecations.extend(parse_azure_openai())
    all_deprecations.extend(parse_anthropic())
    all_deprecations.extend(parse_vertex_ai())
    all_deprecations.extend(parse_bedrock())
    
    # Deduplicate the results
    seen = set()
    unique_deprecations = []
    for item in all_deprecations:
        key = (item['provider'], item['model'])
        if key not in seen:
            seen.add(key)
            unique_deprecations.append(item)

    return unique_deprecations


def check_my_models(my_models, deprecation_data):
    """Loops over your application models to find if they are deprecated."""
    print("\n" + "="*80)
    print(" MODEL DEPRECATION CHECK REPORT")
    print("="*80)
    
    deprecation_matches = []
    
    for user_model in my_models:
        user_model_lower = user_model.lower()
        
        for data in deprecation_data:
            scraped_model_lower = data['model'].lower()
            
            # 1. Exact match
            is_match = (user_model_lower == scraped_model_lower)
            
            # 2. Scraped model has appended dates/info (e.g. user: 'gpt-4o', scraped: 'gpt-4o (2024-05-13)')
            if not is_match:
                is_match = scraped_model_lower.startswith(user_model_lower + " ") or \
                           scraped_model_lower.startswith(user_model_lower + " (")
                           
            # 3. User model has appended version tags (e.g. user: 'claude-3-haiku@20240307', scraped 'claude-3-haiku')
            if not is_match:
                is_match = user_model_lower.startswith(scraped_model_lower + "@") or \
                           user_model_lower.startswith(scraped_model_lower + "-")

            # 4. Bedrock cross-region inference prefix (e.g. user: 'us.meta.llama3-3-70b-instruct-v1:0',
            #    scraped: 'meta.llama3-3-70b-instruct-v1:0'). Strip the geo prefix and re-apply rules 1–3.
            if not is_match and data['provider'] == 'AWS Bedrock':
                stripped = _BEDROCK_GEO_PREFIX_RE.sub('', user_model_lower)
                if stripped != user_model_lower:
                    is_match = (
                        stripped == scraped_model_lower
                        or scraped_model_lower.startswith(stripped + " ")
                        or scraped_model_lower.startswith(stripped + " (")
                        or stripped.startswith(scraped_model_lower + "@")
                        or stripped.startswith(scraped_model_lower + "-")
                    )

            if is_match:
                deprecation_matches.append({
                    'Our Model': user_model,
                    'Scraped Model': data['model'],
                    'Provider': data['provider'],
                    'Shutdown Date': data['shutdown_date']
                })
                
    if deprecation_matches:
        print("\n⚠️  DEPRECATED MODELS FOUND:\n")
        
        # Add risk analysis to each row
        for row in deprecation_matches:
            parsed_date, days_remaining, risk_level, _ = calculate_risk_info(row['Shutdown Date'])
            row['Days Remaining'] = days_remaining
            row['Risk Level'] = risk_level
        
        # Create a simple formatted table
        col_widths = {
            'Our Model': max(25, max(len(str(row['Our Model'])) for row in deprecation_matches)),
            'Scraped Model': max(25, max(len(str(row['Scraped Model'])) for row in deprecation_matches)),
            'Provider': max(15, max(len(str(row['Provider'])) for row in deprecation_matches)),
            'Shutdown Date': 35,
            'Days Left': 10,
            'Risk': 9
        }
        
        # Print header
        header = f"{'Our Model':<{col_widths['Our Model']}} | {'Scraped Model':<{col_widths['Scraped Model']}} | {'Provider':<{col_widths['Provider']}} | {'Shutdown Date':<{col_widths['Shutdown Date']}} | {'Days Left':<{col_widths['Days Left']}} | {'Risk':<{col_widths['Risk']}}"
        print(header)
        print("-" * len(header))
        
        # Print rows
        for row in deprecation_matches:
            shutdown_date = str(row['Shutdown Date'])
            # Truncate long shutdown dates and add ellipsis
            if len(shutdown_date) > col_widths['Shutdown Date']:
                shutdown_date = shutdown_date[:col_widths['Shutdown Date']-3] + '...'
            
            days_remaining = str(row['Days Remaining'])
            risk_level = str(row['Risk Level'])
            
            print(f"{row['Our Model']:<{col_widths['Our Model']}} | {row['Scraped Model']:<{col_widths['Scraped Model']}} | {row['Provider']:<{col_widths['Provider']}} | {shutdown_date:<{col_widths['Shutdown Date']}} | {days_remaining:<{col_widths['Days Left']}} | {risk_level:<{col_widths['Risk']}}")
        
        print()
    else:
        print("\n✅ Awesome! None of Our Models appear to be deprecated right now.\n")
    
    return deprecation_matches


def parse_shutdown_date(date_string):
    """
    Parse shutdown date string to datetime object.
    Returns None if parsing fails.

    Handles complex multi-date strings like:
    "Standard deployment type retires on 2026-03-31, with auto-upgrades
     scheduled to start on 2026-03-09. For other deployment types,
     including ALL Provisioned, Global Standard, and Data Zone Standard,
     the retirement date has been moved to 2026-10-01."
    """
    # Handle complex Azure-style retirement strings with multiple dates
    # Extract all dates that look like YYYY-MM-DD
    date_pattern = r'\b(\d{4}[-–]\d{2}[-–]\d{2})\b'
    matches = re.findall(date_pattern, date_string)

    if matches:
        # Parse all found dates and return the earliest one
        parsed_dates = []
        for match in matches:
            # Normalize en-dash or em-dash to hyphen
            normalized_date = match.replace('–', '-').replace('—', '-')
            try:
                parsed = date_parser.parse(normalized_date)
                parsed_dates.append(parsed)
            except (ValueError, OverflowError):
                continue

        if parsed_dates:
            return min(parsed_dates)  # Return earliest date

    # Fallback to fuzzy parsing for simpler date strings
    try:
        parsed_date = date_parser.parse(date_string, fuzzy=True)
        return parsed_date
    except (ValueError, OverflowError):
        return None


def calculate_risk_info(shutdown_date_str):
    """
    Calculate days remaining and risk level based on shutdown date.
    
    Returns:
        tuple: (parsed_date_str, days_remaining, risk_level, color_dict)
    """
    melbourne_tz = pytz.timezone('Australia/Melbourne')
    current_date = datetime.now(melbourne_tz).replace(hour=0, minute=0, second=0, microsecond=0)
    
    parsed_date = parse_shutdown_date(shutdown_date_str)
    
    if parsed_date is None:
        # Could not parse date
        return (shutdown_date_str, 'N/A', 'Unknown', {'red': 1.0, 'green': 1.0, 'blue': 1.0})
    
    # Make parsed_date timezone-aware
    if parsed_date.tzinfo is None:
        parsed_date = melbourne_tz.localize(parsed_date)
    
    parsed_date = parsed_date.replace(hour=0, minute=0, second=0, microsecond=0)
    days_remaining = (parsed_date - current_date).days
    
    # Format the parsed date
    formatted_date = parsed_date.strftime('%Y-%m-%d')
    
    # Determine risk level and color
    if days_remaining < 0:
        risk_level = 'EXPIRED'
        color = {'red': 0.93, 'green': 0.56, 'blue': 0.56}  # Muted rose
    elif days_remaining <= 30:
        risk_level = 'CRITICAL'
        color = {'red': 1.0, 'green': 0.70, 'blue': 0.48}  # Soft peach-orange
    elif days_remaining <= 90:
        risk_level = 'HIGH'
        color = {'red': 1.0, 'green': 0.90, 'blue': 0.45}  # Soft amber
    elif days_remaining <= 180:
        risk_level = 'MEDIUM'
        color = {'red': 1.0, 'green': 0.97, 'blue': 0.78}  # Pale yellow
    else:
        risk_level = 'LOW'
        color = {'red': 0.76, 'green': 0.93, 'blue': 0.76}  # Soft mint
    
    return (formatted_date, days_remaining, risk_level, color)



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
    This keeps API write requests to 3 per sheet regardless of row count, avoiding
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
            'fields': 'userEnteredFormat.backgroundColor,'
                      'userEnteredFormat.textFormat.bold,'
                      'userEnteredFormat.textFormat.foregroundColor',
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


def export_to_google_sheets(all_deprecations, deprecation_matches, spreadsheet_name='LLM Deprecation Monitoring'):
    """
    Export to Google Sheets with two tabs:
      - 'All Models'       : every model scraped from all provider pages
      - 'Interested Models': only models that matched your my_used_models list

    Args:
        all_deprecations:   Full list of scraped deprecation records (from parse_all_deprecations)
        deprecation_matches: Filtered list of records matching my_used_models (from check_my_models)
        spreadsheet_name:   Name of the Google Sheet to create/update

    Setup Instructions:
        1. Install required packages: pip install gspread google-auth python-dateutil
        2. Enable Google Sheets API at https://console.cloud.google.com/
        3. Create a service account and download credentials JSON
        4. Save credentials as 'credentials.json' in the project root (or set
           GOOGLE_CREDENTIALS_FILE env var to point to the file)
        5. Share your Google Sheet with the service account email
    """
    try:
        import gspread
        from google.oauth2.service_account import Credentials

        scope = ['https://spreadsheets.google.com/feeds',
                 'https://www.googleapis.com/auth/drive']

        credentials_file = os.environ.get('GOOGLE_CREDENTIALS_FILE', 'credentials.json')
        creds = Credentials.from_service_account_file(credentials_file, scopes=scope)
        client = gspread.authorize(creds)

        melbourne_tz = pytz.timezone('Australia/Melbourne')
        last_updated = datetime.now(melbourne_tz).strftime('%Y-%m-%d %H:%M:%S %Z')

        # Open or create the spreadsheet
        is_new_spreadsheet = False
        try:
            spreadsheet = client.open(spreadsheet_name)
        except gspread.SpreadsheetNotFound:
            spreadsheet = client.create(spreadsheet_name)
            is_new_spreadsheet = True
            print(f"  Created new spreadsheet: {spreadsheet_name}")
            print(f"  Share it at: {spreadsheet.url}")

        # ── Sheet 1: All Models ──────────────────────────────────────────────
        all_sheet = _get_or_create_worksheet(spreadsheet, 'All Models', index=0)

        all_headers = ['Provider', 'Model', 'Scraped Shutdown Date',
                       'Parsed Shutdown Date', 'Days Remaining', 'Risk Level', 'Source URL']
        all_rows = []
        all_colors = []

        for item in all_deprecations:
            parsed_date, days_remaining, risk_level, color = calculate_risk_info(item['shutdown_date'])
            parsed_display = parsed_date if days_remaining != 'N/A' else 'N/A'
            all_rows.append([
                item['provider'],
                item['model'],
                item['shutdown_date'],
                parsed_display,
                str(days_remaining),
                risk_level,
                item.get('source_url', '')
            ])
            all_colors.append(color)

        _write_sheet(spreadsheet, all_sheet, all_headers, all_rows, all_colors, last_col_index=6, risk_col_index=5)
        print(f"  'All Models' sheet updated: {len(all_rows)} models across all providers")

        # ── Sheet 2: Interested Models ───────────────────────────────────────
        interested_sheet = _get_or_create_worksheet(spreadsheet, 'Interested Models', index=1)

        interested_headers = ['Last Updated', 'Our Model', 'Scraped Model', 'Provider',
                               'Scraped Shutdown Date', 'Parsed Shutdown Date', 'Days Remaining', 'Risk Level']
        interested_rows = []
        interested_colors = []

        for row in deprecation_matches:
            parsed_date, days_remaining, risk_level, color = calculate_risk_info(row['Shutdown Date'])
            parsed_display = parsed_date if days_remaining != 'N/A' else 'N/A'
            interested_rows.append([
                last_updated,
                row['Our Model'],
                row['Scraped Model'],
                row['Provider'],
                row['Shutdown Date'],
                parsed_display,
                str(days_remaining),
                risk_level
            ])
            interested_colors.append(color)

        _write_sheet(spreadsheet, interested_sheet, interested_headers, interested_rows, interested_colors,
                     last_col_index=7, risk_col_index=7)
        print(f"  'Interested Models' sheet updated: {len(interested_rows)} matched model(s)")

        # Remove the default 'Sheet1' tab that gspread creates on new spreadsheets
        if is_new_spreadsheet:
            try:
                default_sheet = spreadsheet.worksheet('Sheet1')
                spreadsheet.del_worksheet(default_sheet)
            except gspread.WorksheetNotFound:
                pass

        print(f"\n  Successfully exported to Google Sheets!")
        print(f"  Last Updated: {last_updated}")
        print(f"  Risk Levels: EXPIRED (dark red) | CRITICAL <=30d (orange) | HIGH <=90d (yellow) | MEDIUM <=180d (light yellow) | LOW >180d (light green)")

    except ImportError as e:
        print("\n  Google Sheets export skipped: Missing dependencies")
        print(f"  Install with: pip install gspread google-auth python-dateutil")
        print(f"  Error: {e}")
    except FileNotFoundError:
        print("\n  Google Sheets export skipped: credentials file not found")
        print("  Save your service account JSON as 'credentials.json', or set the")
        print("  GOOGLE_CREDENTIALS_FILE environment variable to its path.")
        print("  Setup instructions: https://docs.gspread.org/en/latest/oauth2.html")
    except Exception as e:
        print(f"\n  Google Sheets export failed: {e}")



# ==========================================
# Example Usage
# ==========================================
if __name__ == "__main__":
    
    # Put the list of models you are currently utilizing here:
    my_used_models =[
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
        "qwen.qwen3-235b-a22b-2507-v1:0"
    ]
    
    # 1. Parse all the websites
    structured_deprecations = parse_all_deprecations()
    
    # 2. Match against Our Models
    matches = check_my_models(my_used_models, structured_deprecations)
    
    # 3. Export to Google Sheets — always export so 'All Models' is kept current
    export_to_google_sheets(structured_deprecations, matches)