#!/usr/bin/env python3
"""
Generate the Codebase Analytics card (assets/codebase-analytics.svg) and its
machine-readable twin (data/loc-stats.json).

What it measures
- Lines authored : lines ADDED by you across the full git history of every repo you own
                   (git log --numstat, filtered to your author identities).
- Live codebase  : physical lines currently present in each repo's default branch.
- Top repos      : ranked by live lines.
- Top languages  : ranked by live lines.
- Monthly trend  : lines authored per month for the last 12 months.

Scope rules
- Repositories you own, forks excluded.
- Private repositories are included when a token with access is supplied
  (LOC_STATS_TOKEN). Their names are masked ("private-project-N") in every output
  so nothing sensitive is published.
- Generated, vendored and lock files are excluded (node_modules, dist, build,
  lockfiles, *.min.*, files over 1 MB, binary files, and single-file additions
  above 10,000 lines).

Environment
  GH_USERNAME        GitHub username                       (default: ayushcmd)
  LOC_STATS_TOKEN    PAT with read access to private repos (falls back to GITHUB_TOKEN)
  GITHUB_TOKEN       default Actions token (public repos only)
  INCLUDE_PRIVATE    true|false                            (default: true)
  INCLUDE_FORKS      true|false                            (default: false)
  SHOW_PRIVATE_NAMES true|false                            (default: false)
  EXCLUDE_REPOS      comma-separated repo names to skip    (default: none)
  AUTHOR_PATTERNS    comma-separated git --author patterns (default: ayushcmd,Ayush Raj)
"""
import base64
import html
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SVG_PATH = ROOT / "assets" / "codebase-analytics.svg"
JSON_PATH = ROOT / "data" / "loc-stats.json"

USERNAME = os.environ.get("GH_USERNAME", "ayushcmd")
TOKEN = os.environ.get("LOC_STATS_TOKEN") or os.environ.get("GITHUB_TOKEN", "")


def env_flag(name, default):
    return os.environ.get(name, str(default)).strip().lower() == "true"


def env_list(name, default):
    raw = os.environ.get(name)
    if raw is None:
        return list(default)
    return [item.strip() for item in raw.split(",") if item.strip()]


INCLUDE_PRIVATE = env_flag("INCLUDE_PRIVATE", True)
INCLUDE_FORKS = env_flag("INCLUDE_FORKS", False)
SHOW_PRIVATE_NAMES = env_flag("SHOW_PRIVATE_NAMES", False)
EXCLUDE_REPOS = {name.lower() for name in env_list("EXCLUDE_REPOS", [])}
AUTHOR_PATTERNS = env_list("AUTHOR_PATTERNS", ["ayushcmd", "Ayush Raj"])

TOP_REPOS = 6
TOP_LANGUAGES = 6
MONTHS = 12
MAX_FILE_BYTES = 1_000_000
MAX_FILE_ADDITIONS = 10_000
GIT_TIMEOUT = 600

LANGUAGE_BY_EXTENSION = {
    ".py": "Python", ".pyw": "Python",
    ".js": "JavaScript", ".jsx": "JavaScript", ".mjs": "JavaScript", ".cjs": "JavaScript",
    ".ts": "TypeScript", ".tsx": "TypeScript",
    ".html": "HTML", ".htm": "HTML",
    ".css": "CSS", ".scss": "SCSS", ".sass": "SCSS",
    ".c": "C", ".h": "C",
    ".cpp": "C++", ".cc": "C++", ".cxx": "C++", ".hpp": "C++",
    ".java": "Java", ".kt": "Kotlin", ".swift": "Swift", ".go": "Go", ".rs": "Rust",
    ".rb": "Ruby", ".php": "PHP", ".cs": "C#",
    ".sh": "Shell", ".bash": "Shell", ".zsh": "Shell",
    ".ps1": "PowerShell", ".bat": "Batchfile", ".cmd": "Batchfile",
    ".sql": "SQL", ".r": "R",
    ".astro": "Astro", ".vue": "Vue", ".svelte": "Svelte",
    ".ipynb": "Jupyter Notebook",
}
LANGUAGE_BY_FILENAME = {"dockerfile": "Dockerfile"}

