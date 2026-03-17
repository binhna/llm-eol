import re
import pandas as pd
from io import StringIO
from utils import get_html, parse_shutdown_date

SOURCE_URL = 'https://docs.aws.amazon.com/bedrock/latest/userguide/model-lifecycle.html'


def parse_bedrock():
    """
    Parse model lifecycle data from AWS Bedrock.

    Covers three lifecycle sections from the same page:
      - Active  : uses the 'Model ID' column; EOL dates are "No sooner than X" —
                  the prefix is stripped so the date parses correctly.
      - Legacy  : uses the 'Model version' name (no API ID available); EOL dates are
                  concrete. The same model can appear multiple times for different regions,
                  so we keep only the earliest EOL date.
      - EOL     : same structure as Legacy; distinguished from Legacy by the absence of
                  the "Public extended access date" column.
    """
    print("Parsing AWS Bedrock...")
    # model identifier -> (earliest EOL date string, lifecycle stage)
    earliest: dict = {}

    def _keep_earliest(model, date_str, stage):
        if not model or model.lower() == 'nan':
            return
        if model not in earliest:
            earliest[model] = (date_str, stage)
            return
        existing_date = parse_shutdown_date(earliest[model][0])
        new_date = parse_shutdown_date(date_str)
        if existing_date and new_date and new_date < existing_date:
            earliest[model] = (date_str, stage)

    try:
        html = get_html(SOURCE_URL)
        dfs = pd.read_html(StringIO(html))

        for df in dfs:
            df.columns = [str(c).strip() for c in df.columns]

            # Active models table (has a "Model ID" column)
            if 'Model ID' in df.columns and 'EOL date' in df.columns:
                for _, row in df.iterrows():
                    model_id = str(row['Model ID']).strip()
                    eol = str(row['EOL date']).strip()
                    # Strip "No sooner than " prefix
                    m = re.match(r'no sooner than\s+(.*)', eol, re.IGNORECASE)
                    if m:
                        date_part = m.group(1).strip()
                        # Skip "No sooner than launch date + 1 year" — no concrete date
                        if re.search(r'launch date', date_part, re.IGNORECASE):
                            continue
                        eol = date_part
                    _keep_earliest(model_id, eol, 'Active')

            # Legacy / EOL tables (have a "Model version" column)
            # Legacy has "Public extended access date"; EOL does not.
            elif 'Model version' in df.columns and 'EOL date' in df.columns:
                stage = 'Legacy' if 'Public extended access date' in df.columns else 'EOL'
                for _, row in df.iterrows():
                    model_name = str(row['Model version']).strip()
                    eol = str(row['EOL date']).strip()
                    _keep_earliest(model_name, eol, stage)

    except Exception as e:
        print(f"  Failed to parse AWS Bedrock: {e}")
        return []

    return [
        {
            'provider': 'AWS Bedrock',
            'model': model,
            'shutdown_date': date,
            'lifecycle_stage': stage,
            'source_url': SOURCE_URL,
        }
        for model, (date, stage) in earliest.items()
    ]
