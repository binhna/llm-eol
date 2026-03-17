import re
import requests
import pytz
from datetime import datetime
from dateutil import parser as date_parser


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


def parse_shutdown_date(date_string):
    """
    Parse shutdown date string to datetime object.
    Returns None if parsing fails.

    Handles:
    - ISO dates:    "2026-03-31"
    - Month dates:  "July 7, 2026" / "Mar 1, 2026" / "January 15th, 2026"
    - With regions: "July 7, 2026 (us-west-2 and us-east-2 Regions)"
                    "October 4, 2024 (only in us-west-2)"
    - Multi-date:   "retires on 2026-03-31 ... moved to 2026-10-01"  (returns earliest)
    """
    # Step 1: strip parenthetical content — region qualifiers like
    # "(us-west-2 and us-east-2 Regions)" confuse the fuzzy parser because
    # region names contain numbers and dashes that look like date components.
    cleaned = re.sub(r'\(.*?\)', '', date_string).strip().rstrip(',').strip()

    # Step 2: try extracting all YYYY-MM-DD dates from the cleaned string
    # (handles Azure-style multi-date retirement strings)
    iso_matches = re.findall(r'\b(\d{4}[-–]\d{2}[-–]\d{2})\b', cleaned)
    if iso_matches:
        parsed_dates = []
        for match in iso_matches:
            normalized = match.replace('–', '-').replace('—', '-')
            try:
                parsed_dates.append(date_parser.parse(normalized))
            except (ValueError, OverflowError):
                continue
        if parsed_dates:
            return min(parsed_dates)

    # Step 3: try extracting a "Month Day, Year" style date explicitly
    # (e.g. "July 7, 2026", "Mar 1, 2026", "January 15th, 2026")
    month_match = re.search(
        r'\b(January|February|March|April|May|June|July|August|'
        r'September|October|November|December|'
        r'Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Oct|Nov|Dec)'
        r'\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4}\b',
        cleaned, re.IGNORECASE,
    )
    if month_match:
        try:
            return date_parser.parse(month_match.group(0))
        except (ValueError, OverflowError):
            pass

    # Step 4: fuzzy fallback on the cleaned string
    try:
        return date_parser.parse(cleaned, fuzzy=True)
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
        return (shutdown_date_str, 'N/A', 'Unknown', {'red': 1.0, 'green': 1.0, 'blue': 1.0})

    if parsed_date.tzinfo is None:
        parsed_date = melbourne_tz.localize(parsed_date)

    parsed_date = parsed_date.replace(hour=0, minute=0, second=0, microsecond=0)
    days_remaining = (parsed_date - current_date).days
    formatted_date = parsed_date.strftime('%Y-%m-%d')

    if days_remaining < 0:
        risk_level = 'EXPIRED'
        color = {'red': 0.93, 'green': 0.56, 'blue': 0.56}  # Muted rose
    elif days_remaining <= 30:
        risk_level = 'CRITICAL'
        color = {'red': 1.0, 'green': 0.70, 'blue': 0.48}   # Soft peach-orange
    elif days_remaining <= 90:
        risk_level = 'HIGH'
        color = {'red': 1.0, 'green': 0.90, 'blue': 0.45}   # Soft amber
    elif days_remaining <= 180:
        risk_level = 'MEDIUM'
        color = {'red': 1.0, 'green': 0.97, 'blue': 0.78}   # Pale yellow
    else:
        risk_level = 'LOW'
        color = {'red': 0.76, 'green': 0.93, 'blue': 0.76}  # Soft mint

    return (formatted_date, days_remaining, risk_level, color)
