import re
from utils import calculate_risk_info

# Regex that matches AWS Bedrock cross-region inference prefixes such as
# us., eu., ap., apac., au., ca., jp., global., us-gov.
# us-gov must appear before us so the longer prefix isn't shadowed.
_BEDROCK_GEO_PREFIX_RE = re.compile(
    r'^(?:global|us-gov|us|eu|ap|apac|au|ca|jp)\.',
    re.IGNORECASE,
)

# Providers write a version as a bracketed suffix — "gpt-4o (2024-05-13)" — while
# our configs write it joined on — "gpt-4o-2024-05-13". Normalising the bracketed
# form lets the two meet.
_BRACKET_VERSION_RE = re.compile(r'^(.*?)\s*\(([^)]+)\)\s*$')

# An 8-digit date suffix, as in "claude-sonnet-4-20250514". A model name plus a
# date suffix is the same model, so "claude-sonnet-4" should match it. Crucially
# this must NOT let "claude-sonnet-4" match "claude-sonnet-4-5-20250929", which
# is a different model — hence requiring the suffix to be *only* a date.
_DATE_SUFFIX_RE = re.compile(r'^-\d{8}$')


def _normalise_bracket_version(name):
    """'gpt-4o (2024-05-13)' -> 'gpt-4o-2024-05-13'. Returns None if no brackets."""
    m = _BRACKET_VERSION_RE.match(name)
    if not m:
        return None
    base, version = m.group(1).strip(), m.group(2).strip()
    if not base or not version:
        return None
    return f"{base}-{version}"


# The same model often appears on more than one platform with DIFFERENT dates —
# claude-3-5-haiku retires Feb 2026 on Anthropic's own API but July 2026 on
# Vertex AI. Only the platform we actually call matters, so the provider our
# config declares decides which record wins. Listed best-first.
#
# Note 'anthropic' and 'mistral' prefer Vertex AI: in our configs those models
# are reached through Vertex (their secret_id is google-vertex/...), not through
# the vendor's own API.
_CONFIG_PROVIDER_PREFERENCE = {
    'azure': ('Azure OpenAI', 'OpenAI'),
    'bedrock': ('AWS Bedrock',),
    'google': ('Vertex AI', 'Google Gemini'),
    'anthropic': ('Vertex AI', 'Anthropic', 'AWS Bedrock'),
    'mistral': ('Vertex AI', 'Azure OpenAI', 'AWS Bedrock'),
}


def _preferred_matches(matches, config_provider):
    """
    Given every provider record a model matched, keep only those from the
    platform we actually use. Falls back to all matches when we can't tell.
    """
    if not config_provider or len(matches) < 2:
        return matches
    preference = _CONFIG_PROVIDER_PREFERENCE.get(config_provider.lower())
    if not preference:
        return matches
    for provider in preference:
        subset = [m for m in matches if m['Provider'] == provider]
        if subset:
            return subset
    return matches


def _same_model(user_model, scraped_model):
    """
    Decide whether a model in our config and a model on a provider page are the
    same thing. Both arguments must already be lower-cased.

    Deliberately strict about suffixes: a longer name is a DIFFERENT model
    ('gemini-2.5-flash' vs 'gemini-2.5-flash-lite', 'zai.glm-4.7' vs
    'zai.glm-4.7-flash'), so we never treat one as a prefix-match of the other
    unless the extra part is purely a version.
    """
    if user_model == scraped_model:
        return True

    # Provider appends a version in brackets: 'gpt-4o-mini (2024-07-18)'
    normalised = _normalise_bracket_version(scraped_model)
    if normalised:
        if user_model == normalised:
            return True
        # 'gpt-4o-mini' should match 'gpt-4o-mini (2024-07-18)'
        base = _BRACKET_VERSION_RE.match(scraped_model).group(1).strip()
        if user_model == base:
            return True

    # Our config carries an explicit version tag: 'claude-3-haiku@20240307'
    if user_model.startswith(scraped_model + '@'):
        return True

    # Provider name is our name plus a dated release: 'claude-sonnet-4-20250514'
    if scraped_model.startswith(user_model):
        if _DATE_SUFFIX_RE.match(scraped_model[len(user_model):]):
            return True

    return False


