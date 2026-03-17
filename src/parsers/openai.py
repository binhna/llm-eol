import pandas as pd
from io import StringIO
from utils import get_html

SOURCE_URL = 'https://developers.openai.com/api/docs/deprecations/'


def parse_openai():
    """Parse deprecation data from OpenAI API."""
    print("Parsing OpenAI...")
    deprecations = []
    try:
        html = get_html(SOURCE_URL)
        dfs = pd.read_html(StringIO(html))
        for df in dfs:
            model_col = next(
                (c for c in ['Model / system', 'Deprecated model', 'Legacy model', 'System']
                 if c in df.columns),
                None,
            )
            if model_col and 'Shutdown date' in df.columns:
                for _, row in df.iterrows():
                    model = str(row[model_col]).strip()
                    date = str(row['Shutdown date']).strip()
                    if model and model.lower() != 'nan':
                        deprecations.append({
                            'provider': 'OpenAI',
                            'model': model,
                            'shutdown_date': date,
                            'source_url': SOURCE_URL,
                        })
    except Exception as e:
        print(f"  Failed to parse OpenAI: {e}")
    return deprecations
