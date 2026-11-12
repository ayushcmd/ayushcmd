"""Render monthly contributions and six verified GitHub account metrics."""
import json
from html import escape
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
OUT_PATH = ROOT / 'assets/info-card.svg'
METRICS = [
    (['TOTAL COMMITS'], 'commits_365d', '#bc8cff', 'Trailing 365-day commit search'),
    (['PUBLIC REPOS'], 'public_repos', '#22d3ee', 'Public repositories'),
    (['CODE REVIEWS'], 'code_reviews', '#f0883e', 'PRs reviewed, not individual reviews'),
    (['PULL REQUESTS'], 'total_prs', '#39d353', 'Authored pull requests, all time'),
    (['ISSUES'], 'total_issues', '#ff6b6b', 'Authored issues, all time'),
    (['ACCEPTED', 'DISCUSSION ANSWERS'], 'accepted_discussion_answers', '#f2cc60', 'Accepted discussion answers in API-visible repositories'),
]
ICONS = [
    '<circle cx="7" cy="5" r="3"/><circle cx="7" cy="23" r="3"/><circle cx="23" cy="5" r="3"/><path d="M7 8v12m0-6c12 0 16-2 16-6"/>',
    '<path d="m2 9 13-7 13 7-13 7ZM2 15l13 7 13-7M2 21l13 7 13-7"/>',
    '<path d="m10 6-8 9 8 9m10-18 8 9-8 9m-3-22-4 26"/>',
    '<circle cx="7" cy="6" r="3"/><circle cx="7" cy="24" r="3"/><circle cx="23" cy="24" r="3"/><path d="M7 9v12m16 0V10q0-4-7-4m0 0 4-4m-4 4 4 4"/>',
    '<circle cx="15" cy="15" r="12"/><path d="M15 7v10m0 4v2"/>',
    '<path d="M5 3h20q3 0 3 3v15q0 3-3 3h-9l-7 5v-5H5q-3 0-3-3V6q0-3 3-3Z"/><path d="m8 13 5 5 9-10"/>',
]

def recent_months(data):
    """Twelve months ending at the snapshot; absent observations stay unavailable."""
    rows = {m['key']: m for m in data.get('monthly', [])}
    end = data.get('range', {}).get('end') or (max(rows) if rows else None)
    if not end:
        return []
    year, month = map(int, end[:7].split('-'))
    names = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']
    result = []
    for offset in range(11, -1, -1):
        y, m = divmod(year*12 + month-1-offset, 12)
        key = f'{y:04d}-{m+1:02d}'
        result.append({'key':key, 'label':names[m], 'total':rows.get(key, {}).get('total')})
    return result

def render_info_card(data, out_path=OUT_PATH):
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="560" height="323" viewBox="0 0 560 323" font-family="Consolas, monospace" role="img" aria-label="Monthly contributions and account activity">',
        '<defs><linearGradient id="green" x2="0" y2="1"><stop stop-color="#39d353"/><stop offset="1" stop-color="#00a63e"/></linearGradient></defs>',
        '<rect x="0.5" y="0.5" width="559" height="322" rx="9" fill="#0D1117" stroke="#30363d"/><path d="M1 28H559" stroke="#30363d"/>',
        '<text x="280" y="19" fill="#c9d1d9" font-size="11" text-anchor="middle">ayushcmd@github: ~/account --stats</text>']
    for i, color in enumerate(['#ff5f56','#ffbd2e','#27c93f']):
        parts.append(f'<circle cx="{16+i*14}" cy="14" r="4.5" fill="{color}"/>')
    parts += ['<rect x="12" y="40" width="536" height="112" rx="7" fill="#10161e" stroke="#30363d"/>',
        '<text x="24" y="59" fill="#c9d1d9" font-size="12">MONTHLY CONTRIBUTION ACTIVITY <tspan fill="#8b949e" font-size="10">(Last 12 Months)</tspan></text>']
    months = recent_months(data)
    peak = max((m['total'] or 0 for m in months), default=0) or 1
    for i, month in enumerate(months):
        x, value = 27+i*43, month['total']
        height = max(2, value/peak*43) if value else 2
        label = str(value) if value is not None else 'N/A'
        fill = 'url(#green)' if value else '#21262d'
        parts.append(f'<g class="month"><title>{month["key"]}: {label} contributions</title><rect x="{x}" y="{125-height:.1f}" width="32" height="{height:.1f}" rx="2" fill="{fill}"/><text x="{x+16}" y="{119-height:.1f}" fill="#e6edf3" font-size="11" text-anchor="middle">{label}</text><text x="{x+16}" y="141" fill="#c9d1d9" font-size="11" text-anchor="middle">{month["label"]}</text></g>')
    if not months:
        parts.append('<text x="280" y="105" text-anchor="middle" fill="#8b949e" font-size="12">Monthly data unavailable</text>')
    for i, (labels, key, color, definition) in enumerate(METRICS):
        x, y = 12+(i%3)*182, 164+(i//3)*68
        value = data.get('github_stats', {}).get(key)
        value = f'{value:,}' if value is not None else 'N/A'
        parts.append(f'<g class="metric"><title>{escape(definition)}</title><rect x="{x}" y="{y}" width="172" height="60" rx="6" fill="#10161e" stroke="{color}" stroke-opacity="0.4"/><rect x="{x+8}" y="{y+14}" width="30" height="32" rx="6" fill="{color}" fill-opacity="0.1"/><g transform="translate({x+12} {y+18}) scale(.72)" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">{ICONS[i]}</g>')
        for line, label in enumerate(labels):
            parts.append(f'<text x="{x+46}" y="{y+18+line*12}" fill="#c9d1d9" font-size="10.5">{label}</text>')
        parts.append(f'<text x="{x+46}" y="{y+49}" fill="{color}" font-size="24" font-weight="700">{value}</text></g>')
    parts.append('<text x="12" y="311" fill="#8b949e" font-size="9.5">Commits: 365d · Reviews: PRs reviewed · Current month in progress</text></svg>')
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text('\n'.join(parts), encoding='utf-8')

def main():
    render_info_card(json.loads((ROOT / 'data/contributions.json').read_text(encoding='utf-8')))

if __name__ == '__main__':
    main()