def check_my_models(my_models, deprecation_data, model_providers=None):
    """
    Match each model in my_models against the scraped deprecation data.

    Args:
        model_providers: optional {model: provider} from the repo scan. When a
            model matches records on several platforms, this picks the one we
            actually call — see _CONFIG_PROVIDER_PREFERENCE.

    Matching rules (see _same_model):
      1. Exact match
      2. Bracketed version   ('gpt-4o-2024-05-13' and 'gpt-4o' both match
                              'gpt-4o (2024-05-13)')
      3. Version tag         ('claude-3-haiku@20240307' matches 'claude-3-haiku')
      4. Dated release       ('claude-sonnet-4' matches 'claude-sonnet-4-20250514'
                              but NOT 'claude-sonnet-4-5-20250929')
      5. Bedrock geo-prefix strip, then rules 1-4 again
                             ('us.meta.llama3-...' matches 'meta.llama3-...')

    Returns:
        list of match dicts with keys: Our Model, Scraped Model, Provider,
        Shutdown Date, Days Remaining, Risk Level.
    """
    print("\n" + "=" * 80)
    print(" MODEL DEPRECATION CHECK REPORT")
    print("=" * 80)

    deprecation_matches = []
    model_providers = model_providers or {}

    for user_model in my_models:
        user_model_lower = user_model.lower()
        found_for_model = []

        for data in deprecation_data:
            scraped_model_lower = data['model'].lower()

            is_match = _same_model(user_model_lower, scraped_model_lower)

            # Bedrock cross-region inference prefix: strip it and try again
            if not is_match and data['provider'] == 'AWS Bedrock':
                stripped = _BEDROCK_GEO_PREFIX_RE.sub('', user_model_lower)
                if stripped != user_model_lower:
                    is_match = _same_model(stripped, scraped_model_lower)

            if is_match:
                found_for_model.append({
                    'Our Model': user_model,
                    'Scraped Model': data['model'],
                    'Provider': data['provider'],
                    'Shutdown Date': data['shutdown_date'],
                })

        # Narrow multi-platform matches down to the platform we actually call
        deprecation_matches.extend(
            _preferred_matches(found_for_model, model_providers.get(user_model))
        )

    matched_set = {r['Our Model'] for r in deprecation_matches}
    unmatched = [m for m in my_models if m not in matched_set]

    if deprecation_matches:
        print("\n  DEPRECATED MODELS FOUND:\n")

        for row in deprecation_matches:
            _, days_remaining, risk_level, _ = calculate_risk_info(row['Shutdown Date'])
            row['Days Remaining'] = days_remaining
            row['Risk Level'] = risk_level

        col_widths = {
            'Our Model':     max(25, max(len(str(r['Our Model']))     for r in deprecation_matches)),
            'Scraped Model': max(25, max(len(str(r['Scraped Model'])) for r in deprecation_matches)),
            'Provider':      max(15, max(len(str(r['Provider']))      for r in deprecation_matches)),
            'Shutdown Date': 35,
            'Days Left':     10,
            'Risk':          9,
        }

        header = (
            f"{'Our Model':<{col_widths['Our Model']}} | "
            f"{'Scraped Model':<{col_widths['Scraped Model']}} | "
            f"{'Provider':<{col_widths['Provider']}} | "
            f"{'Shutdown Date':<{col_widths['Shutdown Date']}} | "
            f"{'Days Left':<{col_widths['Days Left']}} | "
            f"{'Risk':<{col_widths['Risk']}}"
        )
        print(header)
        print("-" * len(header))

        for row in deprecation_matches:
            shutdown_date = str(row['Shutdown Date'])
            if len(shutdown_date) > col_widths['Shutdown Date']:
                shutdown_date = shutdown_date[:col_widths['Shutdown Date'] - 3] + '...'
            print(
                f"{row['Our Model']:<{col_widths['Our Model']}} | "
                f"{row['Scraped Model']:<{col_widths['Scraped Model']}} | "
                f"{row['Provider']:<{col_widths['Provider']}} | "
                f"{shutdown_date:<{col_widths['Shutdown Date']}} | "
                f"{str(row['Days Remaining']):<{col_widths['Days Left']}} | "
                f"{str(row['Risk Level']):<{col_widths['Risk']}}"
            )
        print()
    else:
        print("\n  None of your models appear to be deprecated right now.\n")

    if unmatched:
        print(f"  {len(unmatched)} model(s) in your list had no match in any provider page:")
        for m in unmatched:
            print(f"    - {m}")
        print()

    return deprecation_matches, unmatched
