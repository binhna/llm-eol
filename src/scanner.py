"""
Scan our product repos to find which LLM models they declare and actually use.

The idea: every model our products can call must be declared in that project's
model config file (`llm_config.json` or `models.yaml`). So the config is the
authoritative list. We then search the rest of that repo for each model string
to see whether anything actually references it.

Each model ends up in one of three buckets:

  Used            the model name appears in production code or prompt config
  Test/Experiment it only appears in tests, experiments, dev or reporting code
  Config only     it is declared in the config but referenced nowhere else

"Config only" does NOT mean dead. These projects resolve models by *name* at
runtime (millmerran's `ModelConfig.get_model(name)`), so any declared model
becomes live by editing one prompt YAML — no code change, no deploy. That is
exactly why we keep tracking them for end-of-life dates.

## How we read other people's code safely

By default each project is read from a **bare mirror** kept under `.cache/repos/`
inside this project. A mirror is a private, read-only copy of the repo's history
with no working tree at all, so scanning:

  - never touches your own clone of that repo — not the files, not the branch,
    not uncommitted work, not even its .git directory
  - always sees the latest `main` from the server
  - works even if you have never cloned that repo

Nothing here ever runs `git pull`, `git checkout`, `git merge` or `git reset`.

A project can instead be read from a local checkout with `'source': 'worktree'`,
which is useful when you want whatever branch is currently checked out rather
than `main`. That is still read-only — we only ever read files and run
`git grep`. If the local checkout is missing, we fall back to the mirror.

Searching uses `git grep`, which only looks at files git tracks. That keeps
virtualenvs, caches and build output from polluting the results, and needs no
tools beyond git itself.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

# ── Which repos to scan ───────────────────────────────────────────────────────
# name   : label shown in the report and the Google Sheet
# remote : git URL, used to build/update the private bare mirror
# branch : which branch to read (mirrors track exactly this one branch)
# source : 'mirror'   read from our own bare mirror of `branch` (default, safest)
#          'worktree' read the checkout at `path` as it is on disk right now,
#                     whatever branch that happens to be. Read-only. Falls back
#                     to the mirror if `path` isn't there.
# path   : only needed for 'worktree', relative to this project's root
# config : the model config file, relative to the repo root
# format : how to read model names out of that file
#            'json_keys'   -> top-level JSON keys whose value is an object
#            'yaml_nested' -> YAML, model names are the keys under `models:`
# exclude: extra files to leave out of the usage search. The config file itself
#          is always excluded; list any OTHER file that is also configuration
#          rather than usage, otherwise every model looks "used" just because it
#          is named in a second config file.
# aliases: rewrite a config key that isn't the real model name
PROJECTS = [
    {
        'name': 'bellmere',
        'remote': 'git@github.com:Studiosity/bellmere.git',
        'branch': 'main',
        # bellmere moves fast on integration branches, so read the local
        # checkout to see what is actually in flight. Change to 'mirror' to
        # track main instead.
        'source': 'worktree',
        'path': '../bellmere',
        'config': 'src/config/models.yaml',
        'format': 'yaml_nested',
        # projects.yaml wires each model to an environment + TPM limit, and the
        # -ar pair is the autoresearch variant of the same two files. All four
        # are configuration, so a match in them is not evidence of use.
        'exclude': [
            'src/config/projects.yaml',
            'src/dev_scripts/models-ar.yaml',
            'src/dev_scripts/projects-ar.yaml',
        ],
    },
    {
        'name': 'burley',
        'remote': 'git@github.com:Studiosity/burley.git',
        'branch': 'main',
        'source': 'mirror',
        'config': 'src/llm_config.json',
        'format': 'json_keys',
        # burley calls its embedding model "embedding"; the real model is the
        # Azure deployment behind it
        'aliases': {'embedding': 'text-embedding-ada-002'},
    },
    {
        'name': 'norval',
        'remote': 'git@github.com:Studiosity/norval.git',
        'branch': 'main',
        'source': 'mirror',
        'config': 'llm_config.json',
        'format': 'json_keys',
    },
    {
        'name': 'bordertown',
        'remote': 'git@github.com:Studiosity/bordertown.git',
        'branch': 'main',
        'source': 'mirror',
        'config': 'dev/llm_config.json',
        'format': 'json_keys',
    },
]

# Where the private bare mirrors live, relative to this project's root.
# Safe to delete at any time — it is rebuilt on the next run.
MIRROR_DIR = '.cache/repos'

# Update the mirrors from the server before scanning. Set False to work fully
# offline from whatever the mirrors already hold.
REFRESH_MIRRORS = True

# A hit in a path containing any of these is treated as non-production.
NON_PRODUCTION_MARKERS = (
    '/test', 'test_', 'tests/', 'conftest',
    'experiment', '/dev/', 'dev_scripts/', '/uat/',
    'reporting/', 'simulation/', 'simulators/',
    'notebook', '.ipynb', 'eval_results/', 'performance/',
    'cloudwatch_logs/', '/docs/', '.md', '.jsonl', '.csv',
    'training/',  # data-generation pipelines, not request serving
)

# Never evidence of use, in any project: dependency lock files pin package
# versions and sometimes contain strings that look like model names.
GLOBAL_EXCLUDES = (
    'poetry.lock', 'package-lock.json', 'yarn.lock', 'Pipfile.lock',
    'pnpm-lock.yaml', 'uv.lock',
)

STATUS_USED = 'Used'
STATUS_TEST = 'Test/Experiment'
STATUS_CONFIG_ONLY = 'Config only'

# Best-to-worst, for picking a model's overall status across several projects
_STATUS_RANK = {STATUS_USED: 0, STATUS_TEST: 1, STATUS_CONFIG_ONLY: 2}


def _run(args: list[str], cwd: Path) -> tuple[int, str]:
    """Run a command, returning (returncode, stdout). Never raises."""
    try:
        p = subprocess.run(args, cwd=str(cwd), capture_output=True, text=True, timeout=300)
        return p.returncode, p.stdout
    except (OSError, subprocess.SubprocessError):
        return 1, ''


def _is_git_repo(path: Path) -> bool:
    code, _ = _run(['git', 'rev-parse', '--git-dir'], path)
    return code == 0


def _current_branch(path: Path) -> str:
    _, out = _run(['git', 'rev-parse', '--abbrev-ref', 'HEAD'], path)
    return out.strip() or '(unknown)'


def _ensure_mirror(project: dict, root: Path) -> Path | None:
    """
    Make sure we have an up-to-date bare mirror of the project's branch, and
    return its path. Returns None if it can't be created.

    A bare mirror has no working tree, so this cannot affect any checkout —
    ours or anyone else's. It is stored under MIRROR_DIR inside this project.
    """
    name = project['name']
    remote = project.get('remote')
    branch = project.get('branch', 'main')
    if not remote:
        return None

    mirror = (root / MIRROR_DIR / f'{name}.git').resolve()

    if mirror.exists():
        if REFRESH_MIRRORS:
            # Update just this branch. '+' allows a force update in case the
            # branch was rewritten upstream; there is no working tree to disturb.
            code, _ = _run(
                ['git', 'fetch', '--depth', '1', 'origin', f'+{branch}:{branch}'],
                mirror,
            )
            if code != 0:
                print(f"  [{name}] could not reach the server — using the mirror as it is")
        return mirror

    if not REFRESH_MIRRORS:
        print(f"  [{name}] no mirror yet and refresh is off — skipping")
        return None

    mirror.parent.mkdir(parents=True, exist_ok=True)
    print(f"  [{name}] creating a local mirror of {branch} (first run only)")
    code, _ = _run(
        ['git', 'clone', '--bare', '--depth', '1', '--single-branch',
         '--branch', branch, remote, str(mirror)],
        mirror.parent,
    )
    if code != 0:
        print(f"  [{name}] could not mirror {remote} — check your git access")
        return None
    return mirror


def _resolve_source(project: dict, root: Path) -> tuple[Path, str, str] | None:
    """
    Work out where to read this project from.

    Returns (repo_path, ref, description) where `ref` is either 'worktree'
    (read files straight off disk) or a git ref to read out of history.
    Returns None if the project can't be read at all.
    """
    name = project['name']

    if project.get('source') == 'worktree':
        path = project.get('path')
        if path:
            repo = (root / path).resolve()
            if repo.exists() and _is_git_repo(repo):
                return repo, 'worktree', f"local checkout ({_current_branch(repo)})"
            print(f"  [{name}] no local checkout at {repo} — using our mirror instead")

    mirror = _ensure_mirror(project, root)
    if mirror is None:
        return None
    branch = project.get('branch', 'main')
    return mirror, branch, f"mirror of {branch}"


def _read_config(project: dict, repo: Path, ref: str) -> str | None:
    """Return the raw text of the project's model config file, or None."""
    config_path = project['config']

    if ref == 'worktree':
        full = repo / config_path
        if not full.exists():
            return None
        try:
            return full.read_text(encoding='utf-8')
        except OSError:
            return None

    code, out = _run(['git', 'show', f'{ref}:{config_path}'], repo)
    return out if code == 0 and out.strip() else None


