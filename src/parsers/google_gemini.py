import pandas as pd
from io import StringIO
from utils import get_html

SOURCE_URL = 'https://ai.google.dev/gemini-api/docs/deprecations'


def parse_google_gemini():
    """Parse deprecation data from Google Gemini API."""
    print("Parsing Google Gemini...")
    deprecations = []
    try:
        html = get_html(SOURCE_URL)
        dfs = pd.read_html(StringIO(html))
        for df in dfs:
            if 'Model' in df.columns and 'Shutdown date' in df.columns:
                for _, row in df.iterrows():
                    model = str(row['Model']).strip()
                    date = str(row['Shutdown date']).strip()
                    if model and model.lower() != 'nan' and not model.startswith('Preview models'):
                        deprecations.append({
                            'provider': 'Google Gemini',
                            'model': model,
                            'shutdown_date': date,
                            'source_url': SOURCE_URL,
                        })
    except Exception as e:
        print(f"  Failed to parse Gemini: {e}")
    return deprecations
