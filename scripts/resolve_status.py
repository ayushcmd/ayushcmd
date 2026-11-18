#!/usr/bin/env python3
"""CLI tool to validate and resolve Ayush Live Status configuration.

Examples:
    # Strict PR validation (fails on placeholders, invalid presets, bad HH:MM)
    python scripts/resolve_status.py --strict --check-only

    # Resolve current status and write data/status_resolved.json if changed
    python scripts/resolve_status.py

    # Deterministic local test at a specific IST timestamp
    python scripts/resolve_status.py --effective-date 2026-11-18 \
        --now 2026-11-18T21:30:00+05:30 --check-only --print-json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from status_engine import (  # noqa: E402
    DEFAULT_FALLBACK_STATUS,
    StatusValidationError,
    ensure_ist,
    load_json_file,
    load_presets,
    parse_daily_branch_date,
    parse_iso_date,
    parse_iso_datetime,
    resolve_status,
    validate_resolved_status,
    validate_status_meta,
    write_resolved_if_changed,
)

DEFAULT_STATUS_FILE = REPO_ROOT / "data" / "status.json"
DEFAULT_PRESETS_FILE = REPO_ROOT / "data" / "status_presets.json"
DEFAULT_META_FILE = REPO_ROOT / "data" / "status_meta.json"
DEFAULT_OUT_FILE = REPO_ROOT / "data" / "status_resolved.json"


def _emit_error(message: str, file_path: str | None = None) -> None:
    if os.environ.get("GITHUB_ACTIONS") == "true":
        prefix = f"::error file={file_path}::" if file_path else "::error::"
        print(f"{prefix}{message}", file=sys.stderr)
    else:
        print(f"ERROR: {message}", file=sys.stderr)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate and resolve Ayush Live Status configuration."
    )
    parser.add_argument(
        "--status-file",
        type=Path,
        default=DEFAULT_STATUS_FILE,
        help="Path to the two-field user status JSON file (default: data/status.json).",
    )
    parser.add_argument(
        "--presets-file",
        type=Path,
        default=DEFAULT_PRESETS_FILE,
        help="Path to the presets catalog JSON file (default: data/status_presets.json).",
    )
    parser.add_argument(
        "--meta-file",
        type=Path,
        default=DEFAULT_META_FILE,
        help="Path to the status metadata JSON file (default: data/status_meta.json).",
    )
    parser.add_argument(
        "--out-file",
        type=Path,
        default=DEFAULT_OUT_FILE,
        help="Path to write resolved status JSON (default: data/status_resolved.json).",
    )
    parser.add_argument(
        "--effective-date",
        type=str,
        default=None,
        help="Override effective IST date (YYYY-MM-DD) instead of reading --meta-file.",
    )
    parser.add_argument(
        "--now",
        type=str,
        default=None,
        help="Override current timestamp with an aware ISO-8601 string (e.g. 2026-11-18T14:00:00+05:30).",
    )
    parser.add_argument(
        "--branch",
        type=str,
        default=None,
        help="Optional git branch name; if status/YYYY-MM-DD, checks effective_date matches.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Fail with non-zero exit code on any validation error or placeholder.",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Validate and resolve without writing --out-file.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Rewrite --out-file even when semantic status fields did not change.",
    )
    parser.add_argument(
        "--print-json",
        action="store_true",
        help="Print resolved status JSON to stdout.",
    )
    return parser


def _safe_load_json(path: Path, label: str) -> tuple[Any, str | None]:
    if not path.exists():
        return None, f"{label} not found at {path}."
    try:
        return load_json_file(path), None
    except json.JSONDecodeError as exc:
        return None, f"{label} ({path}) contains invalid JSON: {exc}"
    except OSError as exc:
        return None, f"Could not read {label} ({path}): {exc}"


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        current_ist = parse_iso_datetime(args.now) if args.now else ensure_ist()
    except ValueError as exc:
        _emit_error(f"Invalid --now timestamp: {exc}")
        return 1

    try:
        presets, fallback_id = load_presets(args.presets_file)
    except (OSError, json.JSONDecodeError, StatusValidationError) as exc:
        _emit_error(f"Preset catalog validation failed: {exc}", str(args.presets_file))
        if args.strict:
            return 1
        presets = None
        fallback_id = DEFAULT_FALLBACK_STATUS

    user_config, config_err = _safe_load_json(args.status_file, "Status config")

    expected_branch_date = parse_daily_branch_date(args.branch)
    effective_date = None
    meta_errors: list[str] = []

    if args.effective_date is not None:
        try:
            effective_date = parse_iso_date(args.effective_date)
        except ValueError as exc:
            meta_errors.append(f"Invalid --effective-date: {exc}")
    else:
        meta_data, meta_load_err = _safe_load_json(args.meta_file, "Status metadata")
        if meta_load_err:
            meta_errors.append(meta_load_err)
        else:
            meta_errors.extend(validate_status_meta(meta_data, expected_date=expected_branch_date))
            if not meta_errors and isinstance(meta_data, dict):
                effective_date = parse_iso_date(meta_data["effective_date"])

    if args.strict:
        errors: list[str] = []
        if config_err:
            errors.append(config_err)
        errors.extend(meta_errors)
        if errors:
            for err in errors:
                _emit_error(err, str(args.status_file))
            return 1

        try:
            assert presets is not None
            resolved = resolve_status(
                user_config,
                presets,
                effective_date,
                now=current_ist,
                strict=True,
                fallback_id=fallback_id,
            )
        except StatusValidationError as exc:
            for err in exc.errors:
                _emit_error(err, str(args.status_file))
            return 1
    else:
        if config_err:
            print(f"WARNING: {config_err}", file=sys.stderr)
        if meta_errors:
            print(f"WARNING: {'; '.join(meta_errors)}", file=sys.stderr)
        resolved = resolve_status(
            None if config_err else user_config,
            presets,
            effective_date if not meta_errors else None,
            now=current_ist,
            strict=False,
            fallback_id=fallback_id,
        )

    schema_errors = validate_resolved_status(resolved)
    if schema_errors:
        for err in schema_errors:
            _emit_error(err)
        return 1

    wrote = False
    if not args.check_only:
        wrote = write_resolved_if_changed(resolved, args.out_file, force=args.force)

    if args.print_json:
        print(json.dumps(resolved, indent=2, ensure_ascii=False))
    else:
        if resolved["resolution_source"] == "night_override":
            state_label = "NIGHT OVERRIDE -> sleeping"
        elif resolved["expired"]:
            state_label = "EXPIRED -> offline fallback"
        else:
            state_label = "ACTIVE"
        action_label = (
            "checked only"
            if args.check_only
            else ("updated" if wrote else "unchanged")
        )
        print(
            f"Live status [{state_label}] ({action_label}): "
            f"{resolved['emoji']} {resolved['title']} "
            f"(status={resolved['status']}, availability={resolved['availability']}, "
            f"until={resolved['until']}, source={resolved['resolution_source']}, "
            f"effective_date={resolved['computed']['effective_date']})"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