EXCLUDED_DIRS = {
    ".git", "node_modules", "dist", "build", "vendor", "venv", ".venv", "env",
    "__pycache__", ".next", ".astro", ".cache", "coverage", "target", "Pods",
    "DerivedData", "bower_components", "site-packages", ".gradle", ".idea", ".vscode",
}
LOCKFILES = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "cargo.lock",
    "gemfile.lock", "composer.lock", "pipfile.lock", "bun.lockb", "uv.lock",
}

LANGUAGE_COLORS = {
    "Python": "#3572A5", "TypeScript": "#3178C6", "JavaScript": "#F1E05A",
    "Jupyter Notebook": "#DA5B0B", "HTML": "#E34C26", "CSS": "#8250DF",
    "SCSS": "#C6538C", "Dockerfile": "#4C9ED9", "C++": "#F34B7D", "C": "#7C7C7C",
    "R": "#198CE7", "Shell": "#89E051", "Java": "#B07219", "Astro": "#FF5A03",
    "PowerShell": "#2D6BA8", "Batchfile": "#C1F12E", "Swift": "#79C0FF",
    "Kotlin": "#A97BFF", "Go": "#00ADD8", "Rust": "#DEA584", "SQL": "#E38C00",
    "Vue": "#41B883", "Svelte": "#FF3E00", "PHP": "#4F5D95", "Ruby": "#701516",
    "C#": "#178600",
}
FALLBACK_COLORS = ["#2EA043", "#D2A8FF", "#FFA657", "#79C0FF", "#FF7B72", "#7EE787"]


# --------------------------------------------------------------------------- #
# File classification
# --------------------------------------------------------------------------- #

def language_for(path):
    name = os.path.basename(str(path)).lower()
    if name in LANGUAGE_BY_FILENAME:
        return LANGUAGE_BY_FILENAME[name]
    return LANGUAGE_BY_EXTENSION.get(os.path.splitext(name)[1])


def is_excluded(path):
    parts = str(path).replace("\\", "/").split("/")
    if any(part in EXCLUDED_DIRS for part in parts[:-1]):
        return True
    base = parts[-1].lower()
    return base in LOCKFILES or ".min." in base


def classify(path):
    """Language name when the file counts toward code stats, otherwise None."""
    if is_excluded(path):
        return None
    return language_for(path)


# --------------------------------------------------------------------------- #
# Live codebase counting
# --------------------------------------------------------------------------- #

def count_physical_lines(data):
    if not data:
        return 0
    return data.count(b"\n") + (0 if data.endswith(b"\n") else 1)


def count_notebook_lines(data):
    try:
        notebook = json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return 0
    total = 0
    for cell in notebook.get("cells", []):
        if cell.get("cell_type") != "code":
            continue
        source = cell.get("source", [])
        if isinstance(source, str):
            source = source.splitlines()
        total += sum(1 for line in source if line.strip())
    return total


def count_live(repo_dir):
    """Physical lines per language currently in the working tree."""
    totals = defaultdict(int)
    repo_dir = Path(repo_dir)
    for current, dirs, files in os.walk(repo_dir):
        dirs[:] = [d for d in dirs if d not in EXCLUDED_DIRS]
        for filename in files:
            full = Path(current) / filename
            rel = full.relative_to(repo_dir).as_posix()
            language = classify(rel)
            if not language:
                continue
            try:
                if full.is_symlink() or full.stat().st_size > MAX_FILE_BYTES:
                    continue
                data = full.read_bytes()
            except OSError:
                continue
            if b"\0" in data[:8192]:
                continue
            if language == "Jupyter Notebook":
                lines = count_notebook_lines(data)
            else:
                lines = count_physical_lines(data)
                if lines and len(data) / lines > 400:
                    continue  # minified or generated single-line blobs
            if lines:
                totals[language] += lines
    return dict(totals)


# --------------------------------------------------------------------------- #
# Authored lines from git history
# --------------------------------------------------------------------------- #

def normalize_numstat_path(path):
    if "=>" not in path:
        return path
    if "{" in path and "}" in path:
        head, rest = path.split("{", 1)
        inner, tail = rest.split("}", 1)
        new = inner.split(" => ")[-1]
        return (head + new + tail).replace("//", "/")
    return path.split(" => ")[-1]


