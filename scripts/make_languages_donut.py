import json
import math
import os
import sys
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

USERNAME = os.environ.get("GH_USERNAME", "ayushcmd")
TOKEN = os.environ.get("GITHUB_TOKEN", "")
INCLUDE_FORKS = os.environ.get("INCLUDE_FORKS", "false").lower() == "true"
OUTPUT_PATH = Path(__file__).resolve().parent.parent / "assets" / "languages-donut.svg"
MAX_LEGEND_ROWS = 7

LANGUAGE_COLORS = {
    "Python": "#3572A5",
    "TypeScript": "#3178C6",
    "Jupyter Notebook": "#DA5B0B",
    "JavaScript": "#F1E05A",
    "HTML": "#E34C26",
    "CSS": "#8250DF",
    "Dockerfile": "#4C9ED9",
    "C++": "#F34B7D",
    "C": "#7C7C7C",
    "R": "#198CE7",
    "Shell": "#89E051",
    "Java": "#B07219",
    "SCSS": "#C6538C",
    "Astro": "#FF5A03",
    "PowerShell": "#2D6BA8",
    "Batchfile": "#C1F12E",
}
FALLBACK_COLORS = ["#2EA043", "#D2A8FF", "#FFA657", "#79C0FF", "#FF7B72", "#7EE787"]
OTHER_COLOR = "#6E7681"


def api_get(url):
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "profile-languages-donut",
    }
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_repos():
    repos = []
    page = 1
    while True:
        url = (
            f"https://api.github.com/users/{USERNAME}/repos"
            f"?per_page=100&type=owner&page={page}"
        )
        batch = api_get(url)
        if not batch:
            break
        repos.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    if not INCLUDE_FORKS:
        repos = [repo for repo in repos if not repo.get("fork")]
    return repos


def fetch_language_totals(repos):
    totals = {}
    for repo in repos:
        try:
            languages = api_get(repo["languages_url"])
        except urllib.error.URLError as error:
            print(f"skip {repo['name']}: {error}", file=sys.stderr)
            continue
        for language, size in languages.items():
            totals[language] = totals.get(language, 0) + size
    return totals


def color_for(language, index):
    if language in LANGUAGE_COLORS:
        return LANGUAGE_COLORS[language]
    return FALLBACK_COLORS[index % len(FALLBACK_COLORS)]


def group_languages(totals):
    ordered = sorted(totals.items(), key=lambda item: item[1], reverse=True)
    grand_total = sum(size for _, size in ordered)
    head = ordered[:MAX_LEGEND_ROWS]
    tail = ordered[MAX_LEGEND_ROWS:]
    rows = []
    for index, (language, size) in enumerate(head):
        rows.append((language, size / grand_total * 100, color_for(language, index)))
    if tail:
        other_size = sum(size for _, size in tail)
        rows.append((f"Other ({len(tail)})", other_size / grand_total * 100, OTHER_COLOR))
    return rows


def build_arcs(rows, cx, cy, radius, stroke_width):
    circumference = 2 * math.pi * radius
    gap = 3.0 if len(rows) > 1 else 0.0
    arcs = []
    offset = 0.0
    for language, percent, color in rows:
        length = circumference * percent / 100
        visible = max(length - gap, 0.5)
        arcs.append(
            f'<circle cx="{cx}" cy="{cy}" r="{radius}" fill="none" stroke="{color}" '
            f'stroke-width="{stroke_width}" stroke-dasharray="{visible:.2f} {circumference - visible:.2f}" '
            f'stroke-dashoffset="{-offset:.2f}" transform="rotate(-90 {cx} {cy})"/>'
        )
        offset += length
    return "\n    ".join(arcs)


