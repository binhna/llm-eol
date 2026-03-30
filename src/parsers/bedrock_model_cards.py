"""
Scrape rich metadata from AWS Bedrock individual model card pages.

Discovery path:
  model-cards.html
    └─ model-cards-{provider}.html   (one per provider)
         └─ model-card-{provider}-{model}.html  (one per model)

Metadata extracted per card:
  model_id, context_window, max_output_tokens,
  input_modalities, output_modalities,
  knowledge_cutoff, geo_inference_ids, model_card_url
"""
from __future__ import annotations

import re
from bs4 import BeautifulSoup
from utils import get_html

_BASE = 'https://docs.aws.amazon.com/bedrock/latest/userguide/'
_CARDS_INDEX = _BASE + 'model-cards.html'

# Matches provider-level index pages
_PROVIDER_PAGE_RE = re.compile(r'model-cards-[a-z][a-z0-9\-]*\.html')
# Matches individual model card pages
_MODEL_CARD_RE = re.compile(r'model-card-[a-z][a-z0-9\-]*\.html')


def _discover_card_urls() -> list[str]:
    """Walk the index hierarchy and return all individual model card URLs."""
    try:
        html = get_html(_CARDS_INDEX)
    except Exception as e:
        print(f"  [model cards] Failed to fetch index: {e}")
        return []

    soup = BeautifulSoup(html, 'html.parser')
    provider_pages = {
        a['href'].split('/')[-1]
        for a in soup.find_all('a', href=True)
        if _PROVIDER_PAGE_RE.search(a['href'])
    }

    card_urls = []
    for page in sorted(provider_pages):
        try:
            phtml = get_html(_BASE + page)
            psoup = BeautifulSoup(phtml, 'html.parser')
            for a in psoup.find_all('a', href=True):
                filename = a['href'].split('/')[-1].split('?')[0]
                if _MODEL_CARD_RE.match(filename) and filename not in {u.split('/')[-1] for u in card_urls}:
                    card_urls.append(_BASE + filename)
        except Exception as e:
            print(f"  [model cards] Failed to fetch provider page {page}: {e}")

    return card_urls


def _parse_card(url: str) -> dict | None:
    """
    Parse a single model card page and return a metadata dict, or None on failure.
    Uses text-search on the page body since the layout varies across providers.
    """
    try:
        html = get_html(url)
    except Exception as e:
        print(f"  [model cards] Failed to fetch {url}: {e}")
        return None

    soup = BeautifulSoup(html, 'html.parser')
    text = soup.get_text(separator='\n', strip=True)

    def _find_value(patterns: list[str]) -> str:
        """Return the first non-empty value found after any of the given label patterns."""
        for pattern in patterns:
            m = re.search(
                pattern + r'[\s:：]+([^\n]+)',
                text, re.IGNORECASE,
            )
            if m:
                return m.group(1).strip()
        return ''

    # Model ID — prefer code tags, fall back to text search
    model_id = ''
    for code in soup.find_all(['code', 'tt']):
        candidate = code.get_text(strip=True)
        # Bedrock model IDs follow provider.name-version:variant pattern
        if re.match(r'^[a-z][a-z0-9]+\.[a-z0-9][\w\-\.]+:[0-9]$', candidate, re.IGNORECASE):
            model_id = candidate
            break
    if not model_id:
        model_id = _find_value(['Model ID', 'ModelId'])

    if not model_id:
        return None  # Can't identify the model — skip

    # Numeric context window — strip commas and units like "K" or "tokens"
    raw_ctx = _find_value(['Context [Ww]indow', 'Context window'])
    context_window = None
    if raw_ctx:
        m = re.search(r'([\d,]+)\s*[Kk]?', raw_ctx)
        if m:
            digits = m.group(1).replace(',', '')
            multiplier = 1000 if re.search(r'\d\s*[Kk]', raw_ctx) else 1
            try:
                context_window = int(digits) * multiplier
            except ValueError:
                pass

    # Max output tokens
    raw_max = _find_value(['Max [Oo]utput [Tt]okens', 'Maximum output tokens'])
    max_output_tokens = None
    if raw_max:
        m = re.search(r'([\d,]+)\s*[Kk]?', raw_max)
        if m:
            digits = m.group(1).replace(',', '')
            multiplier = 1000 if re.search(r'\d\s*[Kk]', raw_max) else 1
            try:
                max_output_tokens = int(digits) * multiplier
            except ValueError:
                pass

    # Modalities — find the modalities table and read checkmarks
    input_modalities, output_modalities = [], []
    modality_names = ['Text', 'Image', 'Video', 'Audio', 'Speech', 'Embedding']
    for table in soup.find_all('table'):
        headers = [th.get_text(strip=True) for th in table.find_all('th')]
        if 'Input' in headers and 'Output' in headers:
            input_idx = headers.index('Input')
            output_idx = headers.index('Output')
            for row in table.find_all('tr')[1:]:
                cells = row.find_all('td')
                if not cells:
                    continue
                modality = cells[0].get_text(strip=True)
                if modality not in modality_names:
                    continue
                if input_idx < len(cells) and '✓' in cells[input_idx].get_text():
                    input_modalities.append(modality)
                if output_idx < len(cells) and '✓' in cells[output_idx].get_text():
                    output_modalities.append(modality)
            if input_modalities or output_modalities:
                break

    # Knowledge cutoff
    knowledge_cutoff = _find_value(['Knowledge [Cc]utoff', 'Training cutoff', 'Knowledge cutoff date'])

    # Geo inference IDs — look for lines containing provider prefix pattern
    geo_ids = list(dict.fromkeys(
        m.group(0)
        for m in re.finditer(
            r'(?:us|eu|ap|apac|au|ca|jp|us-gov)\.[\w\-]+\.[\w\-]+:[0-9]',
            text, re.IGNORECASE,
        )
    ))

    return {
        'model_id': model_id,
        'context_window': context_window,
        'max_output_tokens': max_output_tokens,
        'input_modalities': input_modalities or None,
        'output_modalities': output_modalities or None,
        'knowledge_cutoff': knowledge_cutoff or None,
        'geo_inference_ids': geo_ids or None,
        'model_card_url': url,
    }


def scrape_bedrock_model_cards() -> list[dict]:
    """
    Discover and scrape all Bedrock model card pages.
    Returns a list of metadata dicts (one per successfully parsed card).
    """
    print("Scraping AWS Bedrock model cards...")
    card_urls = _discover_card_urls()
    print(f"  Found {len(card_urls)} model card pages")

    results = []
    for url in card_urls:
        record = _parse_card(url)
        if record:
            results.append(record)

    print(f"  Parsed metadata for {len(results)} models")
    return results
