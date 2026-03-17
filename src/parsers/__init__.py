from parsers.google_gemini import parse_google_gemini
from parsers.openai import parse_openai
from parsers.azure_openai import parse_azure_openai
from parsers.anthropic import parse_anthropic
from parsers.vertex_ai import parse_vertex_ai
from parsers.bedrock import parse_bedrock


def parse_all_deprecations():
    """
    Collect and deduplicate deprecation records from all supported providers.

    Returns:
        list: Deduplicated list of deprecation records, each a dict with keys:
              provider, model, shutdown_date, source_url,
              and optionally lifecycle_stage (AWS Bedrock only).
    """
    all_deprecations = []
    all_deprecations.extend(parse_google_gemini())
    all_deprecations.extend(parse_openai())
    all_deprecations.extend(parse_azure_openai())
    all_deprecations.extend(parse_anthropic())
    all_deprecations.extend(parse_vertex_ai())
    all_deprecations.extend(parse_bedrock())

    seen = set()
    unique = []
    for item in all_deprecations:
        key = (item['provider'], item['model'])
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique
