import re
import pandas as pd
from io import StringIO
from utils import get_html, pick_column

# Retirement dates for Google's own models (Gemini, Imagen, Veo, embeddings) as
# served through Vertex AI / Gemini Enterprise Agent Platform — the platform our
# products call, so it wins over the Gemini API page for 'google' models.
SOURCE_URL = 'https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/model-versions'
PROVIDER = 'Vertex AI (Gemini)'

# Google writes an earliest-possible date as "May 19, 2027 or later". Rewrite it
# to the "No sooner than" wording the risk calculation already understands.
_OR_LATER_RE = re.compile(r'^(.*?)\s+or later\s*$', re.IGNORECASE)


def parse_vertex_gemini():
    print("Parsing Vertex AI Gemini model versions...")
    records = []
    try:
        for df in pd.read_html(StringIO(get_html(SOURCE_URL))):
            model_col = pick_column(df, 'Model ID')
            date_col = pick_column(df, 'Retirement date')
            if model_col is None or date_col is None:
                continue
            for _, row in df.iterrows():
                model = str(row[model_col]).strip()
                date = str(row[date_col]).strip()
                if not model or model.lower() == 'nan':
                    continue
                if date.lower() == 'nan':
                    date = ''
                m = _OR_LATER_RE.match(date)
                if m:
                    date = f'No sooner than {m.group(1)}'
                records.append({'provider': PROVIDER, 'model': model,
                                'shutdown_date': date, 'source_url': SOURCE_URL})
    except Exception as e:
        print(f"  Failed to parse Vertex AI Gemini: {e}")
    return records
