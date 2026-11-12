#!/usr/bin/env python3
"""
Generate the Star Statistics cards and their machine-readable twins.

Cards (one per GitHub account, each with its own colour theme)
- ayushcmd           gold    assets/star-stats.svg                       data/star-stats.json
- PortfolioStore99   orange  assets/star-stats-portfoliostore99.svg      data/star-stats-portfoliostore99.json
- Readmify           sky     assets/star-stats-readmify.svg              data/star-stats-readmify.json
- Automate-Workflow  red     assets/star-stats-automate-workflow.svg     data/star-stats-automate-workflow.json

What each card shows
- Total stars across every public repository of the account.
- Change in total stars since the previous daily refresh.
- Number of repositories that have at least one star, and the top repository.
- Every public repository with its own live star count, ranked by stars.

How it stays current
- The GitHub Actions workflow runs this script once every 24 hours and commits
  the refreshed cards, so the numbers always come straight from the GitHub API.
- Each data/*.json keeps a rolling daily history (last 30 days) which is used to
  compute the change since the previous refresh.
- If one account cannot be fetched, the others are still refreshed and the
  script exits with a non-zero status so the failure stays visible.

Scope rules
- Public repositories owned by the account (private repositories are never listed).
- Forks are included by default so the personal total matches the "Stars Earned"
  tile in the statistics dashboard. Set INCLUDE_FORKS=false to hide them.

Environment
  GH_USERNAME    Personal GitHub username                 (default: ayushcmd)
  GITHUB_TOKEN   Actions token, used only to raise the API rate limit
  INCLUDE_FORKS  true|false                               (default: true)
  EXCLUDE_REPOS  comma-separated repo names to skip       (default: none)
"""
import html
import json
import math
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

USERNAME = os.environ.get("GH_USERNAME", "ayushcmd")
TOKEN = os.environ.get("GITHUB_TOKEN", "")


def env_flag(name, default):
    return os.environ.get(name, str(default)).strip().lower() == "true"


def env_list(name):
    raw = os.environ.get(name, "")
    return [item.strip() for item in raw.split(",") if item.strip()]


INCLUDE_FORKS = env_flag("INCLUDE_FORKS", True)
EXCLUDE_REPOS = {name.lower() for name in env_list("EXCLUDE_REPOS")}

HISTORY_DAYS = 30
PAGE_SIZE = 100
RETRIES = 3
RETRY_DELAY = 2

WIDTH = 860
ROW_START = 182
ROW_STEP = 24

THEMES = {
    "gold": {"accent": "#e3b341", "second": "#39d0d8", "third": "#d2a8ff", "border": "#30363d"},
    "orange": {"accent": "#ff8c1a", "second": "#ffb86b", "third": "#ffd9a8", "border": "#5c3a17"},
    "sky": {"accent": "#38bdf8", "second": "#7dd3fc", "third": "#bae6fd", "border": "#1c4a60"},
    "red": {"accent": "#f04848", "second": "#ff8a8a", "third": "#ffc2c2", "border": "#5c2328"},
}

ORGANIZATIONS = [
    ("PortfolioStore99", "orange"),
    ("Readmify", "sky"),
    ("Automate-Workflow", "red"),
]


def personal_profile():
    return {
        "owner": USERNAME,
        "slug": "star-stats",
        "theme": "gold",
        "title": f"{USERNAME}@github: ~/stars --stats",
        "heading": "ALL PUBLIC REPOSITORIES BY STARS",
    }


def organization_profile(name, theme):
    return {
        "owner": name,
        "slug": f"star-stats-{name.lower()}",
        "theme": theme,
        "title": f"{USERNAME}@github: ~/orgs/{name} --stars",
        "heading": f"{name.upper()} REPOSITORIES BY STARS",
    }


def all_profiles():
    return [personal_profile()] + [organization_profile(name, theme) for name, theme in ORGANIZATIONS]


def asset_path(profile):
    return ROOT / "assets" / f"{profile['slug']}.svg"


def data_path(profile):
    return ROOT / "data" / f"{profile['slug']}.json"


def fetch_json(url):
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "profile-star-stats",
    }
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    last_error = None
    for attempt in range(1, RETRIES + 1):
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            if error.code < 500 and error.code != 429:
                raise
            last_error = error
        except urllib.error.URLError as error:
            last_error = error
        time.sleep(RETRY_DELAY * attempt)
    raise last_error