def _model_names(raw: str, fmt: str) -> list[str]:
    """Pull the declared model names out of a config file's text."""
    if fmt == 'json_keys':
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            return []
        if not isinstance(data, dict):
            return []
        return [k for k, v in data.items() if isinstance(v, dict)]

    if fmt == 'yaml_nested':
        try:
            import yaml
        except ImportError:
            print("  [scanner] PyYAML not installed — cannot read YAML config")
            return []
        try:
            data = yaml.safe_load(raw)
        except Exception:
            return []
        if not isinstance(data, dict):
            return []
        # Model names live under a top-level `models:` key; tolerate a flat file
        section = data.get('models', data)
        return list(section.keys()) if isinstance(section, dict) else []

    return []


def _whole_name_re(model: str) -> re.Pattern:
    """
    Match `model` only where it is the WHOLE model name, not part of a longer one.

    This matters a lot. Plain substring search reports 'gemini-2.5-flash' as used
    on a line that actually says 'gemini-2.5-flash-lite', and 'claude-sonnet-4'
    on a line saying 'claude-sonnet-4-6' — two different models with different
    end-of-life dates. So the character either side must not be one that could
    extend the name (letter, digit, dot, hyphen or underscore).
    """
    return re.compile(r'(?<![\w.\-])' + re.escape(model) + r'(?![\w.\-])')


