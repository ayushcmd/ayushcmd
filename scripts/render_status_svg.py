#!/usr/bin/env python3
"""Render the Ayush Live Status SVG card from `data/status_resolved.json`.

Reads the machine-readable resolved status output (`data/status_resolved.json`),
validates that it represents one of the three supported statuses without any
placeholders or schema errors, and writes `img/status/current.svg` only when
the rendered SVG content changes.

Uses a modular layout:
- Shared terminal frame, status info block, and availability pill rendering
- Dedicated animated Prehistoric Jurassic World scene for `available`
- Dedicated animated monochrome pixel-art Dino Runner scene for `offline`
- Dedicated animated Medieval Dragon Night scene for `sleeping`

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
    SUPPORTED_STATUS_IDS,
    is_placeholder_value,
    load_json_file,
    validate_resolved_status,
)

DEFAULT_IN_PATH = REPO_ROOT / "data" / "status_resolved.json"
DEFAULT_OUT_PATH = REPO_ROOT / "img" / "status" / "current.svg"

AVAILABILITY_COLORS = {
    "online": "#3fb950",
    "offline": "#8b949e",
}

AVAILABILITY_LABELS = {
    "online": "ONLINE",
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


def _palette_grid_to_paths(
    rows: tuple[str, ...],
    palette: dict[str, str],
    x0: int,
    y0: int,
    cell: int = 2,
    *,
    indent: str = "      ",
) -> list[str]:
    """Compile a multi-color ASCII pixel sprite into one compact `<path>` per color."""
    by_color: dict[str, list[str]] = {}
    for row_idx, row in enumerate(rows):
        col = 0
        width = len(row)
        y = y0 + row_idx * cell
        while col < width:
            ch = row[col]
            if ch not in palette:
                col += 1
                continue
            start = col
            while col < width and row[col] == ch:
                col += 1
            run_w = (col - start) * cell
            x = x0 + start * cell
            by_color.setdefault(ch, []).append(f"M{x} {y}h{run_w}v{cell}h-{run_w}z")

    out: list[str] = []
    for ch in palette:
        cmds = by_color.get(ch)
        if cmds:
            out.append(f'{indent}<path d="{"".join(cmds)}" fill="{palette[ch]}"/>')
    return out


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
    status_id = data.get("status")
    source = data.get("resolution_source")
    until = data.get("until")
    computed = data.get("computed") or {}
    until_display = computed.get("until_display") or (f"{until} IST" if until else None)

    if source == "night_override" or (status_id == "sleeping" and until == "06:00"):
        return "Ayush available at 6 AM IST."

    if source == "manual" and not data.get("expired") and until_display:
        if status_id == "available":
            return f"Available till {until_display}"
        return f"Expires at {until_display}"

    return "Expired · Offline until next update"


def _format_mode_badge(status_id: str, source: str) -> str:
    if source == "night_override":
        return f"STATUS · {status_id.upper()}"
    if source == "offline_fallback":
        return "FALLBACK · OFFLINE"
    return f"STATUS · {status_id.upper()}"


def _render_terminal_frame_open(
    aria_label: str,
    accent: str,
    *,
    defs_and_styles: list[str] | None = None,
    show_left_bar: bool = True,
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
    ])
    if show_left_bar:
        lines.append(
            f'  <rect x="14" y="42" width="5" height="116" rx="2.5" fill="{escape(accent, quote=True)}"/>'
        )
    return lines


def _render_status_info_block(
    *,
    title: str,
    message: str,
    tags: list[str],
    avail_color: str,
    avail_label: str,
    text_x: int,
    title_size: int = 22,
    title_y: int = 76,
    title_color: str = "#e6edf3",
    pill_y: int = 56,
    msg_y: int = 99,
    msg_size: float = 13,
    tag_y: int = 109,
    max_msg_len: int = 82,
    pulse_dot: bool = False,
    tag_fill: str = "#161b22",
    tag_stroke: str = "#30363d",
    tag_text_color: str = "#c9d1d9",
) -> list[str]:
    """Render shared status title, right-aligned availability pill, message, and tags."""
    pill_width = max(96, int(len(avail_label) * 7.4) + 42)
    pill_x = 828 - pill_width
    pill_dot_x = pill_x + 16
    pill_dot_y = pill_y + 12
    pill_text_x = pill_x + 29
    pill_text_y = pill_y + 16

    lines = [
        f'  <text x="{text_x}" y="{title_y}" fill="{escape(title_color, quote=True)}" font-size="{title_size}" font-weight="700">{escape(title)}</text>',
        f'  <rect x="{pill_x}" y="{pill_y}" width="{pill_width}" height="24" rx="12" '
        f'fill="{escape(avail_color, quote=True)}" fill-opacity="0.16" '
        f'stroke="{escape(avail_color, quote=True)}" stroke-opacity="0.6"/>',
    ]

    if pulse_dot:
        lines.extend([
            f'  <circle class="jurassic-pulse" cx="{pill_dot_x}" cy="{pill_dot_y}" r="4" '
            f'fill="{escape(avail_color, quote=True)}" opacity="0.55">'
            f'<animate attributeName="r" values="4;7.5;4" dur="2s" repeatCount="indefinite"/>'
            f'<animate attributeName="opacity" values="0.6;0.08;0.6" dur="2s" repeatCount="indefinite"/>'
            f'</circle>',
            f'  <circle cx="{pill_dot_x}" cy="{pill_dot_y}" r="4" fill="#4ade80">'
            f'<animate attributeName="opacity" values="0.85;1;0.85" dur="2s" repeatCount="indefinite"/>'
            f'</circle>',
        ])
    else:
        lines.append(
            f'  <circle cx="{pill_dot_x}" cy="{pill_dot_y}" r="4" fill="{escape(avail_color, quote=True)}"/>'
        )

    lines.extend([
        f'  <text x="{pill_text_x}" y="{pill_text_y}" fill="{escape(avail_color, quote=True)}" '
        f'font-size="11" font-weight="700">{escape(avail_label)}</text>',
        f'  <text x="{text_x}" y="{msg_y}" fill="#c9d1d9" font-size="{msg_size}">{escape(_truncate(message, max_msg_len))}</text>',
    ])

    tag_x = text_x
    for tag in tags:
        t_label = _truncate(tag, 18)
        t_width = max(62, len(t_label) * 7 + 22)
        lines.append(
            f'  <rect x="{tag_x}" y="{tag_y}" width="{t_width}" height="20" rx="10" '
            f'fill="{escape(tag_fill, quote=True)}" stroke="{escape(tag_stroke, quote=True)}"/>'
            f'<text x="{tag_x + t_width / 2:.1f}" y="{tag_y + 13.5:.1f}" '
            f'fill="{escape(tag_text_color, quote=True)}" '
            f'font-size="10.5" text-anchor="middle">{escape(t_label)}</text>'
        )
        tag_x += t_width + 8

    return lines


def _render_footer(expiry_text: str, mode_badge: str) -> list[str]:
    """Render card divider and bottom expiry/status metadata footer for offline."""
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


# ---------------------------------------------------------------------------
# Available Jurassic Valley pixel-art sprites & scene renderer (16-bit world)
# ---------------------------------------------------------------------------

_JURASSIC_CLOUD_PALETTE = {
    "W": "#ffffff",
    "M": "#dff2ff",
    "S": "#a6d8fb",
}
_JURASSIC_CLOUD_LARGE_GRID = (
    ".........WWWWW............",
    ".......WWWWWWWWW..........",
    ".....WWWWWWWWWWWWW........",
    "..WWWWWWWWWWWWWWWWWWW.....",
    ".WWWWWWWWWWWWWWWWWWWWWW...",
    "WWWWWWWWWWWWWWWWWWWWWWMW..",
    "MMMMMMMMMMMMMMMMMMMMMMMMM.",
    "SSSSSSSSSSSSSSSSSSSSSSSSSS",
)
_JURASSIC_CLOUD_SMALL_GRID = (
    ".....WWWWW......",
    "...WWWWWWWWW....",
    ".WWWWWWWWWWWWWW.",
    "MMMMMMMMMMMMMMMW",
    "SSSSSSSSSSSSSSSS",
)

_SAUROPOD_PALETTE = {
    "K": "#223322",
    "D": "#5e7556",
    "M": "#8ea87e",
    "L": "#bdd4ab",
}
_SAUROPOD_BODY_GRID = (
    ".....................KKKK..",
    "....................KLLMK..",
    "....................KMDKK..",
    "...................KLMDK...",
    "..................KLMDK....",
    ".................KLMDK.....",
    "................KLMDK......",
    "...............KLMDDK......",
    "...........KKKKLMDDK.......",
    ".........KKLLLLMMMDDK......",
    ".......KKLLMMMMMMMMDK......",
    "....KKKMMMMMMMMMMMMDK......",
    ".KKKMMMMMMDDDDDDDDDK.......",
    "KDDDDDDDDK.................",
)
_SAUROPOD_LEGS_A = (
    ".........KMDK...KMDK.......",
    ".........KMDK...KMDK.......",
    ".........KKKK...KKKK.......",
)
_SAUROPOD_LEGS_B = (
    "........KMDK.....KMDK......",
    "........KMDK.....KMDK......",
    "........KKKK.....KKKK......",
)

_STEGOSAURUS_PALETTE = {
    "K": "#1c2e14",
    "P": "#b83a22",
    "R": "#e5633b",
    "D": "#4d7d2c",
    "M": "#7db542",
    "L": "#b4e065",
    "W": "#fff3d6",
}
_STEGOSAURUS_BODY_GRID = (
    "..........KK.KK..........",
    "........KKRK.KRKK........",
    "......KKRPKRPKRPKK.......",
    "....KKRPKKKKKKKRPKK......",
    "...KPRKKLLLLLLLKKPK..W...",
    ".KKKKKLLMMMMMMMMLKKKK.W..",
    "KMMMLMMMMMMMMMMMMMMMDKK..",
    "KMDKMMMMMMMMMMMMMMMMDDK..",
    ".KKKDDDDDDDDDDDDDDDDKK...",
)
_STEGOSAURUS_LEGS_A = (
    ".....KMDK.....KMDK.......",
    ".....KKKK.....KKKK.......",
)
_STEGOSAURUS_LEGS_B = (
    "......KMDK...KMDK........",
    "......KKKK...KKKK........",
)

_TRICERATOPS_PALETTE = {
    "K": "#281409",
    "D": "#7a3918",
    "O": "#b85520",
    "L": "#e4853b",
    "B": "#e5b889",
    "W": "#fff6e5",
}
_TRICERATOPS_BODY_GRID = (
    ".....KKKKKKKKKKKKK....",
    "...KKLLLLLLLLLLOOOK...",
    "..KLLOOLLLLOOLLOOOOK..",
    ".KLLOOOOOOOOOOOOLOOOK.",
    "KLOOOODDOOOODDOOOOOOK.",
    "KOOOOODDOOOODDOOOOBBK.",
    ".KDDDOOOOOOOOOOOOBBBK.",
    "..KDDDDDBBBBBBBBBBKK..",
)
_TRICERATOPS_LEGS_GRID = (
    "..KODK..........KODK..",
    "..KODK..KODK....KODK..",
    "..KODK..KODK....KODK..",
    "..KKKK..KKKK....KKKK..",
)
_TRICERATOPS_HEAD_GRID = (
    "..KWKK................",
    ".KWLLWK..........W....",
    "KWLOLWWK.......WW.....",
    "KWOOLWWKK....WW.......",
    "KWOOLWLLKKKKW.........",
    "KWOOLWLLOOOOOK..W.....",
    ".KWOOLLLLOKOOOKW......",
    ".KKOOLLOOOOOOOOOK.....",
    "...KKODBBBBBBBBK......",
    ".....KKKKKKKKKK.......",
)
_TRICERATOPS_TAIL_GRID = (
    ".........KKK",
    ".......KKLLK",
    "....KKKLOOOK",
    ".KKKLOOOODDK",
    "KDDDDDDDDKK.",
)

_PTEROSAUR_PALETTE = {
    "K": "#361c0a",
    "D": "#96531c",
    "M": "#c97a2d",
    "L": "#f0ac54",
}
_PTEROSAUR_UP_GRID = (
    "KKK.............",
    ".KLMK...........",
    "..KLMK....KK....",
    "...KLMMKKKLKK...",
    "....KMMMMMMLLKKK",
    ".....KKDDKKMKKKK",
    ".......KK.......",
)
_PTEROSAUR_DOWN_GRID = (
    "..........KK....",
    "......KKKKLKK...",
    "...KKKMMMMMLLKKK",
    "..KLMKKDDKKMKKKK",
    ".KLMK...KK......",
    "KKK.............",
)


def _render_available_jurassic_defs() -> list[str]:
    """Gradients, clipPaths, and reduced-motion CSS for the Available Jurassic Valley."""
    return [
        '  <defs>',
        '    <clipPath id="available-jurassic-clip">',
        '      <rect x="18" y="44" width="406" height="112" rx="6"/>',
        '    </clipPath>',
        '    <clipPath id="jurassic-waterfall-clip">',
        '      <rect x="310" y="131" width="60" height="22"/>',
        '    </clipPath>',
        '    <linearGradient id="jurassic-sky" x1="0" y1="44" x2="0" y2="104" gradientUnits="userSpaceOnUse">',
        '      <stop offset="0%" stop-color="#1785eb"/>',
        '      <stop offset="56%" stop-color="#48b8ff"/>',
        '      <stop offset="100%" stop-color="#b8edff"/>',
        '    </linearGradient>',
        '    <linearGradient id="jurassic-valley" x1="18" y1="96" x2="424" y2="156" gradientUnits="userSpaceOnUse">',
        '      <stop offset="0%" stop-color="#4ab52c"/>',
        '      <stop offset="45%" stop-color="#8be332"/>',
        '      <stop offset="100%" stop-color="#359c2b"/>',
        '    </linearGradient>',
        '    <linearGradient id="jurassic-water" x1="250" y1="96" x2="350" y2="156" gradientUnits="userSpaceOnUse">',
        '      <stop offset="0%" stop-color="#67d3f5"/>',
        '      <stop offset="55%" stop-color="#2ca6e0"/>',
        '      <stop offset="100%" stop-color="#196fa8"/>',
        '    </linearGradient>',
        '  </defs>',
        '  <style>',
        '    @media (prefers-reduced-motion: reduce) {',
        '      .jurassic-motion { transform: none !important; }',
        '      .jurassic-pulse { display: none !important; }',
        '      .jurassic-frame-b { display: none !important; }',
        '      .jurassic-frame-a { opacity: 1 !important; }',
        '      .jurassic-glow { opacity: 0.85 !important; }',
        '    }',
        '  </style>',
    ]


def _render_available_jurassic_scene() -> list[str]:
    """Render the animated 16-bit Prehistoric Jurassic Valley on the left panel."""
    cloud_l = _palette_grid_to_paths(_JURASSIC_CLOUD_LARGE_GRID, _JURASSIC_CLOUD_PALETTE, 0, 0, cell=2, indent="        ")
    cloud_s = _palette_grid_to_paths(_JURASSIC_CLOUD_SMALL_GRID, _JURASSIC_CLOUD_PALETTE, 0, 0, cell=2, indent="        ")

    sauropod_body = _palette_grid_to_paths(_SAUROPOD_BODY_GRID, _SAUROPOD_PALETTE, 88, 76, cell=2, indent="        ")
    sauropod_legs_a = _palette_grid_to_paths(_SAUROPOD_LEGS_A, _SAUROPOD_PALETTE, 88, 76 + 14 * 2, cell=2, indent="          ")
    sauropod_legs_b = _palette_grid_to_paths(_SAUROPOD_LEGS_B, _SAUROPOD_PALETTE, 88, 76 + 14 * 2, cell=2, indent="          ")

    stego_body = _palette_grid_to_paths(_STEGOSAURUS_BODY_GRID, _STEGOSAURUS_PALETTE, 294, 102, cell=2, indent="        ")
    stego_legs_a = _palette_grid_to_paths(_STEGOSAURUS_LEGS_A, _STEGOSAURUS_PALETTE, 294, 102 + 9 * 2, cell=2, indent="          ")
    stego_legs_b = _palette_grid_to_paths(_STEGOSAURUS_LEGS_B, _STEGOSAURUS_PALETTE, 294, 102 + 9 * 2, cell=2, indent="          ")

    trike_body = _palette_grid_to_paths(_TRICERATOPS_BODY_GRID, _TRICERATOPS_PALETTE, 104, 111, cell=2, indent="        ")
    trike_legs = _palette_grid_to_paths(_TRICERATOPS_LEGS_GRID, _TRICERATOPS_PALETTE, 104, 111 + 8 * 2, cell=2, indent="      ")
    trike_head = _palette_grid_to_paths(_TRICERATOPS_HEAD_GRID, _TRICERATOPS_PALETTE, 140, 109, cell=2, indent="        ")
    trike_tail = _palette_grid_to_paths(_TRICERATOPS_TAIL_GRID, _TRICERATOPS_PALETTE, 84, 120, cell=2, indent="        ")

    ptero_up = _palette_grid_to_paths(_PTEROSAUR_UP_GRID, _PTEROSAUR_PALETTE, 0, 0, cell=2, indent="        ")
    ptero_down = _palette_grid_to_paths(_PTEROSAUR_DOWN_GRID, _PTEROSAUR_PALETTE, 0, 2, cell=2, indent="        ")

    # Rising gentle volcanic smoke puffs (cohesive billowing plume, calm & non-explosive)
    smoke_puffs = [
        ("316 61;328 54;342 47;358 41", "7.8s", "0s", "#e2ebf5", 9, 5),
        ("318 60;332 53;348 46;366 40", "8.6s", "-1.9s", "#cbd7e6", 11, 6),
        ("317 61;330 53;346 46;364 39", "9.4s", "-4.1s", "#eef4fa", 10, 5),
        ("315 62;326 55;340 48;356 42", "10.2s", "-6.4s", "#bccad9", 8, 5),
    ]
    smoke_lines: list[str] = []
    for vals, dur, begin, col, rw, rh in smoke_puffs:
        sx, sy = vals.split(";")[0].split()
        smoke_lines.append(
            f'      <g class="jurassic-motion" transform="translate({sx} {sy})" opacity="0.72">'
            f'<rect x="-{rw}" y="-{rh}" width="{rw * 2}" height="{rh * 2}" rx="3" fill="{col}"/>'
            f'<rect x="-{rw - 2}" y="-{rh + 2}" width="{(rw - 2) * 2}" height="{rh}" rx="2" fill="#f8fbff" opacity="0.6"/>'
            f'<animateTransform attributeName="transform" type="translate" values="{vals}" dur="{dur}" begin="{begin}" repeatCount="indefinite"/>'
            f'<animate attributeName="opacity" values="0;0.78;0.65;0" keyTimes="0;0.18;0.7;1" dur="{dur}" begin="{begin}" repeatCount="indefinite"/>'
            f'</g>'
        )

    lines = [
        '  <g id="available-jurassic-scene" clip-path="url(#available-jurassic-clip)">',
        # 1. Prehistoric sky
        '    <rect x="18" y="44" width="406" height="112" fill="url(#jurassic-sky)"/>',
        # 2. Independently drifting pixel clouds
        '    <g id="jurassic-clouds" shape-rendering="crispEdges">',
        '      <g class="jurassic-motion" transform="translate(166 56)">',
        *cloud_l,
        '        <animateTransform attributeName="transform" type="translate" from="430 56" to="-62 56" dur="28s" begin="-15.0s" repeatCount="indefinite"/>',
        '      </g>',
        '      <g class="jurassic-motion" transform="translate(364 58)">',
        *cloud_s,
        '        <animateTransform attributeName="transform" type="translate" from="430 58" to="-42 58" dur="24s" begin="-3.4s" repeatCount="indefinite"/>',
        '      </g>',
        '      <g class="jurassic-motion" transform="translate(86 70)">',
        *cloud_s,
        '        <animateTransform attributeName="transform" type="translate" from="430 70" to="-42 70" dur="32s" begin="-23.2s" repeatCount="indefinite"/>',
        '      </g>',
        '    </g>',
        # 3. Volcano smoke, towering cone, and subtle lava glow
        '    <g id="jurassic-volcano" shape-rendering="crispEdges">',
        '      <g id="jurassic-smoke">',
        *smoke_lines,
        '      </g>',
        # Distant left-to-center alpine mountain silhouette
        '      <polygon points="18,98 54,78 88,88 122,74 162,86 206,72 246,84 246,104 18,104" fill="#4682b4"/>',
        '      <polygon points="54,78 68,85 58,88 46,84" fill="#6aa8d6"/>',
        '      <polygon points="122,74 138,82 126,86 114,81" fill="#6aa8d6"/>',
        '      <polygon points="206,72 222,80 210,84 198,79" fill="#6aa8d6"/>',
        # Main volcano cone (peak at x=316, y=62)
        '      <polygon points="222,98 274,76 306,62 326,62 358,76 410,98" fill="#486584"/>',
        '      <polygon points="256,98 292,76 312,62 324,62 346,78 386,98" fill="#5e7f9e"/>',
        '      <polygon points="316,62 326,62 356,78 396,98 352,98 330,78" fill="#3a526e"/>',
        # Sunlit ridge strokes on volcano slope
        '      <path d="M306 66l-18 14-24 14M314 66l-10 16-14 14M322 66l12 14 20 14" stroke="#789ab8" stroke-width="1.5" fill="none" opacity="0.65"/>',
        # Subtle glowing lava caldera & slow-pulsing veins (calm, non-explosive)
        '      <g id="jurassic-lava" class="jurassic-glow">',
        '        <path d="M306 62h20v3h-4v2h-12v-2h-4z" fill="#ff7324"/>',
        '        <path d="M310 62h12v2h-12z" fill="#ffd859"/>',
        '        <path d="M312 65h2v6h-2v6h-2v-6h2zM320 65h2v4h2v5h-2v-5h-2zM307 65h2v4h-2z" fill="#ff8c2e"/>',
        '        <animate attributeName="opacity" values="0.68;1;0.68" dur="4.2s" repeatCount="indefinite"/>',
        '      </g>',
        # Misty clouds hugging lower volcanic foothills
        '      <path d="M232 94h34v4h-34zM284 92h48v4h-48zM344 90h38v4h-38z" fill="#c9ecff" opacity="0.72"/>',
        '    </g>',
        # 4. Midground prehistoric green hills, rocky right cliff, and valley forest
        '    <g id="jurassic-landscape" shape-rendering="crispEdges">',
        '      <path d="M18 102 Q92 84 176 96 Q256 84 344 94 L424 90 L424 112 L18 112 Z" fill="#217c3e"/>',
        '      <path d="M18 106 Q116 92 214 100 Q314 90 424 98 L424 120 L18 120 Z" fill="#2f9a43"/>',
        # Right rocky sandstone cliff / mesa topped with prehistoric foliage
        '      <polygon points="366,106 374,90 388,88 394,78 424,78 424,108" fill="#6b5e50"/>',
        '      <polygon points="372,106 378,92 390,90 396,80 416,80 412,106" fill="#91806d"/>',
        '      <path d="M380 92h4v12h-4zM396 82h4v20h-4zM412 82h4v18h-4z" fill="#4a4036"/>',
        '      <path d="M388 78h36v4h-36zM370 88h24v4h-24z" fill="#329c3a"/>',
        '      <path d="M394 76h28v3h-28zM374 86h16v3h-16z" fill="#7be034"/>',
        # Bright sunlit rolling Jurassic valley floor
        '      <path d="M18 106 Q142 102 260 108 Q344 104 424 108 L424 156 L18 156 Z" fill="url(#jurassic-valley)"/>',
        # Distant valley trees & prehistoric bushes (layered rounded pixel canopies)
        '      <g id="jurassic-mid-trees">',
        '        <path d="M168 104h2v6h-2zM206 105h2v6h-2zM242 102h2v7h-2zM362 106h3v8h-3zM400 114h3v10h-3z" fill="#593419"/>',
        '        <path d="M162 104h14v-4h-2v-3h-2v-2h-6v2h-2v3h-2zM200 105h14v-4h-2v-3h-2v-2h-6v2h-2v3h-2zM236 102h14v-4h-2v-3h-2v-2h-6v2h-2v3h-2zM353 106h20v-6h-3v-3h-3v-2h-8v2h-3v3h-3zM389 114h26v-7h-3v-4h-4v-2h-12v2h-4v4h-3z" fill="#186832"/>',
        '        <path d="M164 101h10v-3h-2v-3h-6v3h-2zM202 102h10v-3h-2v-3h-6v3h-2zM238 99h10v-3h-2v-3h-6v3h-2zM356 102h14v-4h-3v-3h-8v3h-3zM393 109h18v-5h-3v-3h-12v3h-3z" fill="#5fd132"/>',
        '      </g>',
        '    </g>',
        # 5. Winding prehistoric river & animated dual waterfall
        '    <g id="jurassic-river" shape-rendering="crispEdges">',
        # River banks (soft sandy/earthy edge)
        '      <path d="M262 100 Q284 104 272 109 Q244 114 268 119 Q296 123 262 128 Q232 133 292 137 Q324 139 348 134 L366 134 L366 156 L306 156 L306 144 Q246 139 246 132 Q246 125 276 121 Q252 116 252 111 Q252 106 266 103 Z" fill="#2c6e2f"/>',
        # Main turquoise-blue S-curved river body
        '      <path d="M264 101 Q282 104 270 109 Q248 114 270 119 Q294 123 264 128 Q242 132 302 136 L362 135 L362 156 L310 156 L310 143 Q254 138 254 132 Q254 126 280 121 Q256 116 256 111 Q256 106 268 103 Z" fill="url(#jurassic-water)"/>',
        # Animated flowing river ripples
        '      <g class="jurassic-motion" fill="#d8f6ff" opacity="0.75">',
        '        <path d="M264 108h8v1.5h-8zM266 118h10v1.5h-10zM268 128h12v1.5h-12zM292 133h14v1.5h-14z">',
        '          <animateTransform attributeName="transform" type="translate" values="0 0;6 2;0 0" dur="3.4s" repeatCount="indefinite"/>',
        '          <animate attributeName="opacity" values="0.35;0.9;0.35" dur="3.4s" repeatCount="indefinite"/>',
        '        </path>',
        '        <path d="M258 113h8v1.5h-8zM278 123h10v1.5h-10zM278 132h12v1.5h-12z">',
        '          <animateTransform attributeName="transform" type="translate" values="0 0;-5 2;0 0" dur="4.2s" begin="-1.6s" repeatCount="indefinite"/>',
        '          <animate attributeName="opacity" values="0.3;0.85;0.3" dur="4.2s" begin="-1.6s" repeatCount="indefinite"/>',
        '        </path>',
        '      </g>',
        # Rocky waterfall ledge splitting two sparkling cascades
        '      <path d="M312 132h52v18h-52z" fill="#1b5c8c"/>',
        '      <polygon points="326,149 328,133 334,130 341,132 343,149" fill="#544b42"/>',
        '      <polygon points="329,144 331,134 336,132 340,135 339,144" fill="#827769"/>',
        '      <path d="M330 130h10v3h-10z" fill="#4ec232"/>',
        # Animated falling waterfall water streaks (clipped to drop)
        '      <g id="jurassic-waterfall" clip-path="url(#jurassic-waterfall-clip)">',
        '        <rect x="314" y="131" width="14" height="19" fill="#5cc8f5"/>',
        '        <rect x="342" y="131" width="18" height="19" fill="#5cc8f5"/>',
        '        <g class="jurassic-motion" fill="#eefbff">',
        '          <path d="M316 125h3v7h-3zM322 128h2v8h-2zM318 138h3v7h-3zM324 140h2v7h-2zM344 125h3v8h-3zM351 128h3v7h-3zM356 126h2v7h-2zM346 138h3v8h-3zM353 140h3v7h-3z"/>',
        '          <animateTransform attributeName="transform" type="translate" from="0 -6" to="0 7" dur="1.1s" repeatCount="indefinite"/>',
        '        </g>',
        '      </g>',
        # Pulsing white waterfall splash foam at pool base
        '      <g class="jurassic-motion" fill="#eefbff">',
        '        <path d="M310 149h20v3h-20zM340 149h24v3h-24zM314 147h12v2h-12zM344 147h16v2h-16z"/>',
        '        <animate attributeName="opacity" values="0.65;1;0.65" dur="1.6s" repeatCount="indefinite"/>',
        '      </g>',
        '    </g>',
        # 6. Flying Pterosaur crossing the sky (2-frame wing flap + smooth flight loop)
        '    <g id="jurassic-pterosaur" shape-rendering="crispEdges">',
        '      <g class="jurassic-motion" transform="translate(124 52)">',
        '        <animateTransform attributeName="transform" type="translate" values="-36 58;124 50;268 56;436 48" dur="15s" begin="-4.8s" repeatCount="indefinite"/>',
        '        <g class="jurassic-frame-a" opacity="1">',
        *ptero_up,
        '          <animate attributeName="opacity" values="1;0" keyTimes="0;0.5" calcMode="discrete" dur="0.64s" repeatCount="indefinite"/>',
        '        </g>',
        '        <g class="jurassic-frame-b" opacity="0">',
        *ptero_down,
        '          <animate attributeName="opacity" values="0;1" keyTimes="0;0.5" calcMode="discrete" dur="0.64s" repeatCount="indefinite"/>',
        '        </g>',
        '      </g>',
        '    </g>',
        # 7. Distant Long-Necked Sauropod (slow subtle walk + leg shuffle + gentle neck sway)
        '    <g id="jurassic-sauropod" shape-rendering="crispEdges">',
        '      <g class="jurassic-motion">',
        '        <animateTransform attributeName="transform" type="translate" values="0 0;10 0;0 0" dur="14s" repeatCount="indefinite"/>',
        '        <ellipse cx="116" cy="110" rx="20" ry="2.5" fill="#185427" opacity="0.45"/>',
        '        <g class="jurassic-frame-a" opacity="1">',
        *sauropod_legs_a,
        '          <animate attributeName="opacity" values="1;0" keyTimes="0;0.5" calcMode="discrete" dur="1.8s" repeatCount="indefinite"/>',
        '        </g>',
        '        <g class="jurassic-frame-b" opacity="0">',
        *sauropod_legs_b,
        '          <animate attributeName="opacity" values="0;1" keyTimes="0;0.5" calcMode="discrete" dur="1.8s" repeatCount="indefinite"/>',
        '        </g>',
        '        <g class="jurassic-motion">',
        '          <animateTransform attributeName="transform" type="rotate" values="-1.5 118 96;2 118 96;-1.5 118 96" dur="5.2s" repeatCount="indefinite"/>',
        *sauropod_body,
        '        </g>',
        '      </g>',
        '    </g>',
        # 8. Wandering Stegosaurus on the right sunlit meadow
        '    <g id="jurassic-stegosaurus" shape-rendering="crispEdges">',
        '      <g class="jurassic-motion">',
        '        <animateTransform attributeName="transform" type="translate" values="0 0;-12 0;0 0" dur="13s" repeatCount="indefinite"/>',
        '        <ellipse cx="316" cy="124" rx="18" ry="2.5" fill="#1d6326" opacity="0.5"/>',
        '        <g class="jurassic-frame-a" opacity="1">',
        *stego_legs_a,
        '          <animate attributeName="opacity" values="1;0" keyTimes="0;0.5" calcMode="discrete" dur="1.5s" repeatCount="indefinite"/>',
        '        </g>',
        '        <g class="jurassic-frame-b" opacity="0">',
        *stego_legs_b,
        '          <animate attributeName="opacity" values="0;1" keyTimes="0;0.5" calcMode="discrete" dur="1.5s" repeatCount="indefinite"/>',
        '        </g>',
        '        <g class="jurassic-motion">',
        '          <animateTransform attributeName="transform" type="translate" values="0 0;0 0.7;0 0" dur="3.8s" repeatCount="indefinite"/>',
        *stego_body,
        '        </g>',
        '      </g>',
        '    </g>',
        # 9. Hero Foreground Triceratops (grazing head nod, breathing body, subtle tail sway)
        '    <g id="jurassic-triceratops" shape-rendering="crispEdges">',
        '      <ellipse cx="132" cy="135" rx="34" ry="4" fill="#195222" opacity="0.55"/>',
        '      <g id="triceratops-tail" class="jurassic-motion">',
        '        <animateTransform attributeName="transform" type="rotate" values="-2.5 106 124;3 106 124;-2.5 106 124" dur="4.8s" repeatCount="indefinite"/>',
        *trike_tail,
        '      </g>',
        *trike_legs,
        '      <g id="triceratops-body" class="jurassic-motion">',
        '        <animateTransform attributeName="transform" type="translate" values="0 0;0 0.8;0 0" dur="4.2s" repeatCount="indefinite"/>',
        *trike_body,
        '      </g>',
        '      <g id="triceratops-head" class="jurassic-motion">',
        '        <animateTransform attributeName="transform" type="rotate" values="0 144 120;0 144 120;7 144 120;8 144 120;0 144 120" keyTimes="0;0.38;0.55;0.74;1" dur="6.4s" repeatCount="indefinite"/>',
        *trike_head,
        '      </g>',
        '    </g>',
        # 10. Framing left prehistoric tree, mossy rocks, ferns & purple/white wildflowers
        '    <g id="jurassic-foreground" shape-rendering="crispEdges">',
        # Left framing gnarled tree trunk & overarching leafy bough
        '      <path d="M18 44h16v18h8v-4h16v-4h16v-4h20v-4h-12v4h-18v4h-16v4h-14v10h-6v16h-4v22h-6z" fill="#2b170b"/>',
        '      <path d="M18 54h10v14h10v-4h16v-4h18v-4h-16v4h-16v4h-10v12h-6v24h-6z" fill="#6b3d1e"/>',
        '      <path d="M22 66h6v10h-4v20h-4v-20h2zM38 62h16v-3h-16z" fill="#a86430"/>',
        '      <g class="jurassic-motion">',
        '        <path d="M18 44h132v4h-10v4h-20v-2h-16v4h-22v-4h-14v6h-20v-4h-12v6h-18z" fill="#124a23"/>',
        '        <path d="M18 44h124v4h-12v2h-22v-2h-18v4h-20v-4h-16v4h-18v4h-18z" fill="#2ea834"/>',
        '        <path d="M24 44h30v4h-18v2h-12zM68 44h36v4h-22v2h-14zM114 44h26v4h-16v2h-10z" fill="#9ae833"/>',
        '        <path d="M18 74h44v8h-8v8h-14v6h-22z" fill="#155728"/>',
        '        <path d="M18 74h36v6h-8v6h-14v6h-14z" fill="#46bd33"/>',
        '        <animateTransform attributeName="transform" type="translate" values="0 0;1.2 0.5;0 0;-0.8 0.2;0 0" dur="5.8s" repeatCount="indefinite"/>',
        '      </g>',
        # Left mossy foreground boulders
        '      <polygon points="18,156 18,128 38,124 58,130 70,144 86,140 106,148 112,156" fill="#3a414a"/>',
        '      <polygon points="22,156 24,132 40,128 54,134 62,148 84,144 98,152 100,156" fill="#646f7d"/>',
        '      <polygon points="28,132 42,128 52,134 40,136" fill="#8f9aa8"/>',
        '      <path d="M24 128h16v4h-16zM44 130h12v4h-12zM78 142h18v4h-18z" fill="#4eb832"/>',
        # Center-bottom rock & right waterfall framing rocks
        '      <polygon points="220,154 226,140 244,138 258,144 264,154" fill="#48515c"/>',
        '      <polygon points="226,152 232,142 246,140 254,146 254,152" fill="#77828f"/>',
        '      <path d="M232 140h12v3h-12z" fill="#6cd430"/>',
        '      <polygon points="366,156 372,136 392,130 412,124 424,126 424,156" fill="#3e4650"/>',
        '      <polygon points="374,154 380,138 398,134 414,128 424,130 424,154" fill="#6b7582"/>',
        '      <path d="M386 130h24v4h-24zM406 124h18v5h-18z" fill="#52c432"/>',
        # Purple lupine spikes & crisp white meadow flowers in bottom-left
        '      <path d="M56 154v-14h4v14zM66 154v-12h4v12zM76 154v-10h4v10z" fill="#5b21b6"/>',
        '      <path d="M56 142h4v3h-4zM58 146h4v3h-4zM66 144h4v3h-4zM68 148h4v3h-4zM76 146h4v3h-4z" fill="#a78bfa"/>',
        '      <path d="M58 140h2v2h-2zM68 142h2v2h-2zM78 144h2v2h-2z" fill="#ddd6fe"/>',
        '      <path d="M126 150h4v2h-4zM128 148h2v6h-2zM148 152h4v2h-4zM150 150h2v4h-2zM186 150h4v2h-4zM188 148h2v4h-2zM272 150h4v2h-4zM274 148h2v4h-2z" fill="#ffffff"/>',
        '      <path d="M128 150h2v2h-2zM150 152h2v2h-2zM188 150h2v2h-2zM274 150h2v2h-2z" fill="#facc15"/>',
        # Gently swaying foreground ferns & tall grass blades
        '      <g class="jurassic-motion">',
        '        <path d="M22 156v-16h3v-4h3v20zM32 156v-14h3v-4h3v18zM44 156v-10h3v10z" fill="#1e6e2b"/>',
        '        <path d="M24 156v-14h3v-4h2v18zM34 156v-12h3v-4h2v16z" fill="#6ae034"/>',
        '        <animateTransform attributeName="transform" type="skewX" values="0;2.5;0;-2;0" dur="5.0s" repeatCount="indefinite"/>',
        '      </g>',
        '      <g class="jurassic-motion">',
        '        <path d="M394 156v-14h3v-4h3v18zM406 156v-18h3v-4h3v22zM416 156v-12h3v12z" fill="#1a6328"/>',
        '        <path d="M396 156v-12h3v-4h2v16zM408 156v-16h3v-4h2v20z" fill="#7be836"/>',
        '        <animateTransform attributeName="transform" type="skewX" values="0;-2.5;0;2;0" dur="5.6s" begin="-1.8s" repeatCount="indefinite"/>',
        '      </g>',
        '    </g>',
        '  </g>',
        # Subtle inner border around the rounded Jurassic scene + vertical panel divider
        '  <rect x="18" y="44" width="406" height="112" rx="6" fill="none" stroke="#30363d" stroke-opacity="0.9"/>',
        '  <path d="M434 44V156" stroke="#21262d"/>',
    ]
    return lines


def _render_available_jurassic_time_and_footer(
    expiry_text: str,
    mode_badge: str,
) -> list[str]:
    """Render the right-side clock icon, dynamic `Available till` line, divider, and green footer."""
    if " · " in mode_badge:
        prefix, suffix = mode_badge.split(" · ", 1)
        badge_markup = (
            f'  <text x="828" y="152" fill="#6e7681" font-size="10.5" text-anchor="end">'
            f'{escape(prefix)} · <tspan fill="#4ade80" font-weight="700">{escape(suffix)}</tspan></text>'
        )
    else:
        badge_markup = (
            f'  <text x="828" y="152" fill="#4ade80" font-size="10.5" font-weight="700" text-anchor="end">'
            f'{escape(mode_badge)}</text>'
        )

    return [
        # Minimalist green clock icon + separator + dynamic "Available till HH:MM IST"
        '  <g id="available-time-row">',
        '    <circle cx="455" cy="127" r="6" fill="none" stroke="#4ade80" stroke-width="1.4"/>',
        '    <path d="M455 123.5V127L457.5 128.5" fill="none" stroke="#4ade80" stroke-width="1.4" stroke-linecap="round"/>',
        '    <path d="M469 121V133" stroke="#30363d"/>',
        f'    <text x="479" y="131" fill="#e6edf3" font-size="12">{escape(expiry_text)}</text>',
        '  </g>',
        '  <path d="M446 139H828" stroke="#21262d"/>',
        badge_markup,
    ]


# ---------------------------------------------------------------------------
# Sleeping Medieval Dragon Night pixel-art sprites & scene renderer
# Palette: Dusty Lavender & Silver (#25283A, #2D3249, #626779, #77758D,
# #E5DDDC, #D9AE8E, #171B2A, #B9A5CA)
# ---------------------------------------------------------------------------

_SLEEPING_CLOUD_PALETTE = {
    "D": "#34374D",
    "M": "#4F526B",
    "L": "#77758D",
}
_SLEEPING_CLOUD_WIDE_GRID = (
    "........LLLLLL............",
    ".....LLLMMMMMMLLL.........",
    "..LLLMMMMMMMMMMMMLLL......",
    ".LMMMMMMMMMMMMMMMMMML.....",
    "DDDDDDDDDDDDDDDDDDDDDD....",
)
_SLEEPING_CLOUD_SMALL_GRID = (
    "....LLLLL.......",
    "..LLMMMMMLL.....",
    ".LMMMMMMMMML....",
    "DDDDDDDDDDDDD...",
)

_DRAGON_PALETTE = {
    "K": "#171B2A",
    "D": "#2F2E47",
    "M": "#4E4B6C",
    "L": "#77758D",
    "A": "#B9A5CA",
    "S": "#E5DDDC",
}

# 3-frame medieval fantasy dragon flying left-to-right (36x16 cells at 2px = 72x32px)
# Broad bat-like twin wings with silver leading edges, dark-lavender horned head,
# and a long sinuous undulating tail.
_DRAGON_FRAME_UP = (
    ".............KKK....................",
    "............KSSAK...................",
    "...........KSAAAMK..KK..............",
    "..........KSAMMMDK.KSAK......KS.....",
    "..........KAMMMDDDDKMMK.....KSSK....",
    "...........KMMMDDDDMMDK....KMLMKK...",
    "............KMMDDDDMMDK...KMLMMMMK..",
    "..KK.........KMMDDDDMMKKKKMLMKMMMK..",
    ".KAAK.........KMMMMMMMLLLLMMMKMMMMK.",
    "KADMMKK........KKMMMMMMMMMMMMMKKKK..",
    ".KKDDMMKKKKKKKKKKMMMMMMMMMDDKK......",
    "...KKKDDDDDMMMMMMMMMMMMDDDDK........",
    ".......KKKKKDDDDDDDDDDDKKKK.........",
    ".............KKK....KKK.............",
)

_DRAGON_FRAME_MID = (
    "....................................",
    "....................................",
    ".............................KS.....",
    "...........KKKKKKKK.........KSSK....",
    ".........KKSSAAAMMMKK......KMLMKK...",
    ".......KKSAAAMMMMMMDDKK...KMLMMMMK..",
    ".....KKAAAMMMMMMDDDDMMMKKKMLMKMMMK..",
    "..KK..KKKMMMMMMMDDDDMMMMMMLMMKMMMMK.",
    ".KAAKK...KKKMMMMMMMMMMMMMMMMMMKKKK..",
    "KADMMMKKKKKKKMMMMMMMMMMMMMDDKK......",
    "..KKKDDDDDDMMMMMMMMMMMMDDDDK........",
    ".......KKKKKDDDDDDDDDDDKKKK.........",
    ".............KKK....KKK.............",
    "....................................",
)

_DRAGON_FRAME_DOWN = (
    "....................................",
    "....................................",
    ".............................KS.....",
    "............................KSSK....",
    "...........................KMLMKK...",
    "..........................KMLMMMMK..",
    "....KK.......KKKKKKKKKKKKKMLMKMMMK..",
    "..KKAAK....KKSSAAAAMMMMMMMLMMKMMMMK.",
    ".KADMMMKKKKSSAAAMMMMMMMMMMMMMMKKKK..",
    "..KKKDDDDDDMMMMMMDDDDMMMMMDDKK......",
    ".......KKKKKMMMMDDDDDMMMMDDK........",
    "...........KMMMDDDDDKK.KKKK.........",
    "..........KAMMMDDDDK................",
    "..........KSAMMMDMK.................",
    "...........KSAAAMK..................",
    "............KKKKK...................",
)


def _render_sleeping_dragon_defs() -> list[str]:
    """Gradients, clipPath, and reduced-motion CSS for Sleeping Medieval Dragon Night."""
    return [
        '  <defs>',
        '    <clipPath id="sleeping-dragon-clip">',
        '      <rect x="18" y="44" width="382" height="112" rx="6"/>',
        '    </clipPath>',
        '    <linearGradient id="sleeping-sky" x1="0" y1="44" x2="0" y2="124" gradientUnits="userSpaceOnUse">',
        '      <stop offset="0%" stop-color="#1A1D2E"/>',
        '      <stop offset="52%" stop-color="#25283A"/>',
        '      <stop offset="100%" stop-color="#43455E"/>',
        '    </linearGradient>',
        '    <radialGradient id="sleeping-moon-halo" cx="86" cy="67" r="46" gradientUnits="userSpaceOnUse">',
        '      <stop offset="0%" stop-color="#E5DDDC" stop-opacity="0.24"/>',
        '      <stop offset="45%" stop-color="#B9A5CA" stop-opacity="0.11"/>',
        '      <stop offset="100%" stop-color="#25283A" stop-opacity="0"/>',
        '    </radialGradient>',
        '    <linearGradient id="sleeping-river-grad" x1="0" y1="130" x2="0" y2="156" gradientUnits="userSpaceOnUse">',
        '      <stop offset="0%" stop-color="#171B2A"/>',
        '      <stop offset="55%" stop-color="#22263B"/>',
        '      <stop offset="100%" stop-color="#2D3249"/>',
        '    </linearGradient>',
        '  </defs>',
        '  <style>',
        '    @media (prefers-reduced-motion: reduce) {',
        '      .sleep-motion { transform: none !important; }',
        '      .sleep-wing-1 { opacity: 1 !important; }',
        '      .sleep-wing-2, .sleep-wing-3 { display: none !important; }',
        '      .sleep-shooting { display: none !important; }',
        '      .sleep-twinkle { opacity: 0.7 !important; }',
        '    }',
        '  </style>',
    ]


def _render_sleeping_dragon_scene() -> list[str]:
    """Render the animated Medieval Dragon Night scene (Dusty Lavender & Silver)."""
    cloud_wide = _palette_grid_to_paths(_SLEEPING_CLOUD_WIDE_GRID, _SLEEPING_CLOUD_PALETTE, 0, 0, cell=2, indent="        ")
    cloud_small = _palette_grid_to_paths(_SLEEPING_CLOUD_SMALL_GRID, _SLEEPING_CLOUD_PALETTE, 0, 0, cell=2, indent="        ")

    dragon_up = _palette_grid_to_paths(_DRAGON_FRAME_UP, _DRAGON_PALETTE, 0, 0, cell=2, indent="          ")
    dragon_mid = _palette_grid_to_paths(_DRAGON_FRAME_MID, _DRAGON_PALETTE, 0, 0, cell=2, indent="          ")
    dragon_down = _palette_grid_to_paths(_DRAGON_FRAME_DOWN, _DRAGON_PALETTE, 0, 0, cell=2, indent="          ")

    # Subtle twinkling silver & dusty-lavender stars (3–6s staggered phases)
    stars = [
        (32, 52, 2, "#E5DDDC", "3.8s", "0s"),
        (54, 62, 1.5, "#B9A5CA", "4.6s", "-1.4s"),
        (132, 50, 2, "#E5DDDC", "5.2s", "-2.1s"),
        (156, 66, 1.5, "#77758D", "3.4s", "-0.9s"),
        (188, 53, 2, "#B9A5CA", "4.9s", "-3.2s"),
        (224, 59, 1.5, "#E5DDDC", "4.1s", "-1.8s"),
        (264, 50, 2, "#E5DDDC", "5.6s", "-0.5s"),
        (308, 56, 1.5, "#B9A5CA", "3.6s", "-2.7s"),
        (348, 51, 2, "#E5DDDC", "4.4s", "-1.1s"),
        (378, 63, 1.5, "#77758D", "5.4s", "-3.8s"),
    ]
    star_lines = [
        f'      <rect class="sleep-twinkle" x="{sx}" y="{sy}" width="{sz}" height="{sz}" fill="{col}" opacity="0.6">'
        f'<animate attributeName="opacity" values="0.2;0.88;0.2" dur="{dur}" begin="{begin}" repeatCount="indefinite"/>'
        f'</rect>'
        for sx, sy, sz, col, dur, begin in stars
    ]

    # Warm flickering town & castle windows (x, y, w, h, dur, begin)
    warm_windows = [
        # Castle tower windows
        (268, 86, 2, 4, "5.2s", "-0.6s"),
        (284, 82, 2, 4, "6.1s", "-2.4s"),
        (294, 88, 2, 3, "4.8s", "-1.2s"),
        (308, 84, 2, 4, "5.8s", "-3.3s"),
        # Left bank medieval stone houses
        (30, 114, 3, 4, "4.6s", "-1.1s"),
        (38, 114, 3, 4, "5.4s", "-2.8s"),
        (54, 110, 3, 4, "6.2s", "-0.4s"),
        (62, 118, 3, 4, "4.9s", "-3.1s"),
        (82, 112, 3, 4, "5.7s", "-1.9s"),
        (98, 116, 3, 4, "4.4s", "-0.8s"),
        (114, 114, 3, 4, "6.0s", "-2.2s"),
        # Right bank medieval stone houses
        (302, 116, 3, 4, "5.1s", "-1.5s"),
        (318, 112, 3, 4, "4.7s", "-3.0s"),
        (336, 116, 3, 4, "5.9s", "-0.9s"),
        (354, 110, 3, 4, "4.5s", "-2.5s"),
        (372, 114, 3, 4, "6.3s", "-1.3s"),
        (386, 118, 3, 4, "5.0s", "-3.6s"),
    ]
    window_lines = [
        f'      <rect x="{wx}" y="{wy}" width="{ww}" height="{wh}" fill="#D9AE8E" opacity="0.82">'
        f'<animate attributeName="opacity" values="0.62;0.96;0.74;0.92;0.62" dur="{dur}" begin="{begin}" repeatCount="indefinite"/>'
        f'</rect>'
        for wx, wy, ww, wh, dur, begin in warm_windows
    ]

    # Warm riverside & bridge street lanterns (x, y_top, dur, begin)
    lanterns = [
        (74, 124, "4.8s", "0s"),
        (126, 125, "5.6s", "-1.7s"),
        (182, 118, "5.0s", "-2.8s"),
        (246, 118, "6.0s", "-0.9s"),
        (298, 125, "4.6s", "-3.2s"),
        (350, 124, "5.4s", "-1.4s"),
    ]
    lantern_lines: list[str] = []
    for lx, ly, dur, begin in lanterns:
        lantern_lines.extend([
            f'      <rect x="{lx}" y="{ly}" width="2" height="8" fill="#171B2A"/>',
            f'      <circle cx="{lx + 1}" cy="{ly}" r="5.5" fill="#D9AE8E" opacity="0.22">'
            f'<animate attributeName="opacity" values="0.16;0.3;0.16" dur="{dur}" begin="{begin}" repeatCount="indefinite"/>'
            f'</circle>',
            f'      <rect x="{lx}" y="{ly - 1}" width="2" height="2" fill="#F2D2B6"/>',
        ])

    lines = [
        '  <g id="sleeping-dragon-scene" clip-path="url(#sleeping-dragon-clip)">',
        # 1. Dusty Lavender & Slate Night Sky + understated silver lunar glow
        '    <rect x="18" y="44" width="382" height="112" fill="url(#sleeping-sky)"/>',
        '    <rect x="18" y="44" width="382" height="112" fill="url(#sleeping-moon-halo)"/>',
        # 2. Scattered twinkling stars + occasional soft shooting star (every 20s)
        '    <g id="sleeping-stars" shape-rendering="crispEdges">',
        *star_lines,
        '      <g id="sleeping-shooting-star" class="sleep-shooting" opacity="0">',
        '        <line x1="0" y1="0" x2="22" y2="-7" stroke="#E5DDDC" stroke-width="1.4" stroke-linecap="round" opacity="0.8"/>',
        '        <rect x="-1" y="-1" width="2.5" height="2.5" fill="#F4F0EF"/>',
        '        <animateTransform attributeName="transform" type="translate" values="332 50;332 50;246 76;246 76" keyTimes="0;0.74;0.82;1" dur="20s" begin="-4s" repeatCount="indefinite"/>',
        '        <animate attributeName="opacity" values="0;0;0.85;0;0" keyTimes="0;0.74;0.77;0.82;1" dur="20s" begin="-4s" repeatCount="indefinite"/>',
        '      </g>',
        '    </g>',
        # 3. Large stationary silver moon with subtle lunar craters
        '    <g id="sleeping-moon">',
        '      <circle cx="86" cy="67" r="14.5" fill="#E5DDDC"/>',
        '      <circle cx="84" cy="65" r="13" fill="#F2ECEB" opacity="0.45"/>',
        '      <g fill="#C6BEC4" opacity="0.55" shape-rendering="crispEdges">',
        '        <rect x="78" y="61" width="5" height="4"/>',
        '        <rect x="89" y="69" width="6" height="4"/>',
        '        <rect x="82" y="72" width="4" height="3"/>',
        '        <rect x="91" y="60" width="3" height="3"/>',
        '      </g>',
        '    </g>',
        # 4. Soft parallax night clouds (28s & 38s)
        '    <g id="sleeping-clouds" shape-rendering="crispEdges" opacity="0.72">',
        '      <g class="sleep-motion" transform="translate(216 54)">',
        *cloud_wide,
        '        <animateTransform attributeName="transform" type="translate" from="408 54" to="-64 54" dur="36s" begin="-14.6s" repeatCount="indefinite"/>',
        '      </g>',
        '      <g class="sleep-motion" transform="translate(36 74)">',
        *cloud_small,
        '        <animateTransform attributeName="transform" type="translate" from="408 74" to="-44 74" dur="28s" begin="-23.0s" repeatCount="indefinite"/>',
        '      </g>',
        '      <g class="sleep-motion" transform="translate(324 68)">',
        *cloud_small,
        '        <animateTransform attributeName="transform" type="translate" from="408 68" to="-44 68" dur="32s" begin="-6.0s" repeatCount="indefinite"/>',
        '      </g>',
        '    </g>',
        # 5. Layered distant lavender-gray mountains
        '    <g id="sleeping-mountains" shape-rendering="crispEdges">',
        '      <polygon points="18,112 52,84 86,96 128,78 168,94 214,80 256,92 298,78 344,90 380,80 400,88 400,118 18,118" fill="#464A61"/>',
        '      <polygon points="52,84 64,91 56,94 46,90" fill="#626779"/>',
        '      <polygon points="128,78 142,86 132,90 120,85" fill="#626779"/>',
        '      <polygon points="214,80 228,88 218,91 206,86" fill="#626779"/>',
        '      <polygon points="18,118 48,96 92,106 144,92 192,108 242,96 290,88 338,98 378,92 400,100 400,126 18,126" fill="#2D3249"/>',
        '      <polygon points="144,92 158,100 146,104 134,98" fill="#484D66"/>',
        '      <polygon points="290,88 302,94 292,98 280,94" fill="#484D66"/>',
        '    </g>',
        # 6. Distant Medieval Castle with multiple crenellated & pointed towers
        '    <g id="sleeping-castle" shape-rendering="crispEdges">',
        # Castle hill crag
        '      <polygon points="246,118 258,100 326,98 342,118" fill="#23273B"/>',
        # Castle main keep & curtain walls
        '      <rect x="260" y="88" width="60" height="18" fill="#2D3249"/>',
        '      <rect x="260" y="88" width="12" height="18" fill="#444963"/>',
        # Left tall spire tower
        '      <rect x="264" y="76" width="10" height="28" fill="#2D3249"/>',
        '      <rect x="264" y="76" width="4" height="28" fill="#525670"/>',
        '      <polygon points="262,76 269,66 276,76" fill="#1B1F32"/>',
        '      <polygon points="262,76 269,66 268,76" fill="#484C66"/>',
        # Central grand keep tower
        '      <rect x="280" y="72" width="14" height="32" fill="#2D3249"/>',
        '      <rect x="280" y="72" width="5" height="32" fill="#5A5E78"/>',
        '      <polygon points="278,72 287,60 296,72" fill="#171B2A"/>',
        '      <polygon points="278,72 287,60 286,72" fill="#4E526C"/>',
        # Right twin turret towers
        '      <rect x="302" y="78" width="12" height="26" fill="#2D3249"/>',
        '      <rect x="302" y="78" width="4" height="26" fill="#4C506A"/>',
        '      <polygon points="300,78 308,68 316,78" fill="#1B1F32"/>',
        '      <rect x="318" y="84" width="8" height="20" fill="#25293E"/>',
        '      <path d="M260 88h4v-2h4v2h4v-2h4v2M294 88h4v-2h4v2" fill="#525670"/>',
        # Soft silver moonlit castle rim
        '      <path d="M264 76h1v28h-1zM280 72h1v32h-1zM302 78h1v26h-1z" fill="#B9A5CA" opacity="0.5"/>',
        '    </g>',
        # 7. Flying Fantasy Dragon (Main animated hero — graceful glide + 3-frame wing cycle)
        '    <g id="sleeping-dragon" shape-rendering="crispEdges">',
        '      <g class="sleep-motion" transform="translate(148 56)">',
        '        <animateTransform attributeName="transform" type="translate" values="-78 60;54 54;156 58;270 51;412 57" dur="22s" begin="-9.6s" repeatCount="indefinite"/>',
        '        <g class="sleep-wing-1" opacity="1">',
        *dragon_up,
        '          <animate attributeName="opacity" values="1;0;0;0" keyTimes="0;0.25;0.5;0.75" calcMode="discrete" dur="1.5s" repeatCount="indefinite"/>',
        '        </g>',
        '        <g class="sleep-wing-2" opacity="0">',
        *dragon_mid,
        '          <animate attributeName="opacity" values="0;1;0;1" keyTimes="0;0.25;0.5;0.75" calcMode="discrete" dur="1.5s" repeatCount="indefinite"/>',
        '        </g>',
        '        <g class="sleep-wing-3" opacity="0">',
        *dragon_down,
        '          <animate attributeName="opacity" values="0;0;1;0" keyTimes="0;0.25;0.5;0.75" calcMode="discrete" dur="1.5s" repeatCount="indefinite"/>',
        '        </g>',
        '      </g>',
        '    </g>',
        # 8. Medieval European stone houses (left & right riverbanks, dark pitched roofs)
        '    <g id="sleeping-town" shape-rendering="crispEdges">',
        # Left riverside stone quay wall
        '      <rect x="18" y="126" width="122" height="8" fill="#23273B"/>',
        '      <rect x="18" y="126" width="122" height="2" fill="#626779"/>',
        # Left bank houses (pitched medieval gables + chimneys)
        '      <rect x="22" y="108" width="26" height="18" fill="#2D3249"/>',
        '      <rect x="22" y="108" width="6" height="18" fill="#3F445E"/>',
        '      <polygon points="20,108 35,96 50,108" fill="#171B2A"/>',
        '      <polygon points="20,108 35,96 34,108" fill="#2A2F45"/>',
        '      <rect x="40" y="96" width="4" height="8" fill="#25283A"/>',
        '      <rect x="48" y="102" width="24" height="24" fill="#282D42"/>',
        '      <rect x="48" y="102" width="6" height="24" fill="#444963"/>',
        '      <polygon points="46,102 60,91 74,102" fill="#171B2A"/>',
        '      <polygon points="46,102 60,91 59,102" fill="#353A52"/>',
        '      <rect x="74" y="106" width="32" height="20" fill="#2D3249"/>',
        '      <rect x="74" y="106" width="8" height="20" fill="#464B66"/>',
        '      <polygon points="72,106 90,95 108,106" fill="#1B1F32"/>',
        '      <rect x="106" y="109" width="22" height="17" fill="#25293E"/>',
        '      <polygon points="104,109 117,99 130,109" fill="#171B2A"/>',
        # Right riverside stone quay wall
        '      <rect x="288" y="126" width="112" height="8" fill="#23273B"/>',
        '      <rect x="288" y="126" width="112" height="2" fill="#626779"/>',
        # Right bank medieval stone houses
        '      <rect x="294" y="108" width="24" height="18" fill="#2D3249"/>',
        '      <rect x="294" y="108" width="6" height="18" fill="#464B66"/>',
        '      <polygon points="292,108 306,97 320,108" fill="#171B2A"/>',
        '      <rect x="318" y="103" width="28" height="23" fill="#272C40"/>',
        '      <rect x="318" y="103" width="6" height="23" fill="#434862"/>',
        '      <polygon points="316,103 332,92 348,103" fill="#1B1F32"/>',
        '      <polygon points="316,103 332,92 331,103" fill="#353A52"/>',
        '      <rect x="346" y="105" width="26" height="21" fill="#2D3249"/>',
        '      <rect x="346" y="105" width="6" height="21" fill="#484D67"/>',
        '      <polygon points="344,105 359,94 374,105" fill="#171B2A"/>',
        '      <rect x="372" y="109" width="28" height="17" fill="#25293E"/>',
        '      <polygon points="370,109 386,98 402,109" fill="#171B2A"/>',
        # Gently flickering warm medieval windows
        *window_lines,
        '    </g>',
        # 9. Calm Night River + Gentle Animated Moon & Lantern Reflections
        '    <g id="sleeping-river" shape-rendering="crispEdges">',
        '      <rect x="18" y="132" width="382" height="24" fill="url(#sleeping-river-grad)"/>',
        # Soft moonlight silver reflection column beneath the moon (x≈86)
        '      <g class="sleep-motion" fill="#E5DDDC" opacity="0.45">',
        '        <path d="M76 136h20v1.5h-20zM79 140h15v1.5h-15zM75 144h22v1.5h-22zM80 148h13v1.5h-13zM78 152h16v1.5h-16z"/>',
        '        <animateTransform attributeName="transform" type="translate" values="-2 0;2.5 0;-2 0" dur="5.6s" repeatCount="indefinite"/>',
        '        <animate attributeName="opacity" values="0.32;0.58;0.32" dur="5.6s" repeatCount="indefinite"/>',
        '      </g>',
        # Soft warm amber reflections beneath street & bridge lanterns
        '      <g class="sleep-motion" fill="#D9AE8E" opacity="0.38">',
        '        <path d="M71 138h8v1.5h-8zM73 142h5v1.5h-5zM123 139h8v1.5h-8zM125 144h5v1.5h-5zM179 141h8v1.5h-8zM243 141h8v1.5h-8zM295 139h8v1.5h-8zM347 138h8v1.5h-8z"/>',
        '        <animateTransform attributeName="transform" type="translate" values="1.5 0;-1.5 0;1.5 0" dur="4.8s" begin="-1.2s" repeatCount="indefinite"/>',
        '        <animate attributeName="opacity" values="0.24;0.5;0.24" dur="4.8s" begin="-1.2s" repeatCount="indefinite"/>',
        '      </g>',
        # Calm horizontal dusty-lavender water wave ripples
        '      <g class="sleep-motion" fill="#77758D" opacity="0.32">',
        '        <path d="M36 139h24v1h-24zM148 145h28v1h-28zM206 149h32v1h-32zM272 143h22v1h-22zM336 150h30v1h-30z"/>',
        '        <animateTransform attributeName="transform" type="translate" values="0 0;8 0;0 0" dur="7.4s" repeatCount="indefinite"/>',
        '        <animate attributeName="opacity" values="0.2;0.42;0.2" dur="7.4s" repeatCount="indefinite"/>',
        '      </g>',
        '    </g>',
        # 10. Medieval 3-Arch Stone Bridge & Warm Street Lanterns
        '    <g id="sleeping-bridge" shape-rendering="crispEdges">',
        # Bridge masonry span across river (x=136..292)
        '      <path d="M136 122h156v14h-18v-6h-4v-2h-22v2h-4v6h-12v-6h-4v-2h-26v2h-4v6h-12v-6h-4v-2h-22v2h-4v6h-20z" fill="#353A52"/>',
        '      <path d="M136 122h156v3h-156z" fill="#626779"/>',
        '      <path d="M136 122h156v1h-156z" fill="#B9A5CA" opacity="0.6"/>',
        # Deep arch interior shadows
        '      <path d="M156 136v-6h4v-2h22v2h4v6zM198 136v-6h4v-2h26v2h4v6zM244 136v-6h4v-2h22v2h4v6z" fill="#171B2A"/>',
        # Stone masonry pier highlights
        '      <rect x="148" y="126" width="6" height="10" fill="#4A4F68"/>',
        '      <rect x="188" y="126" width="8" height="10" fill="#4A4F68"/>',
        '      <rect x="234" y="126" width="8" height="10" fill="#4A4F68"/>',
        '      <rect x="276" y="126" width="6" height="10" fill="#4A4F68"/>',
        # Warm street & bridge lanterns
        *lantern_lines,
        '    </g>',
        '  </g>',
        # Subtle muted frame around the medieval scene + vertical separator
        '  <rect x="18" y="44" width="382" height="112" rx="6" fill="none" stroke="#30363d" stroke-opacity="0.9"/>',
        '  <path d="M412 44V156" stroke="#25283A"/>',
    ]
    return lines


def _render_sleeping_right_footer(expiry_text: str, mode_badge: str) -> list[str]:
    """Render right-panel footer for Sleeping with silver-lavender accents."""
    if " · " in mode_badge:
        prefix, suffix = mode_badge.split(" · ", 1)
        badge_markup = (
            f'  <text x="828" y="152" fill="#626779" font-size="10.5" text-anchor="end">'
            f'{escape(prefix)} · <tspan fill="#B9A5CA" font-weight="700">{escape(suffix)}</tspan></text>'
        )
    else:
        badge_markup = (
            f'  <text x="828" y="152" fill="#B9A5CA" font-size="10.5" font-weight="700" text-anchor="end">'
            f'{escape(mode_badge)}</text>'
        )

    return [
        '  <path d="M428 139H828" stroke="#25283A"/>',
        f'  <text x="428" y="152" fill="#B9A5CA" font-size="11">{escape(expiry_text)}</text>',
        badge_markup,
    ]


def render_status_svg_markup(data: dict[str, Any]) -> str:
    """Render deterministic, valid XML SVG markup for a validated resolved status."""
    errors = validate_renderable_status(data)
    if errors:
        raise ValueError("Cannot render invalid status data:\n" + "\n".join(f"- {e}" for e in errors))

    status_id: str = data["status"]
    title: str = _truncate(data["title"], 38)
    availability: str = data["availability"]
    message: str = data["message"]
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
                tags=tags,
                avail_color=avail_color,
                avail_label=avail_label,
                text_x=318,
                title_size=24,
                title_y=77,
                msg_y=100,
                tag_y=109,
                max_msg_len=62,
            ),
            *_render_footer(expiry_text, mode_badge),
            '</svg>',
        ]
    elif status_id == "available":
        lines = [
            *_render_terminal_frame_open(
                aria_label,
                accent,
                defs_and_styles=_render_available_jurassic_defs(),
                show_left_bar=False,
            ),
            *_render_available_jurassic_scene(),
            *_render_status_info_block(
                title=title,
                message=message,
                tags=tags,
                avail_color=avail_color,
                avail_label=avail_label,
                text_x=448,
                title_size=26,
                title_y=72,
                title_color="#7ee787",
                pill_y=51,
                msg_y=91,
                msg_size=12.5,
                tag_y=98,
                max_msg_len=48,
                pulse_dot=True,
                tag_fill="#0e2c1c",
                tag_stroke="#3fb950",
                tag_text_color="#7ee787",
            ),
            *_render_available_jurassic_time_and_footer(expiry_text, mode_badge),
            '</svg>',
        ]
    elif status_id == "sleeping":
        lines = [
            *_render_terminal_frame_open(
                aria_label,
                accent,
                defs_and_styles=_render_sleeping_dragon_defs(),
                show_left_bar=False,
            ),
            *_render_sleeping_dragon_scene(),
            *_render_status_info_block(
                title=title,
                message=message,
                tags=tags,
                avail_color="#B9A5CA",
                avail_label=avail_label,
                text_x=428,
                title_size=26,
                title_y=74,
                title_color="#E5DDDC",
                pill_y=52,
                msg_y=96,
                msg_size=12.5,
                tag_y=106,
                max_msg_len=50,
                tag_fill="#1D2033",
                tag_stroke="#626779",
                tag_text_color="#E5DDDC",
            ),
            *_render_sleeping_right_footer(expiry_text, mode_badge),
            '</svg>',
        ]
    else:
        raise ValueError(f"Unsupported status ID {status_id!r}.")

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
        help="Path to output SVG file (default: img/status/current.svg).",
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
