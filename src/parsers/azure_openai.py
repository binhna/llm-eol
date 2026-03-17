import pandas as pd
from io import StringIO
from utils import get_html

SOURCE_URL = 'https://learn.microsoft.com/en-us/azure/foundry/openai/concepts/model-retirements?tabs=text'


def parse_azure_openai():
    """Parse deprecation data from Azure OpenAI."""
    print("Parsing Azure OpenAI...")
    deprecations = []
    try:
        html = get_html(SOURCE_URL)
        dfs = pd.read_html(StringIO(html))
        for df in dfs:
            if 'Model Name' in df.columns and 'Retirement Date' in df.columns:
                for _, row in df.iterrows():
                    model = str(row['Model Name']).strip()
                    version = str(row.get('Model Version', row.get('Model Version1', ''))).strip()
                    date = str(row['Retirement Date']).strip()
                    if model and model.lower() != 'nan':
                        full_model = f"{model} ({version})" if version and version.lower() != 'nan' else model
                        deprecations.append({
                            'provider': 'Azure OpenAI',
                            'model': full_model,
                            'shutdown_date': date,
                            'source_url': SOURCE_URL,
                        })
    except Exception as e:
        print(f"  Failed to parse Azure OpenAI: {e}")
    return deprecations
