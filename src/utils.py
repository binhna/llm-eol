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

    # When a response has no charset in its Content-Type header, requests falls
    # back to ISO-8859-1 for text/*, which turns UTF-8 punctuation into
    # mojibake — an em dash "—" arrives as "â€”". Provider pages use em dashes
    # to mean "no retirement scheduled", so getting this wrong changes results.
    if 'charset' not in response.headers.get('Content-Type', '').lower():
        response.encoding = response.apparent_encoding or 'utf-8'

    return response.text


def pick_column(df, *candidates):
    """
    Find a column in a DataFrame, ignoring case and surrounding whitespace.

    Providers rewrite their table headings without warning — Anthropic silently
    changed "API Model Name" to "API model name", which broke an exact-match
    lookup and left four months of stale data behind. Matching loosely means a
    cosmetic edit no longer breaks the scrape.

    Returns the real column name, or None if none of the candidates are present.
    """
    normalised = {str(c).strip().lower(): c for c in df.columns}
    for name in candidates:
        hit = normalised.get(name.strip().lower())
        if hit is not None:
            return hit
    return None


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


# Phrases a provider uses to say "this model exists but we have not scheduled a
# retirement". That is a definite answer, not a gap in our data, so it gets its
# own label instead of being lumped in with genuine parse failures.
_NO_EOL_PHRASES = (
    'no shutdown date',
    'no retirement',
    'not scheduled',
    'no eol',
    'tbd',
)


def calculate_risk_info(shutdown_date_str):
    """
    Calculate days remaining and risk level based on shutdown date.

    Risk levels for the non-date cases:
      'No EOL announced' — the provider lists the model but gives no date, or
                           explicitly says none is scheduled. Nothing to do yet.
      'Unknown'          — there IS a date string but we could not read it.
                           That is a parsing gap worth looking at.

    Returns:
        tuple: (parsed_date_str, days_remaining, risk_level, color_dict)
    """
    melbourne_tz = pytz.timezone('Australia/Melbourne')
    current_date = datetime.now(melbourne_tz).replace(hour=0, minute=0, second=0, microsecond=0)

    raw = (shutdown_date_str or '').strip()
    lowered = raw.lower()

    # Provider explicitly gives no date
    if not raw or any(p in lowered for p in _NO_EOL_PHRASES):
        return (raw or 'None announced', 'N/A', 'No EOL announced',
                {'red': 0.85, 'green': 0.92, 'blue': 0.98})  # pale blue

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