def _search(model: str, project: dict, repo: Path, ref: str) -> list[str]:
    """
    Find where `model` appears in the repo, excluding config files.
    Returns a list of 'path:line' strings.
    """
    excludes = [f":!{project['config']}"]
    excludes += [f":!{p}" for p in project.get('exclude', [])]
    excludes += [f":!**/{p}" for p in GLOBAL_EXCLUDES]

    # -F fixed string, -n line numbers, -I skip binary files.
    # We search for the plain substring (fast, portable) and then filter the
    # results in Python, because git grep has no portable look-around support.
    if ref == 'worktree':
        args = ['git', 'grep', '-F', '-n', '-I', model, '--', '.', *excludes]
    else:
        args = ['git', 'grep', '-F', '-n', '-I', model, ref, '--', '.', *excludes]

    code, out = _run(args, repo)
    if code != 0 or not out:
        return []

    pattern = _whole_name_re(model)
    hits = []
    for line in out.splitlines():
        # worktree form:  path:line:content
        # ref form:       ref:path:line:content
        # Split only as far as the line number — the content that follows often
        # contains colons of its own (`  model: openai.gpt-oss-20b-1:0`), and
        # splitting further would truncate it and lose the match.
        fields = 3 if ref == 'worktree' else 4
        parts = line.split(':', fields - 1)
        if len(parts) < fields:
            continue
        if ref != 'worktree':
            parts = parts[1:]
        path, lineno, content = parts[0], parts[1], parts[2]
        if pattern.search(content):
            hits.append(f"{path}:{lineno}")
    return hits