def parse_numstat(log_text, since):
    """Parse `git log --numstat --format=@@%aI` output."""
    total = 0
    last_window = 0
    by_month = defaultdict(int)
    by_language = defaultdict(int)
    commit_day = None
    for line in log_text.splitlines():
        if line.startswith("@@"):
            commit_day = line[2:12]
            continue
        parts = line.split("\t")
        if len(parts) != 3 or commit_day is None:
            continue
        added, _deleted, raw_path = parts
        if not added.isdigit():
            continue
        added = int(added)
        language = classify(normalize_numstat_path(raw_path))
        if not language or language == "Jupyter Notebook" or added > MAX_FILE_ADDITIONS:
            continue  # notebooks are raw JSON in git history, so their diffs are not code lines
        total += added
        by_month[commit_day[:7]] += added
        by_language[language] += added
        if commit_day >= since:
            last_window += added
    return {
        "total": total,
        "last_30_days": last_window,
        "by_month": dict(by_month),
        "by_language": dict(by_language),
    }


def authored_stats(repo_dir, patterns, today=None):
    today = today or date.today()
    since = (today - timedelta(days=30)).isoformat()
    command = [
        "git", "-C", str(repo_dir), "log", "--no-merges", "--regexp-ignore-case",
        "--numstat", "--format=@@%aI",
    ]
    for pattern in patterns:
        command += ["--author", pattern]
    result = subprocess.run(
        command, capture_output=True, text=True, errors="replace",
        timeout=GIT_TIMEOUT, check=False,
    )
    if result.returncode != 0:
        return parse_numstat("", since)
    return parse_numstat(result.stdout, since)


# --------------------------------------------------------------------------- #
# GitHub access
# --------------------------------------------------------------------------- #

def api_get(url):
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "profile-codebase-analytics",
    }
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def paged(url):
    items = []
    page = 1
    while True:
        batch = api_get(f"{url}{'&' if '?' in url else '?'}per_page=100&page={page}")
        if not batch:
            break
        items.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return items


def keep_repo(repo):
    if repo.get("fork") and not INCLUDE_FORKS:
        return False
    if repo.get("disabled") or repo["name"].lower() in EXCLUDE_REPOS:
        return False
    return True


def list_repos():
    repos = None
    if TOKEN and INCLUDE_PRIVATE:
        try:
            owned = paged("https://api.github.com/user/repos?affiliation=owner&visibility=all")
            repos = [r for r in owned if r["owner"]["login"].lower() == USERNAME.lower()]
        except urllib.error.HTTPError as error:
            print(
                f"private repo listing unavailable (HTTP {error.code}); "
                "falling back to public repositories",
                file=sys.stderr,
            )
    if repos is None:
        repos = paged(f"https://api.github.com/users/{USERNAME}/repos?type=owner")
    return [repo for repo in repos if keep_repo(repo)]


def clone_repo(repo, destination):
    command = ["git"]
    if TOKEN:
        credentials = base64.b64encode(f"x-access-token:{TOKEN}".encode()).decode()
        command += ["-c", f"http.extraheader=AUTHORIZATION: basic {credentials}"]
    command += ["clone", "--quiet", "--no-tags", repo["clone_url"], str(destination)]
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    subprocess.run(
        command, check=True, capture_output=True, timeout=GIT_TIMEOUT, env=env,
    )


def analyze_repo(repo, workdir):
    destination = Path(workdir) / str(repo["id"])
    try:
        clone_repo(repo, destination)
        live = count_live(destination)
        authored = authored_stats(destination, AUTHOR_PATTERNS)
    finally:
        shutil.rmtree(destination, ignore_errors=True)
    return {
        "name": repo["name"],
        "private": bool(repo.get("private")),
        "live_by_language": live,
        "live_lines": sum(live.values()),
        "authored": authored,
    }


# --------------------------------------------------------------------------- #
# Report assembly
# --------------------------------------------------------------------------- #