def build_legend(rows, x, y, row_height):
    max_percent = max(percent for _, percent, _ in rows)
    lines = []
    for index, (language, percent, color) in enumerate(rows):
        row_y = y + index * row_height
        bar_width = max(percent / max_percent * 150, 3)
        lines.append(
            f'<rect x="{x}" y="{row_y - 9}" width="10" height="10" rx="2" fill="{color}"/>'
            f'<text x="{x + 20}" y="{row_y}" class="lang">{language}</text>'
            f'<rect x="{x + 190}" y="{row_y - 8}" width="150" height="6" rx="3" fill="#161b22"/>'
            f'<rect x="{x + 190}" y="{row_y - 8}" width="{bar_width:.1f}" height="6" rx="3" fill="{color}"/>'
            f'<text x="{x + 420}" y="{row_y}" class="pct" text-anchor="end">{percent:.1f}%</text>'
        )
    return "\n    ".join(lines)


def build_svg(totals, repo_count):
    rows = group_languages(totals)
    width = 860
    height = 350
    cx, cy = 200, 172
    radius, stroke_width = 80, 28
    top_language, top_percent, _ = rows[0]
    language_count = len(totals)
    total_kb = sum(totals.values()) / 1024

    arcs = build_arcs(rows, cx, cy, radius, stroke_width)
    legend = build_legend(rows, 380, 90, 27)

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-label="Most used languages across all repositories">
  <style>
    text {{ font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace; }}
    .title {{ fill: #8b949e; font-size: 12px; }}
    .lang {{ fill: #e6edf3; font-size: 13px; }}
    .pct {{ fill: #8b949e; font-size: 13px; }}
    .big {{ fill: #e6edf3; font-size: 26px; font-weight: 700; }}
    .small {{ fill: #8b949e; font-size: 11px; }}
    .foot {{ fill: #8b949e; font-size: 12px; }}
    .accent {{ fill: #39d0d8; font-size: 12px; }}
  </style>
  <rect x="0.5" y="0.5" width="{width - 1}" height="{height - 1}" rx="10" fill="#0d1117" stroke="#30363d"/>
  <path d="M0.5 10.5 a10 10 0 0 1 10 -10 H{width - 10.5} a10 10 0 0 1 10 10 V34 H0.5 Z" fill="#161b22"/>
  <line x1="0.5" y1="34" x2="{width - 0.5}" y2="34" stroke="#30363d"/>
  <circle cx="20" cy="17" r="5" fill="#ff5f56"/>
  <circle cx="38" cy="17" r="5" fill="#ffbd2e"/>
  <circle cx="56" cy="17" r="5" fill="#27c93f"/>
  <text x="{width // 2}" y="21" class="title" text-anchor="middle">{USERNAME}@github: ~/languages --donut</text>
  <g>
    <circle cx="{cx}" cy="{cy}" r="{radius}" fill="none" stroke="#161b22" stroke-width="{stroke_width}"/>
    {arcs}
    <text x="{cx}" y="{cy + 2}" class="big" text-anchor="middle">{top_percent:.0f}%</text>
    <text x="{cx}" y="{cy + 22}" class="small" text-anchor="middle">{top_language}</text>
  </g>
  <g>
    {legend}
  </g>
  <line x1="20" y1="{height - 40}" x2="{width - 20}" y2="{height - 40}" stroke="#21262d"/>
  <text x="20" y="{height - 16}" class="foot"><tspan class="accent">{repo_count}</tspan> repositories · <tspan class="accent">{language_count}</tspan> languages · <tspan class="accent">{total_kb:,.0f} KB</tspan> of code</text>
  <text x="{width - 20}" y="{height - 16}" class="foot" text-anchor="end">updated {date.today().isoformat()}</text>
</svg>
"""


def main():
    repos = fetch_repos()
    totals = fetch_language_totals(repos)
    if not totals:
        print("no language data found", file=sys.stderr)
        sys.exit(1)
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(build_svg(totals, len(repos)), encoding="utf-8")
    print(f"wrote {OUTPUT_PATH} ({len(repos)} repos, {len(totals)} languages)")


if __name__ == "__main__":
    main()
