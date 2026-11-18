#!/usr/bin/env python3
"""Core engine for the Ayush Live Status system.

Resolves Ayush's two-field manual status configuration (`data/status.json`)
against the central preset catalog (`data/status_presets.json`) and daily
effective date metadata (`data/status_meta.json`), using `Asia/Kolkata` (IST)
with a 07:00 AM daily boundary and an automatic night override (`sleeping`,
22:00 through 05:59:59 IST).

Explicit resolution priority:
1. Scheduled night override (22:00–05:59:59 IST -> `sleeping`, offline availability).
2. Valid, unexpired manually selected status.
3. Safe offline fallback (`offline`).

This module uses only the Python standard library and never inspects GitHub
commits, events, or external activity to infer presence.
"""

from __future__ import annotations

import copy
import difflib
import json
import re
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

SCHEMA_VERSION = 1
TIMEZONE_NAME = "Asia/Kolkata"
DAY_BOUNDARY_HOUR = 7
DAY_BOUNDARY_MINUTE = 0
DAY_BOUNDARY_STR = f"{DAY_BOUNDARY_HOUR:02d}:{DAY_BOUNDARY_MINUTE:02d}"

NIGHT_START_HOUR = 22
NIGHT_START_MINUTE = 0
NIGHT_END_HOUR = 6
NIGHT_END_MINUTE = 0
NIGHT_START_STR = f"{NIGHT_START_HOUR:02d}:{NIGHT_START_MINUTE:02d}"
NIGHT_END_STR = f"{NIGHT_END_HOUR:02d}:{NIGHT_END_MINUTE:02d}"
NIGHT_WINDOW_STR = f"{NIGHT_START_STR}-{NIGHT_END_STR}"

DEFAULT_FALLBACK_STATUS = "offline"
NIGHT_OVERRIDE_STATUS = "sleeping"
DEFAULT_THEME_COLOR = "#8b949e"

PLACEHOLDER_STATUS = "__SELECT_STATUS__"
PLACEHOLDER_UNTIL = "__HH:MM__"

ALLOWED_USER_KEYS = frozenset({"status", "until"})
VALID_AVAILABILITIES = frozenset({"available", "focused", "busy", "limited", "offline"})
VALID_RESOLUTION_SOURCES = frozenset({"night_override", "manual", "offline_fallback"})
MAX_TAGS = 2

