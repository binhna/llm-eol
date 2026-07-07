"""
Scrape AWS Bedrock model cards — the authoritative Bedrock source.

AWS consolidated everything onto a single index page:
    model-cards.html  → a flat "Models at a glance" table that links every
                        individual card:  model-card-{provider}-{model}.html

Each card exposes, in a "Model Details" bullet list and a "Programmatic
Access" table, everything we need to *track* the model (not merely annotate
an existing record):
    - base model ID + geo / global cross-region inference IDs
    - Model EOL date   (drives deprecation risk; "N/A" = no shutdown scheduled)
    - Model lifecycle  (Active / Legacy / EOL)
    - context window, max output tokens, knowledge cutoff, modalities

The separate model-lifecycle page only lists models that already have an EOL
date, so brand-new active models never appeared there and matched nothing.
Parsing the cards fixes that: every model on the index becomes a record.
"""
from __future__ import annotations

import re
from bs4 import BeautifulSoup
from utils import get_html

_BASE = 'https://docs.aws.amazon.com/bedrock/latest/userguide/'
_CARDS_INDEX = _BASE + 'model-cards.html'

# Individual card filenames look like  model-card-anthropic-claude-sonnet-4-6.html
# The provider index pages are  model-cards-anthropic.html  — note the plural
# "cards", so this pattern (requiring "model-card-" + a letter) skips them.
_MODEL_CARD_RE = re.compile(r'^model-card-[a-z][a-z0-9\-]*\.html$')

# A Bedrock model / inference-profile ID: provider.name[.version][:variant],
# optionally with a cross-region geo prefix. Accepts IDs with or without the
# trailing ":0" — the newer catalogue entries drop it.
_MODEL_ID_RE = re.compile(r'^[a-z][a-z0-9\-]*\.[a-z0-9][\w\-\.]*(?::[0-9]+)?$', re.IGNORECASE)

_NA_VALUES = {'', 'n/a', 'na', 'none', '-', 'not supported', 'not applicable'}


def _extract_card_links(index_html: str) -> list[str]:
    """Return absolute URLs for every individual model card on the index page."""
    soup = BeautifulSoup(index_html, 'html.parser')
    seen: set[str] = set()
    urls: list[str] = []
    for a in soup.find_all('a', href=True):
        filename = a['href'].split('/')[-1].split('?')[0].split('#')[0]
        if _MODEL_CARD_RE.match(filename) and filename not in seen:
            seen.add(filename)
            urls.append(_BASE + filename)
    return urls


def _headers(table) -> list[str]:
    """Lower-cased header labels for a table (th cells, else the first row)."""
    ths = table.find_all('th')
    if ths:
        return [th.get_text(strip=True).lower() for th in ths]
    first = table.find('tr')
    if first:
        return [c.get_text(strip=True).lower() for c in first.find_all(['td', 'th'])]
    return []


def _tokens_to_int(raw: str):
    """'1M tokens'->1000000, '203K tokens'->203000, '64K'->64000, '8,192'->8192."""
    if not raw:
        return None
    m = re.search(r'([\d,.]+)\s*([KMkm])?', raw)
    if not m:
        return None
    try:
        value = float(m.group(1).replace(',', ''))
    except ValueError:
        return None
    unit = (m.group(2) or '').upper()
    if unit == 'M':
        value *= 1_000_000
    elif unit == 'K':
        value *= 1_000
    return int(value)


