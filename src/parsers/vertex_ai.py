import re
import warnings
from bs4 import BeautifulSoup
from utils import get_html

# Suppress BeautifulSoup warnings about parser choice
warnings.filterwarnings('ignore', category=UserWarning, module='bs4')

SOURCE_URL = 'https://docs.cloud.google.com/vertex-ai/generative-ai/docs/deprecations/partner-models'


def parse_vertex_ai():
    """Parse deprecation data from Vertex AI (natural text parsing)."""
    print("Parsing Vertex AI...")
    deprecations = []
    try:
        html = get_html(SOURCE_URL)
        soup = BeautifulSoup(html, 'html.parser')
        text = soup.get_text(separator=' ', strip=True)

        # Method 1: split by "is deprecated as of" for entries that include a shutdown date
        parts = re.split(r"is\s+deprecated\s+as\s+of", text, flags=re.IGNORECASE)
        for i in range(1, len(parts)):
            chunk = parts[i]
            date_match = re.search(r"and\s+will\s+be\s+shut\s+down\s+on\s+(.*?)\.", chunk, re.IGNORECASE)
            id_match = re.search(r"Model\s+ID\s+([a-zA-Z0-9\-\._]+)", chunk[:1000], re.IGNORECASE)
            if date_match and id_match:
                deprecations.append({
                    'provider': 'Vertex AI',
                    'model': id_match.group(1).strip(),
                    'shutdown_date': date_match.group(1).strip(),
                    'source_url': SOURCE_URL,
                })

        # Method 2: date followed by Model ID + "Launch stage deprecated"
        # Catches models that use a different doc format (e.g. claude-3-7-sonnet)
        date_pattern = (
            r"((?:January|February|March|April|May|June|July|August|"
            r"September|October|November|December)\s+\d+,\s+\d{4})\."
        )
        for date_match in re.finditer(date_pattern, text):
            date = date_match.group(1)
            chunk = text[date_match.end():date_match.end() + 1500]
            model_match = re.search(
                r"Model\s+ID\s+([a-zA-Z0-9\-\._]+)\s+Launch\s+stage\s+deprecated",
                chunk, re.IGNORECASE,
            )
            if model_match:
                model_id = model_match.group(1).strip()
                if not any(d['model'] == model_id and d['provider'] == 'Vertex AI' for d in deprecations):
                    deprecations.append({
                        'provider': 'Vertex AI',
                        'model': model_id,
                        'shutdown_date': date,
                        'source_url': SOURCE_URL,
                    })
    except Exception as e:
        print(f"  Failed to parse Vertex AI: {e}")
    return deprecations