def month_keys(today, count=MONTHS):
    year, month = today.year, today.month
    keys = []
    for _ in range(count):
        keys.append(f"{year:04d}-{month:02d}")
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    return list(reversed(keys))


def build_report(records, today=None, show_private_names=None):
    today = today or date.today()
    if show_private_names is None:
        show_private_names = SHOW_PRIVATE_NAMES

    ranked = sorted(records, key=lambda r: r["live_lines"], reverse=True)
    private_index = 0
    repositories = []
    for record in ranked:
        name = record["name"]
        if record["private"] and not show_private_names:
            private_index += 1
            name = f"private-project-{private_index}"
        repositories.append({
            "name": name,
            "private": record["private"],
            "live_lines": record["live_lines"],
            "authored_lines": record["authored"]["total"],
        })

    languages = defaultdict(int)
    monthly = defaultdict(int)
    authored_total = 0
    last_window = 0
    for record in records:
        for language, lines in record["live_by_language"].items():
            languages[language] += lines
        for month, lines in record["authored"]["by_month"].items():
            monthly[month] += lines
        authored_total += record["authored"]["total"]
        last_window += record["authored"]["last_30_days"]

    ranked_languages = sorted(languages.items(), key=lambda item: item[1], reverse=True)
    private_count = sum(1 for record in records if record["private"])
    return {
        "generated": today.isoformat(),
        "totals": {
            "authored_lines": authored_total,
            "live_lines": sum(r["live_lines"] for r in records),
            "authored_last_30_days": last_window,
            "repositories": len(records),
            "public": len(records) - private_count,
            "private": private_count,
        },
        "top_repositories": repositories[:TOP_REPOS],
        "languages": [
            {"name": name, "lines": lines} for name, lines in ranked_languages[:TOP_LANGUAGES]
        ],
        "monthly_authored": [
            {"month": key, "lines": monthly.get(key, 0)} for key in month_keys(today)
        ],
    }


# --------------------------------------------------------------------------- #
# SVG rendering
# --------------------------------------------------------------------------- #

WIDTH = 860
HEIGHT = 500
MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def esc(value):
    return html.escape(str(value), quote=True)


def fmt(value):
    return f"{int(value):,}"


def compact(value):
    value = int(value)
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}K"
    return str(value)


def truncate(text, limit):
    return text if len(text) <= limit else text[: limit - 1] + "…"


def color_for(language, index):
    return LANGUAGE_COLORS.get(language) or FALLBACK_COLORS[index % len(FALLBACK_COLORS)]


