from parsers.google_gemini import parse_google_gemini
from parsers.openai import parse_openai
from parsers.azure_openai import parse_azure_openai
from parsers.anthropic import parse_anthropic
from parsers.vertex_ai import parse_vertex_ai
from parsers.bedrock import parse_bedrock

# The provider label each parser produces, paired with the parser itself. The
# label must match the 'provider' value in the records it returns, because that
# is how we tell which provider a scrape failure belongs to.
_PARSERS = (
    ('Google Gemini', parse_google_gemini),
    ('OpenAI', parse_openai),
    ('Azure OpenAI', parse_azure_openai),
    ('Anthropic', parse_anthropic),
    ('Vertex AI', parse_vertex_ai),
    ('AWS Bedrock', parse_bedrock),
)


def parse_all_deprecations():
    """
    Collect and deduplicate deprecation records from all supported providers.

    Returns:
        (records, stats) where

        records: deduplicated list of dicts with keys provider, model,
                 shutdown_date, source_url, and optionally lifecycle_stage.
        stats:   {provider_label: number_of_records_scraped}. A count of 0 means
                 that provider's scrape produced nothing — almost always because
                 the provider changed their page layout, not because they
                 retired every model. This matters because the local database
                 keeps its last-known values, so without this signal a broken
                 parser looks exactly like a provider with no news.
    """
    all_deprecations = []
    stats = {}

    for label, parser in _PARSERS:
        try:
            records = parser() or []
        except Exception as e:
            # A parser should handle its own errors, but never let one bad
            # provider stop the others.
            print(f"  !! {label} parser raised an error: {e}")
            records = []
        stats[label] = len(records)
        all_deprecations.extend(records)

    failed = [label for label, count in stats.items() if count == 0]
    if failed:
        print()
        print("  " + "!" * 68)
        print("  !! SCRAPE FAILED for: " + ", ".join(failed))
        print("  !! These providers returned no records at all. Any of their")
        print("  !! models below are showing LAST-KNOWN values, not current")
        print("  !! ones. Check whether the provider changed their page layout.")
        print("  " + "!" * 68)
        print()

    seen = set()
    unique = []
    for item in all_deprecations:
        key = (item['provider'], item['model'])
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique, stats
