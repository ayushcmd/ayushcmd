"""Compact account statistics from the existing contribution snapshot."""
import json
from pathlib import Path
from html import escape
ROOT = Path(__file__).resolve().parent.parent
OUT_PATH = ROOT / "assets/info-card.svg"

def render_info_card(data, out_path=OUT_PATH):
    stats = data.get("github_stats", {})
    metrics = [
        ("COMMITS", "commits_365d", "Trailing 365-day search"),
        ("REPOSITORIES", "public_repos", "Public repositories"),
        ("STARS EARNED", "total_stars", "Across public repos"),
        ("PULL REQUESTS", "total_prs", "Authored PRs · all time"),
        ("CODE REVIEWS", "code_reviews", "PRs reviewed · all time"),
        ("ISSUES", "total_issues", "Created issues · all time"),
    ]
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="440" height="300" viewBox="0 0 440 300" font-family="Consolas, monospace">',
             '<rect x="0.5" y="0.5" width="439" height="299" rx="10" fill="#0D1117" stroke="#30363d"/>',
             '<text x="220" y="19" fill="#8b949e" font-size="11" text-anchor="middle">Account activity</text>']
    for i, color in enumerate(["#ff5f56", "#ffbd2e", "#27c93f"]):
        parts.append(f'<circle cx="{16+i*14}" cy="13" r="4" fill="{color}"/>')
    for i, (label, key, sub) in enumerate(metrics):
        if key == "total_prs" and stats.get("merged_prs") is not None:
            sub = f"Authored · {stats['merged_prs']:,} merged"
        x, y = 12 + (i % 2)*214, 38 + (i//2)*78
        value = stats.get(key)
        value = f"{value:,}" if value is not None else "Unavailable"
        color = "#f2cc60" if key == "total_stars" else "#22d3ee"
        parts.append(f'<rect x="{x}" y="{y}" width="202" height="68" rx="5" fill="#161B22"/>')
        for offset, size, fill, text in [(17,11,"#8b949e",label),(40,22,color,value),(58,10,"#8b949e",sub)]:
            parts.append(f'<text x="{x+10}" y="{y+offset}" fill="{fill}" font-size="{size}">{escape(text)}</text>')
    parts.append('<text x="12" y="288" fill="#8b949e" font-size="10">Public activity · unavailable values are not zero</text></svg>')
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text("\n".join(parts), encoding="utf-8")

def main():
    render_info_card(json.loads((ROOT / "data/contributions.json").read_text(encoding="utf-8")))

if __name__ == "__main__":
    main()