def _classify(hits: list[str]) -> tuple[str, str]:
    """Return (status, evidence) for a model's search hits."""
    if not hits:
        return STATUS_CONFIG_ONLY, ''

    production = [h for h in hits
                  if not any(m in h.lower() for m in NON_PRODUCTION_MARKERS)]
    if production:
        return STATUS_USED, production[0]
    return STATUS_TEST, hits[0]


def scan_projects(projects: list[dict] | None = None, root: Path | None = None) -> dict:
    """
    Scan every configured project and work out which models it declares and uses.

    Returns:
        {
          'models': {
             '<model>': {
                'declared_in':  ['bellmere', ...],
                'used_in':      ['bellmere', ...],       # production references
                'status':       'Used' | 'Test/Experiment' | 'Config only',
                'per_project':  {'bellmere': {'status':…, 'evidence':…}, …},
             }, …
          },
          'rows':     [ {model, project, status, evidence}, … ]  # for the sheet
          'scanned':  ['bellmere', …],
          'skipped':  [('foo', 'reason'), …],
        }
    """
    projects = projects or PROJECTS
    root = root or Path(__file__).parent.parent

    models: dict[str, dict] = {}
    rows: list[dict] = []
    scanned: list[str] = []
    skipped: list[tuple[str, str]] = []

    print("Scanning product repos for model usage...")

    for project in projects:
        name = project['name']

        resolved = _resolve_source(project, root)
        if resolved is None:
            skipped.append((name, 'could not read the repo'))
            print(f"  [{name}] skipped — could not read the repo")
            continue
        repo, ref, where = resolved

        raw = _read_config(project, repo, ref)
        if raw is None:
            skipped.append((name, f"config not readable: {project['config']}"))
            print(f"  [{name}] skipped — cannot read {project['config']} in {where}")
            continue

        declared = _model_names(raw, project['format'])
        if not declared:
            skipped.append((name, 'no models found in config'))
            print(f"  [{name}] skipped — no models found in {project['config']}")
            continue

        aliases = project.get('aliases', {})
        print(f"  [{name}] {len(declared)} models declared in {project['config']} — {where}")

        for raw_name in declared:
            model = aliases.get(raw_name, raw_name)
            # search for the config key, and for the resolved name if different
            hits = _search(raw_name, project, repo, ref)
            if model != raw_name:
                hits += _search(model, project, repo, ref)
            status, evidence = _classify(hits)

            entry = models.setdefault(model, {
                'declared_in': [], 'used_in': [], 'per_project': {},
            })
            entry['declared_in'].append(name)
            if status == STATUS_USED:
                entry['used_in'].append(name)
            entry['per_project'][name] = {'status': status, 'evidence': evidence}

            rows.append({
                'model': model,
                'project': name,
                'status': status,
                'evidence': evidence,
            })

        scanned.append(name)

    # Overall status per model = the best status it has in any project
    for entry in models.values():
        best = min((p['status'] for p in entry['per_project'].values()),
                   key=lambda s: _STATUS_RANK.get(s, 9))
        entry['status'] = best

    counts: dict[str, int] = {}
    for entry in models.values():
        counts[entry['status']] = counts.get(entry['status'], 0) + 1
    summary = ', '.join(f"{v} {k}" for k, v in sorted(counts.items()))
    print(f"  Found {len(models)} distinct models across {len(scanned)} project(s)"
          + (f" — {summary}" if summary else ''))

    return {'models': models, 'rows': rows, 'scanned': scanned, 'skipped': skipped}


def models_to_track(scan: dict, extra: list[str] | None = None) -> list[str]:
    """The model list to check for end-of-life dates: everything found, plus extras."""
    found = sorted(scan['models'].keys())
    for m in (extra or []):
        if m not in found:
            found.append(m)
    return found