def _parse_card_html(html: str, url: str) -> dict | None:
    """Parse one model card's HTML into a record, or None if no model ID found."""
    soup = BeautifulSoup(html, 'html.parser')
    text = soup.get_text(separator='\n', strip=True)

    def _label(patterns: list[str]) -> str:
        for pattern in patterns:
            m = re.search(pattern + r'[\s:：]+([^\n]+)', text, re.IGNORECASE)
            if m:
                return m.group(1).strip()
        return ''

    # ── Model IDs from the "Programmatic Access" table ────────────────────────
    model_id, geo_ids, global_id = '', [], ''
    for table in soup.find_all('table'):
        headers = _headers(table)
        if 'model id' not in headers:
            continue
        idx = {h: i for i, h in enumerate(headers)}
        for row in table.find_all('tr')[1:]:
            cells = row.find_all(['td', 'th'])
            mi = idx['model id']
            if mi < len(cells):
                cid = cells[mi].get_text(strip=True)
                if _MODEL_ID_RE.match(cid):
                    model_id = model_id or cid
            gi = idx.get('geo inference id')
            if gi is not None and gi < len(cells):
                for token in cells[gi].stripped_strings:
                    if _MODEL_ID_RE.match(token.strip()):
                        geo_ids.append(token.strip())
            gl = idx.get('global inference id')
            if gl is not None and gl < len(cells):
                for token in cells[gl].stripped_strings:
                    if _MODEL_ID_RE.match(token.strip()) and not global_id:
                        global_id = token.strip()
        if model_id:
            break

    # Fallback: the "Sample Code" section always has  modelId='...'
    if not model_id:
        m = re.search(r"""modelId\s*=\s*['"]([a-z0-9][\w\-\.]+(?::[0-9]+)?)['"]""",
                      html, re.IGNORECASE)
        if m:
            model_id = m.group(1)

    if not model_id:
        return None

    # ── Lifecycle + EOL from the "Model Details" bullets ──────────────────────
    lifecycle_stage = _label(['Model lifecycle']) or None
    eol_raw = _label(['Model EOL date', 'EOL date'])
    shutdown_date = '' if eol_raw.lower() in _NA_VALUES else eol_raw

    context_window = _tokens_to_int(_label(['Context [Ww]indow']))
    max_output_tokens = _tokens_to_int(_label(['Max [Oo]utput [Tt]okens', 'Maximum output tokens']))
    knowledge_cutoff = _label(['Knowledge [Cc]utoff', 'Training cutoff']) or None

    # ── Modalities table (green icon-yes.png vs red icon-no.png images) ────────
    input_modalities, output_modalities = [], []
    for table in soup.find_all('table'):
        headers = _headers(table)
        in_idx = next((i for i, h in enumerate(headers) if 'input modalities' in h), None)
        out_idx = next((i for i, h in enumerate(headers) if 'output modalities' in h), None)
        if in_idx is None and out_idx is None:
            continue
        for row in table.find_all('tr')[1:]:
            cells = row.find_all('td')
            for col_idx, bucket in ((in_idx, input_modalities), (out_idx, output_modalities)):
                if col_idx is None or col_idx >= len(cells):
                    continue
                cell = cells[col_idx]
                name = cell.get_text(strip=True)
                img = cell.find('img')
                if name and img and 'icon-yes' in img.get('src', ''):
                    bucket.append(name)
        break

    geo_all = list(dict.fromkeys(geo_ids + ([global_id] if global_id else [])))

    return {
        'model_id': model_id,
        'lifecycle_stage': lifecycle_stage,
        'shutdown_date': shutdown_date,
        'context_window': context_window,
        'max_output_tokens': max_output_tokens,
        'input_modalities': input_modalities or None,
        'output_modalities': output_modalities or None,
        'knowledge_cutoff': knowledge_cutoff,
        'geo_inference_ids': geo_all or None,
        'model_card_url': url,
    }


def _parse_card(url: str) -> dict | None:
    try:
        html = get_html(url)
    except Exception as e:
        print(f"  [model cards] Failed to fetch {url}: {e}")
        return None
    return _parse_card_html(html, url)


def scrape_bedrock_model_cards() -> list[dict]:
    """
    Discover and scrape all Bedrock model card pages.
    Returns a list of records (one per successfully parsed card).
    """
    print("Scraping AWS Bedrock model cards...")
    try:
        index_html = get_html(_CARDS_INDEX)
    except Exception as e:
        print(f"  [model cards] Failed to fetch index: {e}")
        return []

    card_urls = _extract_card_links(index_html)
    print(f"  Found {len(card_urls)} model card pages")

    results = []
    for url in card_urls:
        record = _parse_card(url)
        if record:
            results.append(record)

    print(f"  Parsed metadata for {len(results)} models")
    return results