_PRESET_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_HHMM_STRICT_RE = re.compile(r"^(\d{2}):(\d{2})$")
_HHMM_LOOSE_RE = re.compile(r"^(\d{1,2}):(\d{2})$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DAILY_BRANCH_RE = re.compile(r"^status/(\d{4}-\d{2}-\d{2})$")

_PLACEHOLDER_LITERALS = frozenset({
    "todo",
    "tbd",
    "changeme",
    "replace_me",
    "replace-me",
    "your_status",
    "status",
    "hh:mm",
    "pick_status",
    "select_status",
})

_FALLBACK_PRESET_EMERGENCY: dict[str, Any] = {
    "emoji": "🌙",
    "title": "Offline",
    "category": "other",
    "availability": "offline",
    "message": "Away from the keyboard — catch you next cycle",
    "tags": ["Offline"],
    "color": DEFAULT_THEME_COLOR,
}

_NIGHT_PRESET_EMERGENCY: dict[str, Any] = {
    "emoji": "😴",
    "title": "Recharging",
    "category": "life",
    "availability": "offline",
    "message": "Offline for the night — back in the morning",
    "tags": ["Sleep", "AFK"],
    "color": "#6e7681",
}


def _load_ist_timezone() -> tzinfo:
    try:
        return ZoneInfo(TIMEZONE_NAME)
    except ZoneInfoNotFoundError:
        # IST has no DST and is fixed at UTC+05:30.
        return timezone(timedelta(hours=5, minutes=30), "IST")


IST: tzinfo = _load_ist_timezone()
UTC: tzinfo = timezone.utc


class StatusValidationError(ValueError):
    """Raised when strict status, preset, or metadata validation fails."""

    def __init__(self, errors: list[str]):
        self.errors = list(errors)
        super().__init__("\n".join(f"- {err}" for err in self.errors))


def ensure_ist(dt: datetime | None = None) -> datetime:
    """Return a timezone-aware datetime converted to Asia/Kolkata (IST)."""
    if dt is None:
        return datetime.now(IST)
    if not isinstance(dt, datetime):
        raise TypeError("now must be a datetime instance or None")
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError(
            "Naive datetimes are not allowed; provide a timezone-aware datetime "
            "(for example, with +05:30 or UTC)."
        )
    return dt.astimezone(IST)


def parse_iso_datetime(value: str) -> datetime:
    """Parse an ISO-8601 timestamp string (must include a timezone offset or Z)."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Timestamp must be a non-empty ISO-8601 string.")
    normalized = value.strip()
    if normalized.endswith(("Z", "z")):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    return ensure_ist(parsed)


def parse_iso_date(value: Any) -> date:
    """Parse a strict YYYY-MM-DD calendar date."""
    if not isinstance(value, str) or not _DATE_RE.match(value):
        raise ValueError(f"Expected a YYYY-MM-DD date string, got {value!r}.")
    return date.fromisoformat(value)


def parse_daily_branch_date(branch_name: str | None) -> date | None:
    """Extract the IST date from a daily branch name (`status/YYYY-MM-DD`)."""
    if not branch_name:
        return None
    match = _DAILY_BRANCH_RE.match(branch_name.strip())
    if not match:
        return None
    try:
        return date.fromisoformat(match.group(1))
    except ValueError:
        return None


def status_date_for_datetime(dt: datetime) -> date:
    """Return the active status day for an aware datetime using the 07:00 IST boundary.

    Times from 00:00 through 06:59 IST belong to the previous calendar date's
    status cycle. At 07:00 IST, the new calendar date's status cycle begins.
    """
    dt_ist = ensure_ist(dt)
    if dt_ist.time() < time(DAY_BOUNDARY_HOUR, DAY_BOUNDARY_MINUTE):
        return dt_ist.date() - timedelta(days=1)
    return dt_ist.date()


def is_placeholder_value(value: Any) -> bool:
    """Return True if a value looks like an unfilled template placeholder."""
    if not isinstance(value, str):
        return False
    stripped = value.strip()
    if not stripped:
        return True
    if stripped in {PLACEHOLDER_STATUS, PLACEHOLDER_UNTIL}:
        return True
    if (
        (stripped.startswith("__") and stripped.endswith("__") and len(stripped) > 4)
        or (stripped.startswith("<") and stripped.endswith(">") and len(stripped) > 2)
        or (stripped.startswith("{{") and stripped.endswith("}}") and len(stripped) > 4)
    ):
        return True
    return stripped.lower() in _PLACEHOLDER_LITERALS


def parse_until_time(value: Any) -> tuple[tuple[int, int] | None, list[str]]:
    """Validate and parse a 24-hour `HH:MM` expiration time string."""
    if not isinstance(value, str):
        return None, [
            f"`until` must be a 24-hour HH:MM string (e.g. '22:00'), got {type(value).__name__}."
        ]

    if value != value.strip():
        return None, [
            f"`until` must not contain leading or trailing whitespace: {value!r}."
        ]

    if is_placeholder_value(value):
        return None, [
            f"`until` still contains a placeholder ({value!r}). "
            "Replace it with a 24-hour IST time like '22:00' or '02:00'."
        ]

    match = _HHMM_STRICT_RE.match(value)
    if not match:
        loose = _HHMM_LOOSE_RE.match(value)
        if loose:
            hour_i, minute_i = int(loose.group(1)), int(loose.group(2))
            if 0 <= hour_i <= 23 and 0 <= minute_i <= 59:
                return None, [
                    f"`until` value {value!r} must use two-digit 24-hour HH:MM format "
                    f"(use '{hour_i:02d}:{minute_i:02d}')."
                ]
        return None, [
            f"`until` value {value!r} is not a valid 24-hour HH:MM time "
            "(valid range: '00:00' to '23:59', e.g. '22:00' or '02:00')."
        ]

    hour = int(match.group(1))
    minute = int(match.group(2))
    if hour == 24 and minute == 0:
        return None, [
            "`until` cannot be '24:00'. Use '00:00' for midnight (start of the next calendar day)."
        ]
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None, [
            f"`until` value {value!r} is out of range (hours must be 00-23, minutes 00-59)."
        ]

    return (hour, minute), []


def compute_window_start(effective_date: date) -> datetime:
    """Return the 07:00 IST start of the status cycle for `effective_date`."""
    return datetime(
        effective_date.year,
        effective_date.month,
        effective_date.day,
        DAY_BOUNDARY_HOUR,
        DAY_BOUNDARY_MINUTE,
        tzinfo=IST,
    )


def is_night_window(dt: datetime) -> bool:
    """Return True when `dt` falls in the daily automatic night window (22:00–05:59:59 IST)."""
    dt_ist = ensure_ist(dt)
    current_time = dt_ist.time()
    return current_time >= time(NIGHT_START_HOUR, NIGHT_START_MINUTE) or current_time < time(
        NIGHT_END_HOUR, NIGHT_END_MINUTE
    )


def compute_night_override_end(dt: datetime) -> datetime:
    """Return the 06:00 IST timestamp when the current night override window ends."""
    dt_ist = ensure_ist(dt)
    end_date = dt_ist.date()
    if dt_ist.time() >= time(NIGHT_START_HOUR, NIGHT_START_MINUTE):
        end_date = end_date + timedelta(days=1)
    return datetime(
        end_date.year,
        end_date.month,
        end_date.day,
        NIGHT_END_HOUR,
        NIGHT_END_MINUTE,
        tzinfo=IST,
    )


def compute_expiry(effective_date: date, until_str: str) -> datetime:
    """Compute the exact IST expiration timestamp for `effective_date` and `until`.

    Day boundary is 07:00 IST:
    - `07:01` through `23:59` expire on `effective_date`.
    - `00:00` through `07:00` expire on `effective_date + 1 day` (after midnight,
      up to and including the next morning's 07:00 IST reset).
    """
    parsed, errors = parse_until_time(until_str)
    if errors or parsed is None:
        raise StatusValidationError(errors)

    hour, minute = parsed
    target_date = effective_date
    if (hour, minute) <= (DAY_BOUNDARY_HOUR, DAY_BOUNDARY_MINUTE):
        target_date = effective_date + timedelta(days=1)

    return datetime(
        target_date.year,
        target_date.month,
        target_date.day,
        hour,
        minute,
        tzinfo=IST,
    )


def load_json_file(path: Path | str) -> Any:
    """Read and parse a UTF-8 JSON file."""
    file_path = Path(path)
    with file_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def validate_presets_catalog(catalog: Any) -> list[str]:
    """Validate the central status presets catalog structure and entries."""
    errors: list[str] = []
    if not isinstance(catalog, dict):
        return ["Presets catalog root must be a JSON object."]

    presets = catalog.get("presets")
    if not isinstance(presets, dict) or not presets:
        return ["Presets catalog must contain a non-empty `presets` object."]

    fallback_id = catalog.get("default_fallback", DEFAULT_FALLBACK_STATUS)
    if not isinstance(fallback_id, str) or fallback_id not in presets:
        errors.append(
            f"`default_fallback` ({fallback_id!r}) must reference a defined preset."
        )
    if DEFAULT_FALLBACK_STATUS not in presets:
        errors.append(f"Presets catalog must define the `{DEFAULT_FALLBACK_STATUS}` preset.")

    night_id = catalog.get("night_override_status", NIGHT_OVERRIDE_STATUS)
    if not isinstance(night_id, str) or night_id not in presets:
        errors.append(
            f"`night_override_status` ({night_id!r}) must reference a defined preset."
        )
    if NIGHT_OVERRIDE_STATUS not in presets:
        errors.append(f"Presets catalog must define the `{NIGHT_OVERRIDE_STATUS}` preset.")
    elif isinstance(presets.get(NIGHT_OVERRIDE_STATUS), dict):
        if presets[NIGHT_OVERRIDE_STATUS].get("availability") != "offline":
            errors.append(
                f"Preset `{NIGHT_OVERRIDE_STATUS}` must have `availability` set to 'offline'."
            )

    seen_titles: dict[str, str] = {}
    for preset_id, entry in presets.items():
        if not isinstance(preset_id, str) or not _PRESET_ID_RE.match(preset_id):
            errors.append(
                f"Preset ID {preset_id!r} must be lowercase snake_case (e.g. 'deep_focus')."
            )
            continue
        if not isinstance(entry, dict):
            errors.append(f"Preset `{preset_id}` must be a JSON object.")
            continue

        for field in ("emoji", "title", "category", "availability", "message"):
            val = entry.get(field)
            if not isinstance(val, str) or not val.strip():
                errors.append(f"Preset `{preset_id}` is missing non-empty string `{field}`.")

        availability = entry.get("availability")
        if isinstance(availability, str) and availability not in VALID_AVAILABILITIES:
            errors.append(
                f"Preset `{preset_id}` has invalid availability {availability!r}; "
                f"expected one of {sorted(VALID_AVAILABILITIES)}."
            )

        tags = entry.get("tags")
        if not isinstance(tags, list) or not (1 <= len(tags) <= MAX_TAGS):
            errors.append(
                f"Preset `{preset_id}` must define `tags` as a list of 1 to {MAX_TAGS} items."
            )
        else:
            for idx, tag in enumerate(tags):
                if not isinstance(tag, str) or not tag.strip():
                    errors.append(
                        f"Preset `{preset_id}` tag at index {idx} must be a non-empty string."
                    )

        color = entry.get("color")
        if color is not None:
            if not isinstance(color, str) or not _HEX_COLOR_RE.match(color):
                errors.append(
                    f"Preset `{preset_id}` color {color!r} must be a 6-digit hex string (e.g. '#58a6ff')."
                )

        project = entry.get("project")
        if project is not None:
            if not isinstance(project, str) or not project.strip():
                errors.append(
                    f"Preset `{preset_id}` optional `project` must be a non-empty string when provided."
                )

        title = entry.get("title")
        if isinstance(title, str) and title.strip():
            norm_title = title.strip().lower()
            if norm_title in seen_titles:
                errors.append(
                    f"Preset `{preset_id}` reuses title {title!r} already used by `{seen_titles[norm_title]}`."
                )
            else:
                seen_titles[norm_title] = preset_id

    return errors


def load_presets(path: Path | str) -> tuple[dict[str, dict[str, Any]], str]:
    """Load and validate the presets catalog, returning `(presets_map, fallback_id)`."""
    catalog = load_json_file(path)
    errors = validate_presets_catalog(catalog)
    if errors:
        raise StatusValidationError(errors)
    fallback_id = catalog.get("default_fallback", DEFAULT_FALLBACK_STATUS)
    return catalog["presets"], fallback_id


def validate_user_status(
    config: Any,
    presets: dict[str, dict[str, Any]],
) -> list[str]:
    """Validate the two-field user configuration (`status` and `until`)."""
    errors: list[str] = []
    if not isinstance(config, dict):
        return ["Status config must be a JSON object with exactly two fields: `status` and `until`."]

    keys = set(config.keys())
    missing = sorted(ALLOWED_USER_KEYS - keys)
    extra = sorted(keys - ALLOWED_USER_KEYS)

    if missing:
        errors.append(f"Missing required field(s): {', '.join(missing)}.")
    if extra:
        errors.append(
            f"Unexpected extra field(s): {', '.join(extra)}. "
            "Only `status` and `until` should be edited; all other details resolve from presets."
        )

    if "status" in config:
        status_val = config["status"]
        if not isinstance(status_val, str):
            errors.append(
                f"`status` must be a string preset ID, got {type(status_val).__name__}."
            )
        elif status_val != status_val.strip():
            errors.append(
                f"`status` must not contain leading or trailing whitespace: {status_val!r}."
            )
        elif is_placeholder_value(status_val):
            errors.append(
                f"`status` still contains a placeholder ({status_val!r}). "
                "Replace it with a valid preset ID from `data/status_presets.json` "
                "(for example: 'working', 'travelling', 'available', 'offline')."
            )
        elif status_val not in presets:
            suggestions = difflib.get_close_matches(status_val.lower(), list(presets.keys()), n=3)
            hint = f" Did you mean: {', '.join(suggestions)}?" if suggestions else ""
            errors.append(
                f"Unknown status preset {status_val!r}.{hint} "
                f"Valid presets: {', '.join(sorted(presets.keys()))}."
            )

    if "until" in config:
        _, until_errors = parse_until_time(config["until"])
        errors.extend(until_errors)

    return errors


def validate_status_meta(
    meta: Any,
    expected_date: date | None = None,
) -> list[str]:
    """Validate the workflow-managed status metadata (`data/status_meta.json`)."""
    errors: list[str] = []
    if not isinstance(meta, dict):
        return ["Status metadata (`data/status_meta.json`) must be a JSON object."]

    raw_date = meta.get("effective_date")
    parsed_date: date | None = None
    try:
        parsed_date = parse_iso_date(raw_date)
    except ValueError as exc:
        errors.append(f"Invalid `effective_date` in status metadata: {exc}")

    tz_val = meta.get("timezone")
    if tz_val is not None and tz_val != TIMEZONE_NAME:
        errors.append(
            f"Unsupported `timezone` {tz_val!r} in status metadata; expected {TIMEZONE_NAME!r}."
        )

    boundary_val = meta.get("day_boundary")
    if boundary_val is not None and boundary_val != DAY_BOUNDARY_STR:
        errors.append(
            f"Unsupported `day_boundary` {boundary_val!r} in status metadata; expected {DAY_BOUNDARY_STR!r}."
        )

    if expected_date is not None and parsed_date is not None and parsed_date != expected_date:
        errors.append(
            f"Metadata `effective_date` ({parsed_date.isoformat()}) does not match "
            f"daily branch date ({expected_date.isoformat()})."
        )

    return errors


def _format_utc(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _format_ist(dt: datetime) -> str:
    return dt.astimezone(IST).isoformat(timespec="seconds")


def _extract_manual_input(config: Any) -> dict[str, Any]:
    if isinstance(config, dict):
        return {
            "status": config.get("status"),
            "until": config.get("until"),
        }
    return {"status": None, "until": None}


def build_night_override(
    presets: dict[str, dict[str, Any]] | None,
    *,
    now: datetime | None = None,
    night_status_id: str = NIGHT_OVERRIDE_STATUS,
    manual_input: dict[str, Any] | None = None,
    effective_date: date | None = None,
    expires_at_ist: datetime | None = None,
    manual_valid: bool = False,
    manual_expired: bool | None = None,
) -> dict[str, Any]:
    """Build Priority 1 scheduled night override payload (`sleeping`, offline availability)."""
    current_ist = ensure_ist(now)
    preset = (presets or {}).get(night_status_id) or _NIGHT_PRESET_EMERGENCY
    resolved_manual = manual_input if manual_input is not None else {"status": None, "until": None}
    window_start = compute_window_start(effective_date) if effective_date is not None else None
    override_end = compute_night_override_end(current_ist)

    return {
        "schema_version": SCHEMA_VERSION,
        "status": night_status_id,
        "emoji": preset["emoji"],
        "title": preset["title"],
        "category": preset["category"],
        "availability": "offline",
        "tags": list(preset["tags"]),
        "message": preset["message"],
        "project": preset.get("project"),
        "color": preset.get("color", DEFAULT_THEME_COLOR),
        "until": NIGHT_END_STR,
        "timezone": TIMEZONE_NAME,
        "expired": False,
        "is_fallback": False,
        "is_override": True,
        "resolution_source": "night_override",
        "manual_input": {
            "status": resolved_manual.get("status"),
            "until": resolved_manual.get("until"),
        },
        "computed": {
            "priority_source": "night_override",
            "night_override_active": True,
            "night_window": NIGHT_WINDOW_STR,
            "night_override_ends_at_ist": _format_ist(override_end),
            "night_override_ends_at_utc": _format_utc(override_end),
            "override_reason": "scheduled_night_hours",
            "effective_date": effective_date.isoformat() if effective_date else None,
            "day_boundary": DAY_BOUNDARY_STR,
            "window_start_ist": _format_ist(window_start) if window_start else None,
            "expires_at_ist": _format_ist(expires_at_ist) if expires_at_ist else None,
            "expires_at_utc": _format_utc(expires_at_ist) if expires_at_ist else None,
            "resolved_at_ist": _format_ist(current_ist),
            "resolved_at_utc": _format_utc(current_ist),
            "manual_valid": manual_valid,
            "manual_expired": manual_expired,
            "expired": False,
            "fallback_reason": None,
            "configured_status": resolved_manual.get("status"),
            "active_status": night_status_id,
            "until_display": f"{NIGHT_END_STR} IST",
        },
    }


def build_offline_fallback(
    presets: dict[str, dict[str, Any]] | None,
    *,
    now: datetime | None = None,
    fallback_id: str = DEFAULT_FALLBACK_STATUS,
    reason: str = "fallback_offline",
    manual_input: dict[str, Any] | None = None,
    effective_date: date | None = None,
    expires_at_ist: datetime | None = None,
    manual_valid: bool = False,
    manual_expired: bool | None = None,
) -> dict[str, Any]:
    """Build Priority 3 safe offline fallback payload."""
    current_ist = ensure_ist(now)
    preset = (presets or {}).get(fallback_id) or _FALLBACK_PRESET_EMERGENCY
    resolved_manual = manual_input if manual_input is not None else {"status": None, "until": None}
    window_start = compute_window_start(effective_date) if effective_date is not None else None

    return {
        "schema_version": SCHEMA_VERSION,
        "status": fallback_id,
        "emoji": preset["emoji"],
        "title": preset["title"],
        "category": preset["category"],
        "availability": preset["availability"],
        "tags": list(preset["tags"]),
        "message": preset["message"],
        "project": preset.get("project"),
        "color": preset.get("color", DEFAULT_THEME_COLOR),
        "until": None,
        "timezone": TIMEZONE_NAME,
        "expired": True,
        "is_fallback": True,
        "is_override": False,
        "resolution_source": "offline_fallback",
        "manual_input": {
            "status": resolved_manual.get("status"),
            "until": resolved_manual.get("until"),
        },
        "computed": {
            "priority_source": "offline_fallback",
            "night_override_active": False,
            "night_window": NIGHT_WINDOW_STR,
            "night_override_ends_at_ist": None,
            "night_override_ends_at_utc": None,
            "override_reason": None,
            "effective_date": effective_date.isoformat() if effective_date else None,
            "day_boundary": DAY_BOUNDARY_STR,
            "window_start_ist": _format_ist(window_start) if window_start else None,
            "expires_at_ist": _format_ist(expires_at_ist) if expires_at_ist else None,
            "expires_at_utc": _format_utc(expires_at_ist) if expires_at_ist else None,
            "resolved_at_ist": _format_ist(current_ist),
            "resolved_at_utc": _format_utc(current_ist),
            "manual_valid": manual_valid,
            "manual_expired": manual_expired,
            "expired": True,
            "fallback_reason": reason,
            "configured_status": resolved_manual.get("status"),
            "active_status": fallback_id,
            "until_display": None,
        },
    }


def resolve_status(
    user_config: Any,
    presets: dict[str, dict[str, Any]] | None,
    effective_date: date | str | None,
    *,
    now: datetime | None = None,
    strict: bool = False,
    fallback_id: str = DEFAULT_FALLBACK_STATUS,
    night_status_id: str = NIGHT_OVERRIDE_STATUS,
) -> dict[str, Any]:
    """Resolve user status configuration into a machine-readable SVG-ready payload.

    Explicit priority:
    1. Scheduled night override (22:00 through 05:59:59 IST -> `sleeping`, offline availability).
    2. Valid, unexpired manually selected status.
    3. Safe offline fallback (`offline`).

    In `strict=True` mode (PR validation), invalid manual configuration or
    invalid/missing `effective_date` always raises `StatusValidationError`, even
    during night hours.
    In `strict=False` mode (runtime/scheduled resolution), Priority 1 applies
    during night hours even if no daily PR was merged or the manual config is
    expired/invalid, and Priority 3 safely falls back to `offline` outside night
    hours.
    """
    current_ist = ensure_ist(now)
    manual_input = _extract_manual_input(user_config)

    parsed_date: date | None = None
    date_errors: list[str] = []
    if effective_date is None:
        date_errors.append("Missing effective status date (`effective_date`).")
    elif isinstance(effective_date, date) and not isinstance(effective_date, datetime):
        parsed_date = effective_date
    else:
        try:
            parsed_date = parse_iso_date(effective_date)
        except ValueError as exc:
            date_errors.append(str(exc))

    config_errors = (
        validate_user_status(user_config, presets)
        if presets is not None
        else ["Preset catalog is unavailable."]
    )
    all_errors = config_errors + date_errors

    if strict and all_errors:
        raise StatusValidationError(all_errors)

    manual_valid = not all_errors
    expires_at_ist: datetime | None = None
    manual_expired: bool | None = None

    if manual_valid:
        assert parsed_date is not None
        expires_at_ist = compute_expiry(parsed_date, user_config["until"])
        manual_expired = current_ist >= expires_at_ist

    # Priority 1: Scheduled night override (22:00:00 through 05:59:59 IST)
    if is_night_window(current_ist):
        return build_night_override(
            presets,
            now=current_ist,
            night_status_id=night_status_id,
            manual_input=manual_input,
            effective_date=parsed_date,
            expires_at_ist=expires_at_ist,
            manual_valid=manual_valid,
            manual_expired=manual_expired,
        )

    # Priority 2: Valid, unexpired manually selected status
    if manual_valid and not manual_expired:
        assert presets is not None and parsed_date is not None and expires_at_ist is not None
        status_id: str = user_config["status"]
        until_str: str = user_config["until"]
        window_start_ist = compute_window_start(parsed_date)
        preset = presets[status_id]
        return {
            "schema_version": SCHEMA_VERSION,
            "status": status_id,
            "emoji": preset["emoji"],
            "title": preset["title"],
            "category": preset["category"],
            "availability": preset["availability"],
            "tags": list(preset["tags"]),
            "message": preset["message"],
            "project": preset.get("project"),
            "color": preset.get("color", DEFAULT_THEME_COLOR),
            "until": until_str,
            "timezone": TIMEZONE_NAME,
            "expired": False,
            "is_fallback": False,
            "is_override": False,
            "resolution_source": "manual",
            "manual_input": {
                "status": status_id,
                "until": until_str,
            },
            "computed": {
                "priority_source": "manual",
                "night_override_active": False,
                "night_window": NIGHT_WINDOW_STR,
                "night_override_ends_at_ist": None,
                "night_override_ends_at_utc": None,
                "override_reason": None,
                "effective_date": parsed_date.isoformat(),
                "day_boundary": DAY_BOUNDARY_STR,
                "window_start_ist": _format_ist(window_start_ist),
                "expires_at_ist": _format_ist(expires_at_ist),
                "expires_at_utc": _format_utc(expires_at_ist),
                "resolved_at_ist": _format_ist(current_ist),
                "resolved_at_utc": _format_utc(current_ist),
                "manual_valid": True,
                "manual_expired": False,
                "expired": False,
                "fallback_reason": None,
                "configured_status": status_id,
                "active_status": status_id,
                "until_display": f"{until_str} IST",
            },
        }

    # Priority 3: Safe offline fallback
    if manual_valid and manual_expired:
        reason = "status_expired"
    elif presets is None:
        reason = "invalid_presets"
    elif isinstance(user_config, dict) and (
        is_placeholder_value(user_config.get("status"))
        or is_placeholder_value(user_config.get("until"))
    ):
        reason = "placeholder_config"
    elif date_errors and not config_errors:
        reason = "missing_effective_date"
    else:
        reason = "invalid_config"

    return build_offline_fallback(
        presets,
        now=current_ist,
        fallback_id=fallback_id,
        reason=reason,
        manual_input=manual_input,
        effective_date=parsed_date,
        expires_at_ist=expires_at_ist,
        manual_valid=manual_valid,
        manual_expired=manual_expired,
    )


def validate_resolved_status(resolved: Any) -> list[str]:
    """Validate the resolved output JSON schema for downstream SVG consumers."""
    errors: list[str] = []
    if not isinstance(resolved, dict):
        return ["Resolved status must be a JSON object."]

    required_top = (
        "schema_version",
        "status",
        "emoji",
        "title",
        "category",
        "availability",
        "tags",
        "message",
        "project",
        "color",
        "until",
        "timezone",
        "expired",
        "is_fallback",
        "is_override",
        "resolution_source",
        "manual_input",
        "computed",
    )
    for key in required_top:
        if key not in resolved:
            errors.append(f"Resolved output missing required key `{key}`.")

    if errors:
        return errors

    if resolved["timezone"] != TIMEZONE_NAME:
        errors.append(f"Resolved timezone must be {TIMEZONE_NAME!r}.")
    if not isinstance(resolved["expired"], bool):
        errors.append("`expired` must be a boolean.")
    if not isinstance(resolved["is_fallback"], bool):
        errors.append("`is_fallback` must be a boolean.")
    if not isinstance(resolved["is_override"], bool):
        errors.append("`is_override` must be a boolean.")
    if resolved["resolution_source"] not in VALID_RESOLUTION_SOURCES:
        errors.append(
            f"Invalid `resolution_source` {resolved['resolution_source']!r}; "
            f"expected one of {sorted(VALID_RESOLUTION_SOURCES)}."
        )
    if resolved["project"] is not None and not isinstance(resolved["project"], str):
        errors.append("`project` must be a string or null.")
    if resolved["availability"] not in VALID_AVAILABILITIES:
        errors.append(f"Invalid resolved availability {resolved['availability']!r}.")
    if not isinstance(resolved["tags"], list) or not (1 <= len(resolved["tags"]) <= MAX_TAGS):
        errors.append(f"Resolved `tags` must contain 1 to {MAX_TAGS} items.")
    if not isinstance(resolved["manual_input"], dict) or set(resolved["manual_input"].keys()) != ALLOWED_USER_KEYS:
        errors.append("`manual_input` must contain strictly `status` and `until`.")

    computed = resolved["computed"]
    if not isinstance(computed, dict):
        errors.append("`computed` must be a JSON object.")
    else:
        for key in (
            "priority_source",
            "night_override_active",
            "night_window",
            "night_override_ends_at_ist",
            "night_override_ends_at_utc",
            "override_reason",
            "effective_date",
            "day_boundary",
            "window_start_ist",
            "expires_at_ist",
            "expires_at_utc",
            "resolved_at_ist",
            "resolved_at_utc",
            "manual_valid",
            "manual_expired",
            "expired",
            "fallback_reason",
            "configured_status",
            "active_status",
            "until_display",
        ):
            if key not in computed:
                errors.append(f"`computed` missing required key `{key}`.")

    return errors


def _semantic_snapshot(resolved: dict[str, Any]) -> dict[str, Any]:
    """Strip volatile resolution timestamps for change detection."""
    snap = copy.deepcopy(resolved)
    computed = snap.get("computed")
    if isinstance(computed, dict):
        computed.pop("resolved_at_ist", None)
        computed.pop("resolved_at_utc", None)
    return snap


def write_resolved_if_changed(
    resolved: dict[str, Any],
    out_path: Path | str,
    *,
    force: bool = False,
) -> bool:
    """Write resolved JSON only when semantic fields changed or file is missing."""
    target = Path(out_path)
    if not force and target.exists():
        try:
            existing = load_json_file(target)
            if isinstance(existing, dict) and _semantic_snapshot(existing) == _semantic_snapshot(resolved):
                return False
        except (OSError, ValueError):
            pass

    target.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(resolved, indent=2, ensure_ascii=False) + "\n"
    target.write_text(rendered, encoding="utf-8")
    return True
