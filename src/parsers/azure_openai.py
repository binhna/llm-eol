import pandas as pd
from io import StringIO
from utils import get_html

# Microsoft split this into two pages. The one we want is the *schedule*, which
# is the canonical list of dates ("For specific retirement dates, see Model
# retirement schedule"). The older `model-retirements` page is the lifecycle
# *policy* article; it still carries some legacy tables whose dates lag behind
# the schedule, which is why we read the schedule instead.
SOURCE_URL = 'https://learn.microsoft.com/en-us/azure/foundry/openai/concepts/model-retirement-schedule'

# Column headings differ between the schedule page and the older policy page,
# so accept either spelling.
_MODEL_COLS = ('Model', 'Model Name')
_DATE_COLS = ('Retirement date', 'Retirement Date')
_VERSION_COLS = ('Version', 'Model Version', 'Model Version1')
_LIFECYCLE_COLS = ('Lifecycle',)

# An em dash (or empty cell) in the date column means Microsoft has not
# scheduled a retirement for that model yet.
_NO_DATE_VALUES = {'', '-', '—', '–', 'nan', 'none', 'n/a'}


def _pick(df, names):
    """Return the first column present in df from `names`, else None."""
    for n in names:
        if n in df.columns:
            return n
    return None


def parse_azure_openai():
    """
    Parse the Azure / Microsoft Foundry model retirement schedule.

    The page has one table per model publisher (Azure OpenAI, Anthropic,
    Mistral AI, Meta, xAI, ...), all with the same columns, so we read every
    table that has a model column and a retirement-date column.
    """
    print("Parsing Azure OpenAI...")
    deprecations = []
    try:
        html = get_html(SOURCE_URL)
        dfs = pd.read_html(StringIO(html))
        for df in dfs:
            df.columns = [str(c).strip() for c in df.columns]
            model_col = _pick(df, _MODEL_COLS)
            date_col = _pick(df, _DATE_COLS)
            if not model_col or not date_col:
                continue
            version_col = _pick(df, _VERSION_COLS)
            lifecycle_col = _pick(df, _LIFECYCLE_COLS)

            for _, row in df.iterrows():
                model = str(row[model_col]).strip()
                if not model or model.lower() == 'nan':
                    continue

                version = str(row[version_col]).strip() if version_col else ''
                if version.lower() in _NO_DATE_VALUES:
                    version = ''
                full_model = f"{model} ({version})" if version else model

                date = str(row[date_col]).strip()
                # Keep the record even with no date — it still tells us the
                # model exists and that no retirement is scheduled.
                if date.lower() in _NO_DATE_VALUES:
                    date = ''

                record = {
                    'provider': 'Azure OpenAI',
                    'model': full_model,
                    'shutdown_date': date,
                    'source_url': SOURCE_URL,
                }
                if lifecycle_col:
                    stage = str(row[lifecycle_col]).strip()
                    if stage and stage.lower() not in _NO_DATE_VALUES:
                        record['lifecycle_stage'] = stage
                deprecations.append(record)

    except Exception as e:
        print(f"  Failed to parse Azure OpenAI: {e}")
    return deprecations
