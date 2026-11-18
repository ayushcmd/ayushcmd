#!/usr/bin/env python3
"""Render a clean, functional SVG live status card from `data/status_resolved.json`.

Reads the machine-readable resolved status output (`data/status_resolved.json`),
validates that it represents one of the six supported statuses without any
placeholders or schema errors, and writes `img/status/available.svg` only when
the rendered SVG content changes.

Uses only the Python standard library.
"""

from __future__ import annotations

import argparse
import json
import sys
from html import escape
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from status_engine import (  # noqa: E402
    is_placeholder_value,
    load_json_file,
    validate_resolved_status,
)

DEFAULT_IN_PATH = REPO_ROOT / "data" / "status_resolved.json"
DEFAULT_OUT_PATH = REPO_ROOT / "img" / "status" / "available.svg"

SUPPORTED_STATUS_IDS = frozenset({
    "available",
    "offline",
    "exam",
    "travelling",
    "working",
    "sleeping",
})

AVAILABILITY_COLORS = {
    "available": "#3fb950",
    "focused": "#58a6ff",
    "busy": "#f85149",
    "limited": "#f0883e",
    "offline": "#8b949e",
}

AVAILABILITY_LABELS = {
    "available": "AVAILABLE",
    "focused": "FOCUSED",
    "busy": "BUSY",
    "limited": "LIMITED",
    "offline": "OFFLINE",
}


def validate_renderable_status(data: Any) -> list[str]:
    """Validate that resolved status JSON is safe to render into a public SVG."""
    errors = validate_resolved_status(data)
    if errors:
        return errors

    status_id = data.get("status")
    if status_id not in SUPPORTED_STATUS_IDS:
        errors.append(
            f"Unsupported status ID {status_id!r}; expected one of {sorted(SUPPORTED_STATUS_IDS)}."
        )

    for field in ("status", "title", "message", "availability"):
        val = data.get(field)
        if not isinstance(val, str) or not val.strip() or is_placeholder_value(val):
            errors.append(f"Refusing to render placeholder or empty `{field}` ({val!r}).")

    until_val = data.get("until")
    if until_val is not None and is_placeholder_value(until_val):
        errors.append(f"Refusing to render placeholder `until` ({until_val!r}).")

    return errors


def _truncate(text: str, max_len: int) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= max_len:
        return cleaned
    return cleaned[: max_len - 1].rstrip() + "…"


def format_expiry_text(data: dict[str, Any]) -> str:
    """Build clear, human-readable expiry or schedule text from resolved status."""
    source = data.get("resolution_source")
    until = data.get("until")
    computed = data.get("computed") or {}
    until_display = computed.get("until_display") or (f"{until} IST" if until else None)

    if source == "night_override":
        end_label = until_display or "06:00 IST"
        return f"Auto night mode (22:00–06:00 IST) · Ends at {end_label}"

    if source == "manual" and not data.get("expired") and until_display:
        return f"Expires at {until_display}"

    return "Expired · Offline until next update"


