#!/usr/bin/env python3
"""Render the Ayush Live Status SVG card from `data/status_resolved.json`.

Reads the machine-readable resolved status output (`data/status_resolved.json`),
validates that it represents one of the six supported statuses without any
placeholders or schema errors, and writes `img/status/available.svg` only when
the rendered SVG content changes.

Uses a modular layout:
- Shared terminal frame and footer rendering
- Shared status info & availability badge rendering
- Dedicated animated monochrome pixel-art Dino Runner scene for `offline`
- Standard icon card layout for `available`, `exam`, `travelling`, `working`,
  and `sleeping`

Uses only the Python standard library; emits pure SVG with no JavaScript or
external assets for full GitHub README compatibility.
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

# Monochrome pixel-art sprites for the Offline Dino Runner scene (2px grid cells).
_DINO_BODY_GRID = (
    "...........########.",
    "..........##########",
    "..........##.#######",
    "..........##########",
    "..........##########",
    "..........#####.....",
    "..........########..",
    "#........######.....",
    "#.......########....",
    "##.....###########..",
    "###...#########..#..",
    "####.##########.....",
    "###############.....",
    ".##############.....",
    "..############......",
    "...##########.......",
    "....########........",
)

_DINO_LEG_FRAMES = (
    # Frame 1: left leg planted, right leg lifted forward
    (
        ".....###..###.......",
        ".....##....###......",
        ".....##.............",
        ".....###............",
    ),
    # Frame 2: mid-stride crossover
    (
        ".....###..###.......",
        "......##..##........",
        "......###..##.......",
        "..........###.......",
    ),
    # Frame 3: right leg planted, left leg tucked back
    (
        ".....###..###.......",
        "....###....##.......",
        "...........##.......",
        "...........###......",
    ),
)

_CACTUS_TALL_GRID = (
    "....##....",
    "....##....",
    ".##.##....",
    ".##.##.##.",
    ".##.##.##.",
    ".##.##.##.",
    ".##.##.##.",
    ".#####.##.",
    "..####.##.",
    "....#####.",
    "....####..",
    "....##....",
    "....##....",
    "....##....",
    "....##....",
)

_CACTUS_SMALL_GRID = (
    "...##....",
    "...##....",
    ".#.##.#..",
    ".#.##.#..",
    ".#.##.#..",
    ".####.#..",
    "...####..",
    "...##....",
    "...##....",
    "...##....",
    "...##....",
)

_CLOUD_LARGE_GRID = (
    "......####......",
    "....########....",
    "..############..",
    ".##############.",
    "################",
)

_CLOUD_SMALL_GRID = (
    ".....####.....",
    "...########...",
    ".############.",
    "##############",
)

_HILLS_GRID = (
    ".........##..................",
    "........####.................",
    ".......######........##......",
    "......########......####.....",
    ".....##########....######....",
    "..################.########..",
    ".###########################.",
)

_ROCK_MOUND_GRID = (
    "...###...",
    "..#####..",
    ".#######.",
)


def _pixel_grid_to_path(
    rows: tuple[str, ...],
    x0: int,
    y0: int,
    cell: int = 2,
) -> str:
    """Convert an ASCII pixel grid into a compact horizontal-run SVG path."""
    cmds: list[str] = []
    for row_idx, row in enumerate(rows):
        col = 0
        width = len(row)
        y = y0 + row_idx * cell
        while col < width:
            if row[col] != "#":
                col += 1
                continue
            start = col
            while col < width and row[col] == "#":
                col += 1
            run_w = (col - start) * cell
            x = x0 + start * cell
            cmds.append(f"M{x} {y}h{run_w}v{cell}h-{run_w}z")
    return "".join(cmds)


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


def _format_mode_badge(status_id: str, source: str) -> str:
    if source == "night_override":
        return "SCHEDULED · NIGHT"
    if source == "offline_fallback":
        return "FALLBACK · OFFLINE"
    return f"STATUS · {status_id.upper()}"


def _render_terminal_frame_open(
    aria_label: str,
    accent: str,
    *,
    defs_and_styles: list[str] | None = None,
) -> list[str]:
    """Render shared outer terminal window, header bar, and inner card container."""
    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="860" height="172" '
        f'viewBox="0 0 860 172" font-family="Consolas, \'Segoe UI\', monospace" '
        f'role="img" aria-label="{escape(aria_label, quote=True)}">',
        f'  <title>{escape(aria_label)}</title>',
    ]
    if defs_and_styles:
        lines.extend(defs_and_styles)
    lines.extend([
        '  <rect x="0.5" y="0.5" width="859" height="171" rx="10" fill="#0d1117" stroke="#30363d"/>',
        '  <path d="M1 30H859" stroke="#30363d"/>',
        '  <circle cx="16" cy="15" r="4.5" fill="#ff5f56"/>',
        '  <circle cx="30" cy="15" r="4.5" fill="#ffbd2e"/>',
        '  <circle cx="44" cy="15" r="4.5" fill="#27c93f"/>',
        '  <text x="430" y="19" fill="#c9d1d9" font-size="11.5" text-anchor="middle">ayushcmd@github: ~/status --live</text>',
        '  <text x="844" y="19" fill="#8b949e" font-size="10.5" text-anchor="end">IST · Asia/Kolkata</text>',
        '  <rect x="14" y="42" width="832" height="116" rx="8" fill="#10161e" stroke="#30363d"/>',
        f'  <rect x="14" y="42" width="5" height="116" rx="2.5" fill="{escape(accent, quote=True)}"/>',
    ])
    return lines


def _render_generic_icon_badge(emoji: str, accent: str, avail_color: str) -> list[str]:
    """Render the standard left emoji badge used by non-offline statuses."""
    return [
        f'  <rect x="32" y="58" width="64" height="64" rx="12" fill="{escape(accent, quote=True)}" '
        f'fill-opacity="0.14" stroke="{escape(accent, quote=True)}" stroke-opacity="0.55"/>',
        f'  <text x="64" y="100" font-size="30" text-anchor="middle">{escape(emoji)}</text>',
        '  <circle cx="88" cy="114" r="6" fill="#10161e"/>',
        f'  <circle cx="88" cy="114" r="4" fill="{escape(avail_color, quote=True)}"/>',
    ]


def _render_status_info_block(
    *,
    title: str,
    message: str,
    project: str | None,
    tags: list[str],
    accent: str,
    avail_color: str,
    avail_label: str,
    text_x: int,
    title_size: int = 22,
    title_y: int = 76,
    msg_y: int = 99,
    tag_y: int = 109,
    max_msg_len: int = 82,
    max_proj_len: int = 76,
) -> list[str]:
    """Render shared status title, right-aligned availability pill, message, and tags/project."""
    pill_width = max(96, int(len(avail_label) * 7.4) + 42)
    pill_x = 828 - pill_width
    pill_dot_x = pill_x + 16
    pill_text_x = pill_x + 29

    lines = [
        f'  <text x="{text_x}" y="{title_y}" fill="#e6edf3" font-size="{title_size}" font-weight="700">{escape(title)}</text>',
        f'  <rect x="{pill_x}" y="56" width="{pill_width}" height="24" rx="12" '
        f'fill="{escape(avail_color, quote=True)}" fill-opacity="0.16" '
        f'stroke="{escape(avail_color, quote=True)}" stroke-opacity="0.6"/>',
        f'  <circle cx="{pill_dot_x}" cy="68" r="4" fill="{escape(avail_color, quote=True)}"/>',
        f'  <text x="{pill_text_x}" y="72" fill="{escape(avail_color, quote=True)}" '
        f'font-size="11" font-weight="700">{escape(avail_label)}</text>',
        f'  <text x="{text_x}" y="{msg_y}" fill="#c9d1d9" font-size="13">{escape(_truncate(message, max_msg_len))}</text>',
    ]

    if project:
        proj_str = _truncate(project, max_proj_len)
        lines.append(
            f'  <text x="{text_x}" y="{tag_y + 13}" fill="#8b949e" font-size="11.5">'
            f'<tspan fill="{escape(accent, quote=True)}" font-weight="600">PROJECT</tspan>'
            f'  {escape(proj_str)}</text>'
        )
    else:
        tag_x = text_x
        for tag in tags:
            t_label = _truncate(tag, 18)
            t_width = max(62, len(t_label) * 7 + 22)
            lines.append(
                f'  <rect x="{tag_x}" y="{tag_y}" width="{t_width}" height="20" rx="10" '
                f'fill="#161b22" stroke="#30363d"/>'
                f'<text x="{tag_x + t_width / 2:.1f}" y="{tag_y + 13.5:.1f}" fill="#c9d1d9" '
                f'font-size="10.5" text-anchor="middle">{escape(t_label)}</text>'
            )
            tag_x += t_width + 8

    return lines


def _render_footer(expiry_text: str, mode_badge: str) -> list[str]:
    """Render shared card divider and bottom expiry/status metadata footer."""
    return [
        '  <path d="M32 134H828" stroke="#21262d"/>',
        f'  <text x="32" y="149" fill="#8b949e" font-size="11">{escape(expiry_text)}</text>',
        f'  <text x="828" y="149" fill="#6e7681" font-size="10.5" text-anchor="end">{escape(mode_badge)}</text>',
    ]


def _render_offline_dino_defs() -> list[str]:
    """Definitions and reduced-motion CSS for the Offline Dino Runner scene."""
    return [
        '  <defs>',
        '    <clipPath id="offline-dino-clip">',
        '      <rect x="30" y="46" width="270" height="86"/>',
        '    </clipPath>',
        '    <mask id="offline-moon-mask">',
        '      <rect x="190" y="50" width="32" height="32" fill="#ffffff"/>',
        '      <circle cx="201" cy="61" r="9.2" fill="#000000"/>',
        '    </mask>',
        '  </defs>',
        '  <style>',
        '    @media (prefers-reduced-motion: reduce) {',
        '      .dino-motion { transform: none !important; }',
        '      .dino-leg-1 { opacity: 1 !important; }',
        '      .dino-leg-2, .dino-leg-3 { display: none !important; }',
        '      .dino-star { opacity: 0.75 !important; }',
        '    }',
        '  </style>',
    ]


def _build_ground_tile_elements(offset_x: int) -> list[str]:
    """Build one seamless 270px tile of monochrome pixel pebbles and terrain bumps."""
    rock_path = _pixel_grid_to_path(_ROCK_MOUND_GRID, offset_x + 42, 117, cell=2)
    pebbles = [
        (36, 127, 4, 2),
        (58, 129, 2, 2),
        (74, 126, 6, 2),
        (98, 128, 4, 2),
        (118, 126, 2, 2),
        (134, 129, 6, 2),
        (158, 127, 4, 2),
        (178, 126, 2, 2),
        (194, 129, 4, 2),
        (214, 127, 6, 2),
        (238, 128, 2, 2),
        (256, 126, 4, 2),
        (276, 129, 6, 2),
        (292, 127, 4, 2),
    ]
    pebble_cmds = "".join(f"M{offset_x + x} {y}h{w}v{h}h-{w}z" for x, y, w, h in pebbles)
    return [
        f'        <path d="{rock_path}" fill="#c9d1d9"/>',
        f'        <path d="{pebble_cmds}" fill="#6e7681"/>',
    ]


def _render_offline_dino_scene() -> list[str]:
    """Render the animated monochrome pixel-art Dino Runner scene on the left side."""
    # Pre-baked compact pixel-art paths
    cloud_large_path = _pixel_grid_to_path(_CLOUD_LARGE_GRID, 0, 0, cell=2)
    cloud_small_path = _pixel_grid_to_path(_CLOUD_SMALL_GRID, 0, 0, cell=2)
    hills_tile_1 = _pixel_grid_to_path(_HILLS_GRID, 162, 109, cell=2)
    hills_tile_2 = _pixel_grid_to_path(_HILLS_GRID, 162 + 270, 109, cell=2)
    cactus_tall_path = _pixel_grid_to_path(_CACTUS_TALL_GRID, 0, 0, cell=2)
    cactus_small_path = _pixel_grid_to_path(_CACTUS_SMALL_GRID, 0, 0, cell=2)

    dino_x, dino_y = 78, 81
    dino_body_path = _pixel_grid_to_path(_DINO_BODY_GRID, dino_x, dino_y, cell=2)
    dino_legs_paths = [
        _pixel_grid_to_path(frame, dino_x, dino_y + 17 * 2, cell=2)
        for frame in _DINO_LEG_FRAMES
    ]

    # Twinkling stars (x, y, size, duration, begin, is_plus)
    stars = [
        (46, 82, 2, "3.6s", "0s", False),
        (132, 62, 2, "4.2s", "-1.2s", True),
        (160, 77, 2, "3.4s", "-2.1s", False),
        (220, 86, 2, "4.6s", "-0.8s", False),
        (274, 59, 2, "3.9s", "-2.6s", True),
        (94, 57, 2, "4.8s", "-1.7s", False),
    ]
    star_lines: list[str] = []
    for sx, sy, sz, dur, begin, is_plus in stars:
        if is_plus:
            star_d = f"M{sx} {sy - 2}h2v6h-2zM{sx - 2} {sy}h6v2h-6z"
            star_lines.append(
                f'      <path class="dino-star" d="{star_d}" fill="#c9d1d9" opacity="0.7">'
                f'<animate attributeName="opacity" values="0.22;0.95;0.22" dur="{dur}" begin="{begin}" repeatCount="indefinite"/>'
                f'</path>'
            )
        else:
            star_lines.append(
                f'      <rect class="dino-star" x="{sx}" y="{sy}" width="{sz}" height="{sz}" fill="#8b949e" opacity="0.65">'
                f'<animate attributeName="opacity" values="0.2;0.9;0.2" dur="{dur}" begin="{begin}" repeatCount="indefinite"/>'
                f'</rect>'
            )

    lines = [
        '  <g id="offline-dino-scene" clip-path="url(#offline-dino-clip)" shape-rendering="crispEdges">',
        # Stationary crescent moon
        '    <g id="offline-moon" shape-rendering="geometricPrecision">',
        '      <circle cx="206" cy="65" r="10.5" fill="#f0f6fc" mask="url(#offline-moon-mask)"/>',
        '    </g>',
        # Subtle twinkling stars
        '    <g id="offline-stars">',
        *star_lines,
        '    </g>',
        # Slowly drifting independent pixel clouds
        '    <g id="offline-clouds" fill="#3a434e">',
        f'      <g class="dino-motion" transform="translate(62 66)">'
        f'<path d="{cloud_large_path}"/>'
        f'<animateTransform attributeName="transform" type="translate" from="304 66" to="-36 66" dur="28s" begin="-19.8s" repeatCount="indefinite"/>'
        f'</g>',
        f'      <g class="dino-motion" transform="translate(236 72)">'
        f'<path d="{cloud_small_path}"/>'
        f'<animateTransform attributeName="transform" type="translate" from="304 72" to="-32 72" dur="34s" begin="-6.8s" repeatCount="indefinite"/>'
        f'</g>',
        '    </g>',
        # Distant slow-parallax mountain silhouettes
        '    <g id="offline-hills" class="dino-motion" fill="#2c343f">',
        f'      <path d="{hills_tile_1}"/>',
        f'      <path d="{hills_tile_2}"/>',
        '      <animateTransform attributeName="transform" type="translate" from="0 0" to="-270 0" dur="22s" repeatCount="indefinite"/>',
        '    </g>',
        # Horizontal terrain baseline + seamless scrolling ground details (67.5 px/sec)
        '    <rect x="30" y="123" width="270" height="2" fill="#8b949e"/>',
        '    <g id="offline-ground" class="dino-motion">',
        *_build_ground_tile_elements(0),
        *_build_ground_tile_elements(270),
        '      <animateTransform attributeName="transform" type="translate" from="0 0" to="-270 0" dur="4s" repeatCount="indefinite"/>',
        '    </g>',
        # Scrolling pixel cactus obstacles (speed-locked to ground at 67.5 px/sec, no collision effects)
        '    <g id="offline-cacti" fill="#c9d1d9">',
        f'      <g class="dino-motion" transform="translate(254 94)">'
        f'<path d="{cactus_tall_path}"/>'
        f'<animateTransform attributeName="transform" type="translate" from="308 94" to="-164.5 94" dur="7s" begin="-0.8s" repeatCount="indefinite"/>'
        f'</g>',
        f'      <g class="dino-motion" transform="translate(-10 102)">'
        f'<path d="{cactus_small_path}"/>'
        f'<animateTransform attributeName="transform" type="translate" from="308 102" to="-164.5 102" dur="7s" begin="-4.3s" repeatCount="indefinite"/>'
        f'</g>',
        '    </g>',
        # Animated pixel T-Rex runner (3-frame leg cycle + subtle natural stride bounce)
        '    <g id="offline-dino-runner" class="dino-motion">',
        '      <animateTransform attributeName="transform" type="translate" values="0 0;0 -1.5;0 0;0 -1;0 0" dur="0.36s" repeatCount="indefinite"/>',
        f'      <path d="{dino_body_path}" fill="#d8dee4" stroke="#10161e" stroke-width="1.5" paint-order="stroke"/>',
        f'      <path class="dino-leg-1" d="{dino_legs_paths[0]}" fill="#d8dee4" opacity="1">'
        f'<animate attributeName="opacity" values="1;0;0" keyTimes="0;0.333;0.667" calcMode="discrete" dur="0.36s" repeatCount="indefinite"/>'
        f'</path>',
        f'      <path class="dino-leg-2" d="{dino_legs_paths[1]}" fill="#d8dee4" opacity="0">'
        f'<animate attributeName="opacity" values="0;1;0" keyTimes="0;0.333;0.667" calcMode="discrete" dur="0.36s" repeatCount="indefinite"/>'
        f'</path>',
        f'      <path class="dino-leg-3" d="{dino_legs_paths[2]}" fill="#d8dee4" opacity="0">'
        f'<animate attributeName="opacity" values="0;0;1" keyTimes="0;0.333;0.667" calcMode="discrete" dur="0.36s" repeatCount="indefinite"/>'
        f'</path>',
        '    </g>',
        '  </g>',
    ]
    return lines


def render_status_svg_markup(data: dict[str, Any]) -> str:
    """Render deterministic, valid XML SVG markup for a validated resolved status."""
    errors = validate_renderable_status(data)
    if errors:
        raise ValueError("Cannot render invalid status data:\n" + "\n".join(f"- {e}" for e in errors))

    status_id: str = data["status"]
    emoji: str = data["emoji"]
    title: str = _truncate(data["title"], 38)
    availability: str = data["availability"]
    message: str = data["message"]
    project: str | None = data.get("project")
    tags: list[str] = [str(t) for t in data.get("tags", [])[:2]]
    accent: str = data.get("color") or AVAILABILITY_COLORS.get(availability, "#8b949e")
    avail_color: str = AVAILABILITY_COLORS.get(availability, accent)
    avail_label: str = AVAILABILITY_LABELS.get(availability, availability.upper())
    expiry_text: str = format_expiry_text(data)
    source: str = data["resolution_source"]
    mode_badge: str = _format_mode_badge(status_id, source)

    aria_label = (
        f"Ayush Live Status: {title} ({avail_label}) — {_truncate(message, 82)} ({expiry_text})"
    )

    if status_id == "offline":
        lines = [
            *_render_terminal_frame_open(
                aria_label,
                accent,
                defs_and_styles=_render_offline_dino_defs(),
            ),
            *_render_offline_dino_scene(),
            *_render_status_info_block(
                title=title,
                message=message,
                project=project,
                tags=tags,
                accent=accent,
                avail_color=avail_color,
                avail_label=avail_label,
                text_x=318,
                title_size=24,
                title_y=77,
                msg_y=100,
                tag_y=109,
                max_msg_len=62,
                max_proj_len=56,
            ),
            *_render_footer(expiry_text, mode_badge),
            '</svg>',
        ]
    else:
        lines = [
            *_render_terminal_frame_open(aria_label, accent),
            *_render_generic_icon_badge(emoji, accent, avail_color),
            *_render_status_info_block(
                title=title,
                message=message,
                project=project,
                tags=tags,
                accent=accent,
                avail_color=avail_color,
                avail_label=avail_label,
                text_x=114,
            ),
            *_render_footer(expiry_text, mode_badge),
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
