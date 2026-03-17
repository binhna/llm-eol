import pandas as pd
from io import StringIO
from utils import get_html

SOURCE_URL = 'https://platform.claude.com/docs/en/about-claude/model-deprecations'


def parse_anthropic():
    """Parse deprecation data from Anthropic Claude."""
    print("Parsing Anthropic Claude...")
    deprecations = []
    try:
        html = get_html(SOURCE_URL)
        dfs = pd.read_html(StringIO(html))
        for df in dfs:
            if 'API Model Name' in df.columns and 'Tentative Retirement Date' in df.columns:
                for _, row in df.iterrows():
                    model = str(row['API Model Name']).strip()
                    date = str(row['Tentative Retirement Date']).strip()
                    if model and model.lower() != 'nan':
                        deprecations.append({
                            'provider': 'Anthropic',
                            'model': model,
                            'shutdown_date': date,
                            'source_url': SOURCE_URL,
                        })
            elif 'Deprecated Model' in df.columns and 'Retirement Date' in df.columns:
                for _, row in df.iterrows():
                    model = str(row['Deprecated Model']).strip()
                    date = str(row['Retirement Date']).strip()
                    if model and model.lower() != 'nan':
                        deprecations.append({
                            'provider': 'Anthropic',
                            'model': model,
                            'shutdown_date': date,
                            'source_url': SOURCE_URL,
                        })
    except Exception as e:
        print(f"  Failed to parse Anthropic: {e}")
    return deprecations