def fetch_repos(owner=None):
    owner = owner or USERNAME
    repos = []
    page = 1
    while True:
        batch = fetch_json(
            f"https://api.github.com/users/{owner}/repos?per_page={PAGE_SIZE}&page={page}"
        )
        if not batch:
            break
        repos.extend(batch)
        if len(batch) < PAGE_SIZE:
            break
        page += 1
    return repos


def parse_repos(raw, include_forks=True, excluded=frozenset()):
    repos = []
    for item in raw:
        if item.get("private"):
            continue
        if item.get("fork") and not include_forks:
            continue
        name = item["name"]
        if name.lower() in excluded:
            continue
        repos.append({"name": name, "stars": int(item.get("stargazers_count", 0))})
    repos.sort(key=lambda repo: (-repo["stars"], repo["name"].lower()))
    return repos


def load_previous(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def build_history(previous, today, total):
    history = []
    for entry in previous.get("history", []):
        if (
            isinstance(entry, dict)
            and isinstance(entry.get("date"), str)
            and isinstance(entry.get("stars"), int)
            and entry["date"] != today
        ):
            history.append({"date": entry["date"], "stars": entry["stars"]})
    earlier = [entry for entry in history if entry["date"] < today]
    history.append({"date": today, "stars": total})
    history.sort(key=lambda entry: entry["date"])
    return history[-HISTORY_DAYS:], (earlier[-1] if earlier else None)


def build_report(repos, previous=None, today=None, owner=None):
    today = today or date.today().isoformat()
    total = sum(repo["stars"] for repo in repos)
    starred = sum(1 for repo in repos if repo["stars"] > 0)
    top = repos[0] if repos and repos[0]["stars"] > 0 else None
    history, baseline = build_history(previous or {}, today, total)
    return {
        "owner": owner or USERNAME,
        "generated": today,
        "totals": {
            "stars": total,
            "repositories": len(repos),
            "starred_repositories": starred,
        },
        "top_repository": top,
        "change": {
            "since": baseline["date"] if baseline else None,
            "stars": total - baseline["stars"] if baseline else None,
        },
        "repositories": repos,
        "history": history,
    }


def esc(value):
    return html.escape(str(value), quote=True)


def fmt(value):
    return f"{int(value):,}"


def truncate(text, limit):
    return text if len(text) <= limit else text[: limit - 1] + "…"


def star_points(cx, cy, outer, inner):
    points = []
    for index in range(10):
        radius = outer if index % 2 == 0 else inner
        angle = -math.pi / 2 + index * math.pi / 5
        points.append(f"{cx + radius * math.cos(angle):.1f},{cy + radius * math.sin(angle):.1f}")
    return " ".join(points)


def svg_open(profile, aria, height):
    theme = THEMES[profile["theme"]]
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
    .num {{ fill: {theme["accent"]}; font-size: 12px; }}
    .zero {{ fill: #6e7681; font-size: 12px; }}
    .foot {{ fill: #8b949e; font-size: 11px; }}
    .accent {{ fill: {theme["second"]}; }}
  </style>
  <rect x="0.5" y="0.5" width="{WIDTH - 1}" height="{height - 1}" rx="10" fill="#0d1117" stroke="{theme["border"]}"/>
  <path d="M0.5 10.5 a10 10 0 0 1 10 -10 H{WIDTH - 10.5} a10 10 0 0 1 10 10 V34 H0.5 Z" fill="#161b22"/>
  <line x1="0.5" y1="34" x2="{WIDTH - 0.5}" y2="34" stroke="{theme["border"]}"/>
  <circle cx="20" cy="17" r="5" fill="#ff5f56"/>
  <circle cx="38" cy="17" r="5" fill="#ffbd2e"/>
  <circle cx="56" cy="17" r="5" fill="#27c93f"/>
  <text x="{WIDTH // 2}" y="21" class="title" text-anchor="middle">{esc(profile["title"])}</text>
"""


def change_tile(change):
    if change["stars"] is None:
        return "0", "#8b949e", "first snapshot"
    delta = change["stars"]
    if delta > 0:
        return f"+{fmt(delta)}", "#3fb950", f"since {change['since']}"
    if delta < 0:
        return f"-{fmt(abs(delta))}", "#f85149", f"since {change['since']}"
    return "0", "#8b949e", f"since {change['since']}"


def render_tiles(report, theme):
    totals = report["totals"]
    top = report["top_repository"]
    change_value, change_color, change_sub = change_tile(report["change"])
    tiles = [
        ("TOTAL STARS", fmt(totals["stars"]), theme["accent"], "across public repositories"),
        ("SINCE LAST REFRESH", change_value, change_color, change_sub),
        (
            "STARRED REPOSITORIES",
            fmt(totals["starred_repositories"]),
            theme["second"],
            f"of {fmt(totals['repositories'])} have at least 1 star",
        ),
        (
            "TOP REPOSITORY",
            fmt(top["stars"]) if top else "0",
            theme["third"],
            esc(truncate(top["name"], 24)) if top else "no stars yet",
        ),
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
            f'<text x="{x + 14:.1f}" y="113" class="sub">{sub}</text>'
        )
    out.append(
        f'<polygon points="{star_points(20 + tile_w - 26, 72, 8, 3.4)}" fill="{theme["accent"]}"/>'
    )
    return "\n  ".join(out)


def render_rows(repos, theme):
    if not repos:
        return ""
    per_column = math.ceil(len(repos) / 2)
    peak = max(repo["stars"] for repo in repos) or 1
    rows = []
    for index, repo in enumerate(repos):
        column, line = divmod(index, per_column)
        y = ROW_START + line * ROW_STEP
        rank_x, name_x, bar_x, num_x = (20, 48, 236, 412) if column == 0 else (448, 476, 664, 840)
        stars = repo["stars"]
        bar = ""
        if stars > 0:
            width = max(stars / peak * 120, 3)
            fill = theme["accent"] if index == 0 else theme["accent"] + "99"
            bar = f'<rect x="{bar_x}" y="{y - 8}" width="{width:.1f}" height="6" rx="3" fill="{fill}"/>'
        rows.append(
            f'<text x="{rank_x}" y="{y}" class="rank">{index + 1:02d}</text>'
            f'<text x="{name_x}" y="{y}" class="name">{esc(truncate(repo["name"], 22))}</text>'
            f'<rect x="{bar_x}" y="{y - 8}" width="120" height="6" rx="3" fill="#161b22"/>'
            f"{bar}"
            f'<text x="{num_x}" y="{y}" class="{"num" if stars else "zero"}" text-anchor="end">{fmt(stars)}</text>'
        )
    return "\n  ".join(rows)


def card_height(repo_count):
    lines = max(math.ceil(repo_count / 2), 1)
    return ROW_START + (lines - 1) * ROW_STEP + 68


def render_svg(report, profile=None):
    profile = profile or personal_profile()
    theme = THEMES[profile["theme"]]
    totals = report["totals"]
    height = card_height(totals["repositories"])
    return (
        svg_open(profile, f"Star statistics for {profile['owner']}: total stars and stars per public repository", height)
        + f"""  {render_tiles(report, theme)}
  <text x="20" y="150" class="section">{esc(profile["heading"])}</text>
  <text x="840" y="150" class="section" text-anchor="end">{fmt(totals["repositories"])} REPOSITORIES</text>
  <line x1="20" y1="158" x2="412" y2="158" stroke="#21262d"/>
  <line x1="448" y1="158" x2="840" y2="158" stroke="#21262d"/>
  {render_rows(report["repositories"], theme)}
  <line x1="20" y1="{height - 40}" x2="{WIDTH - 20}" y2="{height - 40}" stroke="#21262d"/>
  <text x="20" y="{height - 16}" class="foot"><tspan class="accent">{fmt(totals["repositories"])}</tspan> public repositories · <tspan class="accent">{fmt(totals["starred_repositories"])}</tspan> starred · refreshed every 24 hours</text>
  <text x="{WIDTH - 20}" y="{height - 16}" class="foot" text-anchor="end">updated {esc(report["generated"])}</text>
</svg>
"""
    )


def render_placeholder(profile=None):
    profile = profile or personal_profile()
    height = 300
    return (
        svg_open(profile, f"Star statistics for {profile['owner']}, first refresh pending", height)
        + f"""  <text x="{WIDTH // 2}" y="{height // 2 + 4}" class="name" text-anchor="middle">Star statistics · {esc(profile["owner"])}</text>
  <text x="{WIDTH // 2}" y="{height // 2 + 26}" class="sub" text-anchor="middle">First refresh is pending. The daily workflow fills this card automatically.</text>
</svg>
"""
    )


def write_outputs(profile, report):
    svg_file = asset_path(profile)
    json_file = data_path(profile)
    svg_file.parent.mkdir(parents=True, exist_ok=True)
    json_file.parent.mkdir(parents=True, exist_ok=True)
    svg_file.write_text(render_svg(report, profile), encoding="utf-8")
    json_file.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


def refresh(profile):
    repos = parse_repos(fetch_repos(profile["owner"]), INCLUDE_FORKS, EXCLUDE_REPOS)
    report = build_report(repos, load_previous(data_path(profile)), owner=profile["owner"])
    write_outputs(profile, report)
    return report


def describe(profile, report):
    totals = report["totals"]
    delta = report["change"]["stars"]
    change = "first snapshot" if delta is None else f"{delta:+d} since {report['change']['since']}"
    return (
        f"wrote {asset_path(profile).name}: {profile['owner']} has {totals['stars']} stars across "
        f"{totals['repositories']} public repos ({totals['starred_repositories']} starred, {change})"
    )


def overview_totals(reports):
    orgs = [reports.get(p["owner"]) for p in all_profiles()[1:]]
    personal = reports.get(USERNAME)
    return {
        "personal": personal["totals"]["stars"] if personal else None,
        "organizations": sum(r["totals"]["stars"] for r in orgs) if all(orgs) else None,
        "tracked": len(orgs),
    }


def render_overview(reports):
    totals = overview_totals(reports)
    profile = dict(personal_profile(), title="Stars · personal + organizations")
    parts = [svg_open(profile, "Consolidated stars overview", 330).replace("fill: #6e7681; font-size: 10px", "fill: #8b949e; font-size: 11px")]
    for i, (label, key) in enumerate([("PERSONAL STARS", "personal"), ("ORGANIZATION STARS", "organizations"), ("TRACKED ORGANIZATIONS", "tracked")]):
        x = 20 + i * 280
        value = totals[key]
        value = fmt(value) if value is not None else "Unavailable"
        parts.append(f'<rect x="{x}" y="46" width="260" height="62" rx="5" fill="#161b22"/><text x="{x+12}" y="66" class="label">{label}</text><text x="{x+12}" y="94" class="value" fill="#f2cc60">{value}</text>')
    personal = reports.get(USERNAME)
    parts.append('<text x="20" y="134" class="section">PERSONAL TOP REPOSITORIES</text>')
    if personal:
        repos = sorted(personal["repositories"], key=lambda r: (-r["stars"], r["name"].lower()))[:5]
        for i, repo in enumerate(repos):
            y = 157 + i*26
            parts.append(f'<text x="20" y="{y}" class="name">{i+1:02d} {esc(truncate(repo["name"], 32))}</text><text x="402" y="{y}" class="num" text-anchor="end">{fmt(repo["stars"])}</text>')
        if not repos:
            parts.append('<text x="20" y="157" class="sub">No public repositories</text>')
        parts.append(f'<text x="20" y="310" class="sub">Snapshot {esc(personal["generated"])}</text>')
    else:
        parts.append('<text x="20" y="157" class="sub">Personal data unavailable</text>')
    for i, profile in enumerate(all_profiles()[1:]):
        y = 134 + i*60
        report = reports.get(profile["owner"])
        accent = THEMES[profile["theme"]]["accent"]
        parts.append(f'<text x="446" y="{y}" fill="{accent}" font-size="13">{esc(profile["owner"])}</text>')
        if report:
            count = report["totals"]
            top = report.get("top_repository")
            summary = f'{count["stars"]} stars · {count["repositories"]} repos · {report["generated"]}'
            detail = (f'Top: {truncate(top["name"], 29)} ({top["stars"]})' if top and count["stars"] else "No starred repositories yet")
        else:
            summary, detail = "Data unavailable", "Awaiting successful refresh"
        parts.append(f'<text x="446" y="{y+18}" class="sub">{esc(summary)}</text><text x="446" y="{y+35}" class="sub">{esc(detail)}</text>')
    parts.append('</svg>')
    return "\n".join(parts)


def write_overview():
    # Retain each last successful dated snapshot when an account fails refresh.
    reports = {p["owner"]: load_previous(data_path(p)) for p in all_profiles()}
    output = ROOT / "assets/stars-overview.svg"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_overview(reports), encoding="utf-8")


def main():
    failures = 0
    for profile in all_profiles():
        try:
            report = refresh(profile)
        except (urllib.error.URLError, json.JSONDecodeError) as error:
            failures += 1
            print(f"{profile['owner']}: could not refresh star statistics: {error}", file=sys.stderr)
            continue
        print(describe(profile, report))
    write_overview()
    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