def render_status_svg_markup(data: dict[str, Any]) -> str:
    """Render deterministic, valid XML SVG markup for a validated resolved status."""
    errors = validate_renderable_status(data)
    if errors:
        raise ValueError("Cannot render invalid status data:\n" + "\n".join(f"- {e}" for e in errors))

    status_id: str = data["status"]
    emoji: str = data["emoji"]
    title: str = _truncate(data["title"], 38)
    availability: str = data["availability"]
    message: str = _truncate(data["message"], 82)
    project: str | None = data.get("project")
    tags: list[str] = [str(t) for t in data.get("tags", [])[:2]]
    accent: str = data.get("color") or AVAILABILITY_COLORS.get(availability, "#8b949e")
    avail_color: str = AVAILABILITY_COLORS.get(availability, accent)
    avail_label: str = AVAILABILITY_LABELS.get(availability, availability.upper())
    expiry_text: str = format_expiry_text(data)
    source: str = data["resolution_source"]

    # Availability pill sizing (top-right inside card)
    pill_width = max(98, len(avail_label) * 8 + 34)
    pill_x = 828 - pill_width
    pill_dot_x = pill_x + 15
    pill_text_x = pill_x + 28

    # Secondary detail row: project (for `working`) or tag pills
    detail_parts: list[str] = []
    if project:
        proj_str = _truncate(project, 76)
        detail_parts.append(
            f'<text x="114" y="122" fill="#8b949e" font-size="11.5">'
            f'<tspan fill="{escape(accent, quote=True)}" font-weight="600">PROJECT</tspan>'
            f'  {escape(proj_str)}</text>'
        )
    else:
        tag_x = 114
        for tag in tags:
            t_label = _truncate(tag, 18)
            t_width = max(56, len(t_label) * 7 + 20)
            detail_parts.append(
                f'<rect x="{tag_x}" y="109" width="{t_width}" height="20" rx="10" '
                f'fill="#161b22" stroke="#30363d"/>'
                f'<text x="{tag_x + t_width / 2:.1f}" y="122.5" fill="#c9d1d9" '
                f'font-size="10.5" text-anchor="middle">{escape(t_label)}</text>'
            )
            tag_x += t_width + 8

    # Footer right metadata badge
    if source == "night_override":
        mode_badge = "SCHEDULED · NIGHT"
    elif source == "offline_fallback":
        mode_badge = "FALLBACK · OFFLINE"
    else:
        mode_badge = f"STATUS · {status_id.upper()}"

    aria_label = (
        f"Ayush Live Status: {title} ({avail_label}) — {message} ({expiry_text})"
    )

    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="860" height="172" '
        f'viewBox="0 0 860 172" font-family="Consolas, \'Segoe UI\', monospace" '
        f'role="img" aria-label="{escape(aria_label, quote=True)}">',
        f'  <title>{escape(aria_label)}</title>',
        '  <rect x="0.5" y="0.5" width="859" height="171" rx="10" fill="#0d1117" stroke="#30363d"/>',
        '  <path d="M1 30H859" stroke="#30363d"/>',
        '  <circle cx="16" cy="15" r="4.5" fill="#ff5f56"/>',
        '  <circle cx="30" cy="15" r="4.5" fill="#ffbd2e"/>',
        '  <circle cx="44" cy="15" r="4.5" fill="#27c93f"/>',
        '  <text x="430" y="19" fill="#c9d1d9" font-size="11.5" text-anchor="middle">ayushcmd@github: ~/status --live</text>',
        '  <text x="844" y="19" fill="#8b949e" font-size="10.5" text-anchor="end">IST · Asia/Kolkata</text>',
        '  <rect x="14" y="42" width="832" height="116" rx="8" fill="#10161e" stroke="#30363d"/>',
        f'  <rect x="14" y="42" width="5" height="116" rx="2.5" fill="{escape(accent, quote=True)}"/>',
        f'  <rect x="32" y="58" width="64" height="64" rx="12" fill="{escape(accent, quote=True)}" '
        f'fill-opacity="0.14" stroke="{escape(accent, quote=True)}" stroke-opacity="0.55"/>',
        f'  <text x="64" y="100" font-size="30" text-anchor="middle">{escape(emoji)}</text>',
        f'  <circle cx="88" cy="114" r="6" fill="#10161e"/>',
        f'  <circle cx="88" cy="114" r="4" fill="{escape(avail_color, quote=True)}"/>',
        f'  <text x="114" y="76" fill="#e6edf3" font-size="22" font-weight="700">{escape(title)}</text>',
        f'  <rect x="{pill_x}" y="56" width="{pill_width}" height="24" rx="12" '
        f'fill="{escape(avail_color, quote=True)}" fill-opacity="0.16" '
        f'stroke="{escape(avail_color, quote=True)}" stroke-opacity="0.6"/>',
        f'  <circle cx="{pill_dot_x}" cy="68" r="4" fill="{escape(avail_color, quote=True)}"/>',
        f'  <text x="{pill_text_x}" y="72" fill="{escape(avail_color, quote=True)}" '
        f'font-size="11" font-weight="700">{escape(avail_label)}</text>',
        f'  <text x="114" y="99" fill="#c9d1d9" font-size="13">{escape(message)}</text>',
        *(f"  {part}" for part in detail_parts),
        '  <path d="M32 134H828" stroke="#21262d"/>',
        f'  <text x="32" y="149" fill="#8b949e" font-size="11">{escape(expiry_text)}</text>',
        f'  <text x="828" y="149" fill="#6e7681" font-size="10.5" text-anchor="end">{escape(mode_badge)}</text>',
        '</svg>',
    ]
    return "\n".join(lines) + "\n"


def render_status_svg_file(
    resolved_data: dict[str, Any],
    out_path: Path | str = DEFAULT_OUT_PATH,
    *,
    force: bool = False,
) -> bool:
    """Render SVG markup and write `out_path` only when content changed."""
    svg_text = render_status_svg_markup(resolved_data)
    target = Path(out_path)
    if not force and target.exists():
        try:
            if target.read_text(encoding="utf-8") == svg_text:
                return False
        except OSError:
            pass

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(svg_text, encoding="utf-8")
    return True


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Render Ayush Live Status SVG from data/status_resolved.json."
    )
    parser.add_argument(
        "--in-file",
        type=Path,
        default=DEFAULT_IN_PATH,
        help="Path to resolved status JSON (default: data/status_resolved.json).",
    )
    parser.add_argument(
        "--out-file",
        type=Path,
        default=DEFAULT_OUT_PATH,
        help="Path to output SVG file (default: img/status/available.svg).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Rewrite output SVG even if content is identical.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.in_file.exists():
        print(f"ERROR: Resolved status JSON not found at {args.in_file}.", file=sys.stderr)
        return 1

    try:
        data = load_json_file(args.in_file)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: Failed to read {args.in_file}: {exc}", file=sys.stderr)
        return 1

    try:
        wrote = render_status_svg_file(data, args.out_file, force=args.force)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    action = "updated" if wrote else "unchanged"
    print(
        f"Live status SVG ({action}): {args.out_file} "
        f"[status={data['status']}, availability={data['availability']}]"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
