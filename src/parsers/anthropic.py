import pandas as pd
from io import StringIO
from utils import get_html, pick_column

SOURCE_URL = 'https://platform.claude.com/docs/en/about-claude/model-deprecations'


def parse_anthropic():
    """
    Parse deprecation data from Anthropic Claude.

    The page carries two shapes of table:
      - a model-state table    ('API model name' + 'Tentative retirement date')
      - past-retirement tables ('Deprecated model' + 'Retirement date')

    Column headings are matched case-insensitively via pick_column, because
    Anthropic has changed their capitalisation before and an exact-match lookup
    failed silently for months.
    """
    print("Parsing Anthropic Claude...")
    deprecations = []
    try:
        html = get_html(SOURCE_URL)
        dfs = pd.read_html(StringIO(html))
        for df in dfs:
            # Upcoming/tentative retirements
            model_col = pick_column(df, 'API model name', 'API Model Name', 'Model')
            date_col = pick_column(df, 'Tentative retirement date',
                                   'Tentative Retirement Date', 'Retirement date')
            # Already-retired tables use a different model column
            if model_col is None:
                model_col = pick_column(df, 'Deprecated model', 'Deprecated Model')
            if model_col is None or date_col is None:
                continue

            state_col = pick_column(df, 'Current state', 'Current State')

            for _, row in df.iterrows():
                model = str(row[model_col]).strip()
                date = str(row[date_col]).strip()
                if not model or model.lower() == 'nan':
                    continue
                record = {
                    'provider': 'Anthropic',
                    'model': model,
                    'shutdown_date': '' if date.lower() == 'nan' else date,
                    'source_url': SOURCE_URL,
                }
                if state_col:
                    state = str(row[state_col]).strip()
                    if state and state.lower() != 'nan':
                        record['lifecycle_stage'] = state
                deprecations.append(record)
    except Exception as e:
        print(f"  Failed to parse Anthropic: {e}")
    return deprecations