def svg_open(title, aria, height=HEIGHT):
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{height}" viewBox="0 0 {WIDTH} {height}" role="img" aria-label="{esc(aria)}">
  <style>
    text {{ font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace; }}
    .title {{ fill: #8b949e; font-size: 12px; }}
    .label {{ fill: #8b949e; font-size: 10px; letter-spacing: 1px; }}
    .section {{ fill: #8b949e; font-size: 11px; letter-spacing: 1.2px; }}
    .value {{ font-size: 24px; font-weight: 700; }}
    .sub {{ fill: #6e7681; font-size: 10px; }}
    .name {{ fill: #e6edf3; font-size: 13px; }}
    .rank {{ fill: #6e7681; font-size: 12px; }}
    .num {{ fill: #8b949e; font-size: 12px; }}
    .month {{ fill: #6e7681; font-size: 10px; }}
    .mval {{ fill: #8b949e; font-size: 9px; }}
    .foot {{ fill: #8b949e; font-size: 11px; }}
    .accent {{ fill: #39d0d8; }}
  </style>
  <rect x="0.5" y="0.5" width="{WIDTH - 1}" height="{height - 1}" rx="10" fill="#0d1117" stroke="#30363d"/>
  <path d="M0.5 10.5 a10 10 0 0 1 10 -10 H{WIDTH - 10.5} a10 10 0 0 1 10 10 V34 H0.5 Z" fill="#161b22"/>
  <line x1="0.5" y1="34" x2="{WIDTH - 0.5}" y2="34" stroke="#30363d"/>
  <circle cx="20" cy="17" r="5" fill="#ff5f56"/>
  <circle cx="38" cy="17" r="5" fill="#ffbd2e"/>
  <circle cx="56" cy="17" r="5" fill="#27c93f"/>
  <text x="{WIDTH // 2}" y="21" class="title" text-anchor="middle">{esc(title)}</text>
"""


def render_tiles(totals):
    tiles = [
        ("LINES AUTHORED", fmt(totals["authored_lines"]), "#3fb950", "all-time · lines added by me"),
        ("LIVE CODEBASE", fmt(totals["live_lines"]), "#39d0d8", "current lines across repos"),
        ("LAST 30 DAYS", "+" + fmt(totals["authored_last_30_days"]), "#ffa657", "lines authored"),
        ("REPOSITORIES", fmt(totals["repositories"]), "#d2a8ff",
         f"{totals['public']} public · {totals['private']} private"),
    ]
    gap = 12
    tile_w = (WIDTH - 40 - gap * 3) / 4
    out = []
    for index, (label, value, color, sub) in enumerate(tiles):
        x = 20 + index * (tile_w + gap)
        out.append(
            f'<rect x="{x:.1f}" y="50" width="{tile_w:.1f}" height="70" rx="6" fill="#161b22" stroke="#21262d"/>'
            f'<text x="{x + 14:.1f}" y="70" class="label">{label}</text>'
            f'<text x="{x + 14:.1f}" y="98" class="value" fill="{color}">{esc(value)}</text>'
            f'<text x="{x + 14:.1f}" y="113" class="sub">{esc(sub)}</text>'
        )
    return "\n  ".join(out)


def render_repo_rows(repositories):
    if not repositories:
        return ""
    peak = max(r["live_lines"] for r in repositories) or 1
    rows = []
    for index, repo in enumerate(repositories):
        y = 182 + index * 26
        width = max(repo["live_lines"] / peak * 120, 3)
        fill = "#3fb950" if index == 0 else "#2ea04366"
        rows.append(
            f'<text x="20" y="{y}" class="rank">{index + 1:02d}</text>'
            f'<text x="48" y="{y}" class="name">{esc(truncate(repo["name"], 22))}</text>'
            f'<rect x="236" y="{y - 8}" width="120" height="6" rx="3" fill="#161b22"/>'
            f'<rect x="236" y="{y - 8}" width="{width:.1f}" height="6" rx="3" fill="{fill}"/>'
            f'<text x="412" y="{y}" class="num" text-anchor="end">{fmt(repo["live_lines"])}</text>'
        )
    return "\n  ".join(rows)


def render_language_rows(languages):
    if not languages:
        return ""
    peak = max(item["lines"] for item in languages) or 1
    rows = []
    for index, item in enumerate(languages):
        y = 182 + index * 26
        color = color_for(item["name"], index)
        width = max(item["lines"] / peak * 120, 3)
        rows.append(
            f'<rect x="452" y="{y - 9}" width="10" height="10" rx="2" fill="{color}"/>'
            f'<text x="472" y="{y}" class="name">{esc(truncate(item["name"], 16))}</text>'
            f'<rect x="622" y="{y - 8}" width="120" height="6" rx="3" fill="#161b22"/>'
            f'<rect x="622" y="{y - 8}" width="{width:.1f}" height="6" rx="3" fill="{color}"/>'
            f'<text x="840" y="{y}" class="num" text-anchor="end">{fmt(item["lines"])}</text>'
        )
    return "\n  ".join(rows)


def render_monthly(monthly):
    baseline = 428
    max_height = 56
    peak = max((m["lines"] for m in monthly), default=0) or 1
    slot = (WIDTH - 40) / max(len(monthly), 1)
    bar_w = 34
    out = []
    for index, item in enumerate(monthly):
        cx = 20 + slot * index + slot / 2
        height = item["lines"] / peak * max_height
        if item["lines"] > 0:
            height = max(height, 2)
        top = baseline - height
        month_label = MONTH_NAMES[int(item["month"][5:7]) - 1]
        out.append(
            f'<rect x="{cx - bar_w / 2:.1f}" y="{baseline - max_height}" width="{bar_w}" height="{max_height}" rx="3" fill="#161b22"/>'
            f'<rect x="{cx - bar_w / 2:.1f}" y="{top:.1f}" width="{bar_w}" height="{height:.1f}" rx="3" fill="#3fb950"/>'
            f'<text x="{cx:.1f}" y="{baseline - max_height - 5}" class="mval" text-anchor="middle">{compact(item["lines"]) if item["lines"] else "·"}</text>'
            f'<text x="{cx:.1f}" y="{baseline + 16}" class="month" text-anchor="middle">{month_label}</text>'
        )
    return "\n  ".join(out)


def render_svg(report):
    totals = report["totals"]
    return (
        svg_open(f"{USERNAME}@github: ~/codebase --analytics",
                 "Codebase analytics: lines authored, live codebase, top repositories and languages")
        + f"""  {render_tiles(totals)}
  <text x="20" y="150" class="section">TOP REPOSITORIES BY LINES</text>
  <text x="452" y="150" class="section">LINES BY LANGUAGE</text>
  <line x1="20" y1="158" x2="412" y2="158" stroke="#21262d"/>
  <line x1="452" y1="158" x2="840" y2="158" stroke="#21262d"/>
  {render_repo_rows(report["top_repositories"])}
  {render_language_rows(report["languages"])}
  <text x="20" y="344" class="section">LINES AUTHORED PER MONTH · LAST {len(report["monthly_authored"])} MONTHS</text>
  <line x1="20" y1="352" x2="840" y2="352" stroke="#21262d"/>
  {render_monthly(report["monthly_authored"])}
  <line x1="20" y1="{HEIGHT - 40}" x2="{WIDTH - 20}" y2="{HEIGHT - 40}" stroke="#21262d"/>
  <text x="20" y="{HEIGHT - 16}" class="foot"><tspan class="accent">{totals["repositories"]}</tspan> repositories analyzed · forks, vendored and generated files excluded</text>
  <text x="{WIDTH - 20}" y="{HEIGHT - 16}" class="foot" text-anchor="end">updated {esc(report["generated"])}</text>
</svg>
"""
    )


def render_placeholder():
    return (
        svg_open(f"{USERNAME}@github: ~/codebase --analytics",
                 "Codebase analytics, first refresh pending")
        + f"""  <text x="{WIDTH // 2}" y="{HEIGHT // 2 - 4}" class="name" text-anchor="middle">Codebase analytics</text>
  <text x="{WIDTH // 2}" y="{HEIGHT // 2 + 18}" class="sub" text-anchor="middle">First refresh is pending. The daily workflow fills this card automatically.</text>
</svg>
"""
    )


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #

def render_overview(report):
    """Reuse the verified tiles without changing their methodologies."""
    header = svg_open("Codebase · filtered lines", "Codebase headline metrics", height=156)
    return (header + render_tiles(report["totals"]) +
            f'<text x="20" y="143" class="foot">Snapshot {esc(report["generated"])} · detailed rankings and monthly activity below</text></svg>')


def write_outputs(report):
    SVG_PATH.parent.mkdir(parents=True, exist_ok=True)
    JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    SVG_PATH.write_text(render_svg(report), encoding="utf-8")
    (SVG_PATH.parent / "codebase-overview.svg").write_text(render_overview(report), encoding="utf-8")
    JSON_PATH.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


def main():
    repos = list_repos()
    if not repos:
        print("no repositories found", file=sys.stderr)
        sys.exit(1)

    records = []
    skipped = 0
    with tempfile.TemporaryDirectory() as workdir:
        for repo in repos:
            label = "a private repository" if repo.get("private") else repo["name"]
            try:
                records.append(analyze_repo(repo, workdir))
            except (subprocess.SubprocessError, OSError) as error:
                skipped += 1
                print(f"skipped {label}: {type(error).__name__}", file=sys.stderr)

    if not records:
        print("no repository could be analyzed", file=sys.stderr)
        sys.exit(1)

    report = build_report(records)
    write_outputs(report)
    totals = report["totals"]
    print(
        f"wrote {SVG_PATH.name}: {totals['repositories']} repos "
        f"({totals['private']} private, {skipped} skipped), "
        f"{totals['authored_lines']:,} lines authored, {totals['live_lines']:,} live"
    )


if __name__ == "__main__":
    main()
