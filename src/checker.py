import re
from utils import calculate_risk_info

# Regex that matches AWS Bedrock cross-region inference prefixes such as
# us., eu., ap., apac., au., ca., jp., global., us-gov.
# us-gov must appear before us so the longer prefix isn't shadowed.
_BEDROCK_GEO_PREFIX_RE = re.compile(
    r'^(?:global|us-gov|us|eu|ap|apac|au|ca|jp)\.',
    re.IGNORECASE,
)


def check_my_models(my_models, deprecation_data):
    """
    Match each model in my_models against the scraped deprecation data.

    Matching rules (applied in order, first match wins):
      1. Exact match
      2. Scraped model has appended info  (e.g. 'gpt-4o' matches 'gpt-4o (2024-05-13)')
      3. User model has appended version  (e.g. 'claude-3-haiku@20240307' matches 'claude-3-haiku')
      4. Bedrock geo-prefix strip         (e.g. 'us.meta.llama3-...' matches 'meta.llama3-...')

    Returns:
        list of match dicts with keys: Our Model, Scraped Model, Provider,
        Shutdown Date, Days Remaining, Risk Level.
    """
    print("\n" + "=" * 80)
    print(" MODEL DEPRECATION CHECK REPORT")
    print("=" * 80)

    deprecation_matches = []

    for user_model in my_models:
        user_model_lower = user_model.lower()

        for data in deprecation_data:
            scraped_model_lower = data['model'].lower()

            # 1. Exact match
            is_match = (user_model_lower == scraped_model_lower)

            # 2. Scraped model has appended dates/info
            if not is_match:
                is_match = (
                    scraped_model_lower.startswith(user_model_lower + " ")
                    or scraped_model_lower.startswith(user_model_lower + " (")
                )

            # 3. User model has appended version tags
            if not is_match:
                is_match = (
                    user_model_lower.startswith(scraped_model_lower + "@")
                    or user_model_lower.startswith(scraped_model_lower + "-")
                )

            # 4. Bedrock cross-region inference prefix
            if not is_match and data['provider'] == 'AWS Bedrock':
                stripped = _BEDROCK_GEO_PREFIX_RE.sub('', user_model_lower)
                if stripped != user_model_lower:
                    is_match = (
                        stripped == scraped_model_lower
                        or scraped_model_lower.startswith(stripped + " ")
                        or scraped_model_lower.startswith(stripped + " (")
                        or stripped.startswith(scraped_model_lower + "@")
                        or stripped.startswith(scraped_model_lower + "-")
                    )

            if is_match:
                deprecation_matches.append({
                    'Our Model': user_model,
                    'Scraped Model': data['model'],
                    'Provider': data['provider'],
                    'Shutdown Date': data['shutdown_date'],
                })

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
