"""
Scan our product repos to find which LLM models they declare and actually use.

The idea: every model our products can call must be declared in that project's
model config file (`llm_config.json` or `models.yaml`). So the config is the
authoritative list. We then search the rest of that repo for each model string
to see whether anything actually references it.

Each model ends up in one of three buckets. They answer "where is this model
referenced?", so every bucket except the last IS a form of use — the labels say
*which kind*:

  Production   referenced from production code or prompt config — real traffic
  Test only    referenced, but only from tests, experiments, dev or reporting
  Config only  declared in the config, referenced nowhere else in the repo

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

import collections
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
        # Read main, like every other project. This used to read the local
        # checkout to catch models added on in-flight integration branches, but
        # that made the report depend on whichever branch each person happened
        # to have checked out — two people would produce different sheets from
        # the same command. Everyone reading main means one shared, repeatable
        # answer. Set 'source': 'worktree' with a 'path' to go back to reading a
        # local checkout.
        'source': 'mirror',
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

# ── Deciding whether a reference is production or not ─────────────────────────
# This is a convention-based guess, not something the repos declare, so it is
# worth understanding how it fails.
#
# Directory names below are matched as WHOLE PATH SEGMENTS. That matters: an
# earlier version matched the substring '/dev/', which silently missed
# bordertown's top-level 'dev/' folder because there is no leading slash. Segment
# matching also avoids the opposite mistake — 'dev' will not match 'devices/'.
#
# Anything not listed here counts as production. That default is deliberate: for
# end-of-life tracking, wrongly calling something production is harmless noise,
# while wrongly calling a live model "test only" could get a real dependency
# ignored. So keep this list to names that are unambiguous — when in doubt, leave
# a directory out and let it read as production.
#
# Add project-specific folders with an 'extra_non_production' key in PROJECTS.
NON_PRODUCTION_DIRS = frozenset({
    'test', 'tests', 'testing', 'fixtures', 'mocks', 'stubs',
    'experiment', 'experiments',
    'dev', 'dev_scripts', 'devcontainer', 'uat',
    'reporting', 'reports',
    'simulation', 'simulations', 'simulator', 'simulators',
    'notebook', 'notebooks',
    'eval', 'evals', 'eval_results', 'benchmark', 'benchmarks',
    'performance', 'perf', 'cloudwatch_logs',
    'doc', 'docs', 'example', 'examples', 'samples',
    'sandbox', 'playground', 'poc', 'scratch', 'tmp',
    'training',  # data-generation pipelines, not request serving
})

# File names/extensions that are never production code, wherever they sit.
NON_PRODUCTION_FILE_RE = re.compile(
    r'(?:^|/)(?:conftest|test_[^/]*|[^/]*_test)\.[a-z0-9]+$'
    r'|\.(?:ipynb|md|markdown|jsonl|csv|log|txt|rst)$',
    re.IGNORECASE,
)


def _is_production_path(path: str, extra_dirs: frozenset = frozenset()) -> bool:
    """
    True if a reference at `path` looks like production code.

    `path` is repo-relative, e.g. 'src/bordertown/agents/segmentation_agent.py'.
    """
    if NON_PRODUCTION_FILE_RE.search(path):
        return False
    segments = [s.lower() for s in path.split('/')[:-1]]  # directories only
    blocked = NON_PRODUCTION_DIRS | extra_dirs
    return not any(s in blocked for s in segments)

# Never evidence of use, in any project: dependency lock files pin package
# versions and sometimes contain strings that look like model names.
GLOBAL_EXCLUDES = (
    'poetry.lock', 'package-lock.json', 'yarn.lock', 'Pipfile.lock',
    'pnpm-lock.yaml', 'uv.lock',
)

STATUS_USED = 'Production'
STATUS_TEST = 'Test only'
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


def _declared_models(raw: str, fmt: str) -> list[tuple[str, str]]:
    """
    Pull the declared models out of a config file's text.

    Returns a list of (model_name, provider) pairs. The provider is whatever the
    project itself declares — 'azure', 'google', 'anthropic', 'mistral',
    'bedrock' — which is useful because it tells us where a model is *hosted*
    even when no provider page mentions it. An empty string means the config
    didn't say.
    """
    section = None

    if fmt == 'json_keys':
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            return []
        section = data if isinstance(data, dict) else None

    elif fmt == 'yaml_nested':
        try:
            import yaml
        except ImportError:
            print("  [scanner] PyYAML not installed — cannot read YAML config")
            return []
        try:
            data = yaml.safe_load(raw)
        except Exception:
            return []
        if isinstance(data, dict):
            # Model names live under a top-level `models:` key; tolerate a flat file
            inner = data.get('models', data)
            section = inner if isinstance(inner, dict) else None

    if not section:
        return []

    out = []
    for key, value in section.items():
        if not isinstance(value, dict):
            continue
        provider = value.get('provider') or ''
        out.append((key, str(provider).strip().lower()))
    return out


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


def _classify(hits: list[str], extra_dirs: frozenset = frozenset()) -> tuple[str, str]:
    """
    Return (status, evidence) for a model's search hits.

    Evidence is the single most convincing reference: a production one if any
    exists, preferring paths under a source directory over loose top-level files
    so the evidence shown is the one a reader would find most meaningful.
    """
    if not hits:
        return STATUS_CONFIG_ONLY, ''

    production = [h for h in hits if _is_production_path(h.rsplit(':', 1)[0], extra_dirs)]
    if production:
        production.sort(key=lambda h: (0 if h.startswith('src/') else 1, h))
        return STATUS_USED, production[0]
    return STATUS_TEST, sorted(hits)[0]


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
    production_dirs: dict[str, set] = collections.defaultdict(set)

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

        declared = _declared_models(raw, project['format'])
        if not declared:
            skipped.append((name, 'no models found in config'))
            print(f"  [{name}] skipped — no models found in {project['config']}")
            continue

        aliases = project.get('aliases', {})
        print(f"  [{name}] {len(declared)} models declared in {project['config']} — {where}")

        extra_dirs = frozenset(d.lower() for d in project.get('extra_non_production', ()))

        for raw_name, provider in declared:
            model = aliases.get(raw_name, raw_name)
            # search for the config key, and for the resolved name if different
            hits = _search(raw_name, project, repo, ref)
            if model != raw_name:
                hits += _search(model, project, repo, ref)
            status, evidence = _classify(hits, extra_dirs)

            # Remember which folders we treated as production, so a directory
            # nobody has classified yet can be reported rather than assumed.
            for hit in hits:
                path = hit.rsplit(':', 1)[0]
                if '/' in path and _is_production_path(path, extra_dirs):
                    production_dirs[project['name']].add(path.split('/')[0])

            entry = models.setdefault(model, {
                'declared_in': [], 'used_in': [], 'providers': [], 'per_project': {},
            })
            entry['declared_in'].append(name)
            if status == STATUS_USED:
                entry['used_in'].append(name)
            if provider and provider not in entry['providers']:
                entry['providers'].append(provider)
            entry['per_project'][name] = {
                'status': status, 'evidence': evidence, 'provider': provider,
            }

            rows.append({
                'model': model,
                'project': name,
                'provider': provider,
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

    # Show which top-level folders counted as production. If a repo adds a new
    # folder that is really tests or a sandbox, it shows up here as production
    # and can be added to NON_PRODUCTION_DIRS or the project's
    # 'extra_non_production' list. Without this the misread would be invisible.
    if production_dirs:
        listed = '; '.join(
            f"{proj}: {', '.join(sorted(dirs))}"
            for proj, dirs in sorted(production_dirs.items())
        )
        print(f"  Counted as production — {listed}")
        print("  (if any of those are really tests or scratch work, add them to"
              " NON_PRODUCTION_DIRS in src/scanner.py)")

    return {
        'models': models, 'rows': rows, 'scanned': scanned, 'skipped': skipped,
        'production_dirs': {k: sorted(v) for k, v in production_dirs.items()},
    }


def models_to_track(scan: dict, extra: list[str] | None = None) -> list[str]:
    """The model list to check for end-of-life dates: everything found, plus extras."""
    found = sorted(scan['models'].keys())
    for m in (extra or []):
        if m not in found:
            found.append(m)
    return found
