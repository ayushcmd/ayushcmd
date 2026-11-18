#!/usr/bin/env python3
"""Automated unit and integration tests for the Ayush Live Status system.

Covers:
- Central preset catalog integrity and every individual preset
- Valid and invalid status selection
- Valid and invalid 24-hour HH:MM values
- Preset resolution and manual vs computed separation
- Timezone handling (Asia/Kolkata & UTC conversion, naive datetime rejection)
- Before/at/after expiration and non-rolling expiration across days
- Midnight crossover (00:00-06:59) and 07:00 AM IST daily boundary
- Safe offline fallback on expiry, placeholders, missing/invalid files
- Placeholder rejection in strict validation mode
- JSON schema validation (strictly two user fields, metadata, resolved output)
- Idempotent file writing and CLI execution (`scripts/resolve_status.py`)
- Idempotent daily Draft PR preparation and stale PR safety (`scripts/prepare_daily_status_pr.py`)
- GitHub Actions workflow YAML validity, schedules, and minimal permissions
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import prepare_daily_status_pr  # noqa: E402
import render_status_svg  # noqa: E402
import resolve_status as resolve_cli  # noqa: E402
import status_engine as se  # noqa: E402


EXPECTED_PRESETS = {
    "available",
    "offline",
    "exam",
    "travelling",
    "working",
    "sleeping",
}


class TestPresetCatalog(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.presets_path = REPO_ROOT / "data" / "status_presets.json"
        cls.catalog = se.load_json_file(cls.presets_path)
        cls.presets, cls.fallback_id = se.load_presets(cls.presets_path)

    def test_catalog_has_exact_six_presets_and_passes_validation(self):
        self.assertEqual(se.validate_presets_catalog(self.catalog), [])
        self.assertEqual(self.fallback_id, "offline")
        self.assertEqual(set(self.presets.keys()), EXPECTED_PRESETS)
        self.assertEqual(len(self.presets), 6)
        self.assertEqual(self.presets["sleeping"]["availability"], "offline")
        self.assertIsInstance(self.presets["working"].get("project"), str)
        self.assertTrue(self.presets["working"]["project"].strip())

    def test_every_preset_has_required_fields_and_resolves_cleanly(self):
        seen_titles = set()
        effective = date(2026, 11, 18)
        active_now = datetime(2026, 11, 18, 14, 0, tzinfo=se.IST)
        expired_now = datetime(2026, 11, 18, 21, 1, tzinfo=se.IST)

        for preset_id, preset in self.presets.items():
            with self.subTest(preset=preset_id):
                self.assertIsInstance(preset["emoji"], str)
                self.assertTrue(preset["emoji"].strip())
                self.assertIsInstance(preset["title"], str)
                self.assertTrue(preset["title"].strip())
                self.assertNotIn(preset["title"].lower(), seen_titles)
                seen_titles.add(preset["title"].lower())
                self.assertIn(preset["availability"], se.VALID_AVAILABILITIES)
                self.assertIsInstance(preset["category"], str)
                self.assertIsInstance(preset["message"], str)
                self.assertIsInstance(preset["tags"], list)
                self.assertGreaterEqual(len(preset["tags"]), 1)
                self.assertLessEqual(len(preset["tags"]), 2)
                self.assertRegex(preset["color"], r"^#[0-9a-fA-F]{6}$")

                # Active resolution (daytime, before 21:00)
                resolved = se.resolve_status(
                    {"status": preset_id, "until": "21:00"},
                    self.presets,
                    effective,
                    now=active_now,
                    strict=True,
                )
                self.assertEqual(se.validate_resolved_status(resolved), [])
                self.assertEqual(resolved["status"], preset_id)
                self.assertEqual(resolved["emoji"], preset["emoji"])
                self.assertEqual(resolved["title"], preset["title"])
                self.assertEqual(resolved["category"], preset["category"])
                self.assertEqual(resolved["availability"], preset["availability"])
                self.assertEqual(resolved["tags"], preset["tags"])
                self.assertEqual(resolved["message"], preset["message"])
                self.assertEqual(resolved["project"], preset.get("project"))
                self.assertEqual(resolved["color"], preset["color"])
                self.assertEqual(resolved["until"], "21:00")
                self.assertFalse(resolved["expired"])
                self.assertFalse(resolved["is_fallback"])
                self.assertFalse(resolved["is_override"])
                self.assertEqual(resolved["resolution_source"], "manual")
                self.assertEqual(resolved["manual_input"], {"status": preset_id, "until": "21:00"})

                # Expired daytime resolution must safely fall back to offline
                expired = se.resolve_status(
                    {"status": preset_id, "until": "21:00"},
                    self.presets,
                    effective,
                    now=expired_now,
                    strict=True,
                )
                self.assertEqual(se.validate_resolved_status(expired), [])
                self.assertTrue(expired["expired"])
                self.assertTrue(expired["is_fallback"])
                self.assertFalse(expired["is_override"])
                self.assertEqual(expired["resolution_source"], "offline_fallback")
                self.assertEqual(expired["status"], "offline")
                self.assertEqual(expired["availability"], "offline")
                self.assertIsNone(expired["until"])
                self.assertEqual(expired["computed"]["configured_status"], preset_id)
                self.assertEqual(expired["computed"]["active_status"], "offline")

    def test_working_preset_includes_predefined_project_without_extra_manual_fields(self):
        resolved = se.resolve_status(
            {"status": "working", "until": "21:00"},
            self.presets,
            date(2026, 11, 18),
            now=datetime(2026, 11, 18, 15, 0, tzinfo=se.IST),
            strict=True,
        )
        self.assertEqual(resolved["status"], "working")
        self.assertEqual(resolved["project"], self.presets["working"]["project"])
        self.assertEqual(set(resolved["manual_input"].keys()), {"status", "until"})

    def test_catalog_validator_rejects_broken_presets(self):
        bad_catalog = {
            "default_fallback": "missing",
            "presets": {
                "Bad-ID": {
                    "emoji": "",
                    "title": "A",
                    "category": "coding",
                    "availability": "super_online",
                    "message": "hi",
                    "tags": ["One", "Two", "Three"],
                    "color": "blue",
                },
                "dup_one": {
                    "emoji": "💻",
                    "title": "Same Title",
                    "category": "coding",
                    "availability": "focused",
                    "message": "m1",
                    "tags": ["Tag1"],
                    "color": "#58a6ff",
                },
                "dup_two": {
                    "emoji": "🐛",
                    "title": "same title",
                    "category": "coding",
                    "availability": "focused",
                    "message": "m2",
                    "tags": ["Tag1", "Tag2", "Tag3"],
                    "color": "#zzzzzz",
                },
            },
        }
        errors = se.validate_presets_catalog(bad_catalog)
        joined = "\n".join(errors)
        self.assertIn("default_fallback", joined)
        self.assertIn("offline", joined)
        self.assertIn("snake_case", joined)
        self.assertIn("reuses title", joined)
        self.assertIn("1 to 2 items", joined)
        self.assertIn("6-digit hex", joined)


class TestUserValidationAndSchema(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.presets, _ = se.load_presets(REPO_ROOT / "data" / "status_presets.json")

    def test_valid_two_field_selection(self):
        errors = se.validate_user_status(
            {"status": "travelling", "until": "22:00"},
            self.presets,
        )
        self.assertEqual(errors, [])

    def test_invalid_status_selection_and_suggestion(self):
        errors = se.validate_user_status(
            {"status": "traveling", "until": "22:00"},
            self.presets,
        )
        self.assertEqual(len(errors), 1)
        self.assertIn("Unknown status preset 'traveling'", errors[0])
        self.assertIn("travelling", errors[0])

        for bad_status in (123, None, True, "CODING", " coding ", "nonexistent_mode"):
            with self.subTest(bad_status=bad_status):
                errs = se.validate_user_status(
                    {"status": bad_status, "until": "22:00"},
                    self.presets,
                )
                self.assertTrue(errs)

    def test_strict_two_field_schema_rejects_extra_or_missing_keys(self):
        missing_until = se.validate_user_status({"status": "working"}, self.presets)
        self.assertTrue(any("Missing required field" in e and "until" in e for e in missing_until))

        missing_status = se.validate_user_status({"until": "21:00"}, self.presets)
        self.assertTrue(any("Missing required field" in e and "status" in e for e in missing_status))

        extra_fields = se.validate_user_status(
            {
                "status": "working",
                "until": "21:00",
                "emoji": "💻",
                "availability": "available",
                "project": "Custom",
                "active_window": "09:00-21:00",
                "tags": ["Extra"],
            },
            self.presets,
        )
        self.assertTrue(any("Unexpected extra field" in e for e in extra_fields))

        non_dict = se.validate_user_status(["working", "21:00"], self.presets)
        self.assertTrue(non_dict)

    def test_valid_hhmm_values(self):
        for valid in ("00:00", "00:01", "02:00", "06:59", "07:00", "07:01", "12:30", "22:00", "23:59"):
            with self.subTest(until=valid):
                parsed, errors = se.parse_until_time(valid)
                self.assertEqual(errors, [])
                self.assertIsNotNone(parsed)

    def test_invalid_hhmm_values(self):
        invalid_cases = [
            "9:00",
            "24:00",
            "25:00",
            "12:60",
            "-01:00",
            "22:5",
            "10:00 PM",
            "22:00:00",
            " 22:00 ",
            "",
            "noon",
            2200,
            None,
            False,
        ]
        for bad in invalid_cases:
            with self.subTest(until=bad):
                parsed, errors = se.parse_until_time(bad)
                self.assertIsNone(parsed)
                self.assertTrue(errors)

    def test_placeholder_rejection(self):
        placeholder_pairs = [
            (se.PLACEHOLDER_STATUS, se.PLACEHOLDER_UNTIL),
            ("__PICK_STATUS__", "__TIME__"),
            ("<status>", "<HH:MM>"),
            ("{{status}}", "{{until}}"),
            ("TODO", "TBD"),
            ("", ""),
        ]
        for status_val, until_val in placeholder_pairs:
            with self.subTest(status=status_val, until=until_val):
                errors = se.validate_user_status(
                    {"status": status_val, "until": until_val},
                    self.presets,
                )
                self.assertGreaterEqual(len(errors), 2)
                self.assertTrue(any("placeholder" in e.lower() for e in errors))

        with self.assertRaises(se.StatusValidationError):
            se.resolve_status(
                {"status": se.PLACEHOLDER_STATUS, "until": se.PLACEHOLDER_UNTIL},
                self.presets,
                date(2026, 11, 18),
                now=datetime(2026, 11, 18, 10, 0, tzinfo=se.IST),
                strict=True,
            )

    def test_status_meta_validation(self):
        valid_meta = {
            "effective_date": "2026-11-18",
            "timezone": "Asia/Kolkata",
            "day_boundary": "07:00",
        }
        self.assertEqual(se.validate_status_meta(valid_meta, expected_date=date(2026, 11, 18)), [])

        mismatch = se.validate_status_meta(valid_meta, expected_date=date(2026, 11, 19))
        self.assertTrue(any("does not match" in e for e in mismatch))

        bad_meta = {
            "effective_date": "18-11-2026",
            "timezone": "UTC",
            "day_boundary": "00:00",
        }
        errs = se.validate_status_meta(bad_meta)
        self.assertEqual(len(errs), 3)


class TestTimezoneAndExpiration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.presets, _ = se.load_presets(REPO_ROOT / "data" / "status_presets.json")

    def test_timezone_conversion_and_naive_rejection(self):
        # 2026-11-18 15:29 UTC == 2026-11-18 20:59 IST (1 min before 21:00 IST)
        utc_before = datetime(2026, 11, 18, 15, 29, tzinfo=timezone.utc)
        res_before = se.resolve_status(
            {"status": "travelling", "until": "21:00"},
            self.presets,
            date(2026, 11, 18),
            now=utc_before,
            strict=True,
        )
        self.assertFalse(res_before["expired"])
        self.assertEqual(res_before["resolution_source"], "manual")
        self.assertEqual(res_before["timezone"], "Asia/Kolkata")
        self.assertEqual(res_before["computed"]["expires_at_ist"], "2026-11-18T21:00:00+05:30")
        self.assertEqual(res_before["computed"]["expires_at_utc"], "2026-11-18T15:30:00Z")
        self.assertEqual(res_before["computed"]["resolved_at_ist"], "2026-11-18T20:59:00+05:30")

        # 2026-11-18 15:30 UTC == 2026-11-18 21:00 IST (exact daytime expiry)
        utc_exact = datetime(2026, 11, 18, 15, 30, tzinfo=timezone.utc)
        res_exact = se.resolve_status(
            {"status": "travelling", "until": "21:00"},
            self.presets,
            date(2026, 11, 18),
            now=utc_exact,
            strict=True,
        )
        self.assertTrue(res_exact["expired"])
        self.assertEqual(res_exact["status"], "offline")
        self.assertEqual(res_exact["resolution_source"], "offline_fallback")
        self.assertEqual(res_exact["computed"]["fallback_reason"], "status_expired")

        # Naive datetimes must be rejected to prevent timezone ambiguity
        with self.assertRaises(ValueError):
            se.ensure_ist(datetime(2026, 11, 18, 12, 0))
        with self.assertRaises(ValueError):
            se.parse_iso_datetime("2026-11-18T12:00:00")

    def test_before_at_after_expiration_and_no_next_day_rollover(self):
        cfg = {"status": "working", "until": "21:00"}
        eff = date(2026, 11, 18)

        # 1 second before expiry -> active
        r_before = se.resolve_status(
            cfg, self.presets, eff, now=datetime(2026, 11, 18, 20, 59, 59, tzinfo=se.IST)
        )
        self.assertFalse(r_before["expired"])
        self.assertEqual(r_before["status"], "working")

        # Exact expiry -> expired -> offline
        r_at = se.resolve_status(
            cfg, self.presets, eff, now=datetime(2026, 11, 18, 21, 0, 0, tzinfo=se.IST)
        )
        self.assertTrue(r_at["expired"])
        self.assertEqual(r_at["status"], "offline")

        # Next day morning (10:00 IST on Nov 19) -> MUST STAY EXPIRED, never roll forward!
        r_next_day = se.resolve_status(
            cfg, self.presets, eff, now=datetime(2026, 11, 19, 10, 0, tzinfo=se.IST)
        )
        self.assertTrue(r_next_day["expired"])
        self.assertEqual(r_next_day["status"], "offline")
        self.assertEqual(r_next_day["computed"]["expires_at_ist"], "2026-11-18T21:00:00+05:30")

    def test_midnight_crossover_computation_and_morning_restoration(self):
        eff = date(2026, 11, 18)

        # 02:00 means 2 AM on 2026-11-19
        self.assertEqual(
            se.compute_expiry(eff, "02:00"),
            datetime(2026, 11, 19, 2, 0, tzinfo=se.IST),
        )
        # 00:00 means midnight entering 2026-11-19
        self.assertEqual(
            se.compute_expiry(eff, "00:00"),
            datetime(2026, 11, 19, 0, 0, tzinfo=se.IST),
        )
        # 06:59 means 6:59 AM on 2026-11-19
        self.assertEqual(
            se.compute_expiry(eff, "06:59"),
            datetime(2026, 11, 19, 6, 59, tzinfo=se.IST),
        )

        # Status spanning past 06:00 (until 06:30 next morning)
        cfg_span = {"status": "travelling", "until": "06:30"}
        # At 06:00 IST after night override ends, manual status is restored because 06:00 < 06:30
        r_restored = se.resolve_status(
            cfg_span, self.presets, eff, now=datetime(2026, 11, 19, 6, 0, tzinfo=se.IST)
        )
        self.assertFalse(r_restored["expired"])
        self.assertEqual(r_restored["status"], "travelling")
        self.assertEqual(r_restored["resolution_source"], "manual")

        # At 06:30 IST it expires to offline
        r_expired = se.resolve_status(
            cfg_span, self.presets, eff, now=datetime(2026, 11, 19, 6, 30, tzinfo=se.IST)
        )
        self.assertTrue(r_expired["expired"])
        self.assertEqual(r_expired["status"], "offline")
        self.assertEqual(r_expired["resolution_source"], "offline_fallback")

    def test_7am_daily_boundary(self):
        eff = date(2026, 11, 18)

        # 07:00 expires at tomorrow's 07:00 AM reset (full 24h window)
        self.assertEqual(
            se.compute_expiry(eff, "07:00"),
            datetime(2026, 11, 19, 7, 0, tzinfo=se.IST),
        )
        # 07:01 expires on the same effective date (1 min after 07:00 boundary)
        self.assertEqual(
            se.compute_expiry(eff, "07:01"),
            datetime(2026, 11, 18, 7, 1, tzinfo=se.IST),
        )

        # Active until 06:59:59 next morning, expired at 07:00:00
        cfg = {"status": "sleeping", "until": "07:00"}
        r_before = se.resolve_status(
            cfg, self.presets, eff, now=datetime(2026, 11, 19, 6, 59, 59, tzinfo=se.IST)
        )
        self.assertFalse(r_before["expired"])
        self.assertEqual(r_before["status"], "sleeping")

        r_reset = se.resolve_status(
            cfg, self.presets, eff, now=datetime(2026, 11, 19, 7, 0, 0, tzinfo=se.IST)
        )
        self.assertTrue(r_reset["expired"])
        self.assertEqual(r_reset["status"], "offline")

        # status_date_for_datetime flips precisely at 07:00 IST
        self.assertEqual(
            se.status_date_for_datetime(datetime(2026, 11, 19, 6, 59, 59, tzinfo=se.IST)),
            date(2026, 11, 18),
        )
        self.assertEqual(
            se.status_date_for_datetime(datetime(2026, 11, 19, 7, 0, 0, tzinfo=se.IST)),
            date(2026, 11, 19),
        )

    def test_safe_offline_fallbacks_in_non_strict_mode(self):
        now = datetime(2026, 11, 18, 12, 0, tzinfo=se.IST)

        # Placeholder config
        r_placeholder = se.resolve_status(
            {"status": se.PLACEHOLDER_STATUS, "until": se.PLACEHOLDER_UNTIL},
            self.presets,
            date(2026, 11, 18),
            now=now,
            strict=False,
        )
        self.assertTrue(r_placeholder["expired"])
        self.assertTrue(r_placeholder["is_fallback"])
        self.assertEqual(r_placeholder["status"], "offline")
        self.assertEqual(r_placeholder["computed"]["fallback_reason"], "placeholder_config")

        # Unknown preset
        r_unknown = se.resolve_status(
            {"status": "mystery", "until": "22:00"},
            self.presets,
            date(2026, 11, 18),
            now=now,
            strict=False,
        )
        self.assertEqual(r_unknown["status"], "offline")
        self.assertEqual(r_unknown["computed"]["fallback_reason"], "invalid_config")

        # Missing effective_date (never rolls forward!)
        r_no_date = se.resolve_status(
            {"status": "available", "until": "23:59"},
            self.presets,
            None,
            now=now,
            strict=False,
        )
        self.assertEqual(r_no_date["status"], "offline")
        self.assertEqual(r_no_date["availability"], "offline")
        self.assertEqual(r_no_date["computed"]["fallback_reason"], "missing_effective_date")


class TestNightOverrideAndPriority(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.presets, _ = se.load_presets(REPO_ROOT / "data" / "status_presets.json")

    def test_night_window_exact_boundaries(self):
        # Test exact requested timestamps: 21:59, 22:00, 23:59, 00:00, 05:59, 06:00, 07:00 IST
        # Using a manual status valid through 07:00 so restoration at 06:00 and expiry at 07:00 are both proven.
        cfg = {"status": "travelling", "until": "07:00"}
        eff = date(2026, 11, 18)

        cases = [
            # (timestamp IST, expected_status, expected_avail, expected_source, expected_manual_expired)
            (datetime(2026, 11, 18, 21, 59, 0, tzinfo=se.IST), "travelling", "limited", "manual", False),
            (datetime(2026, 11, 18, 21, 59, 59, tzinfo=se.IST), "travelling", "limited", "manual", False),
            (datetime(2026, 11, 18, 22, 0, 0, tzinfo=se.IST), "sleeping", "offline", "night_override", False),
            (datetime(2026, 11, 18, 23, 59, 0, tzinfo=se.IST), "sleeping", "offline", "night_override", False),
            (datetime(2026, 11, 18, 23, 59, 59, tzinfo=se.IST), "sleeping", "offline", "night_override", False),
            (datetime(2026, 11, 19, 0, 0, 0, tzinfo=se.IST), "sleeping", "offline", "night_override", False),
            (datetime(2026, 11, 19, 5, 59, 0, tzinfo=se.IST), "sleeping", "offline", "night_override", False),
            (datetime(2026, 11, 19, 5, 59, 59, tzinfo=se.IST), "sleeping", "offline", "night_override", False),
            (datetime(2026, 11, 19, 6, 0, 0, tzinfo=se.IST), "travelling", "limited", "manual", False),
            (datetime(2026, 11, 19, 7, 0, 0, tzinfo=se.IST), "offline", "offline", "offline_fallback", True),
        ]

        for ts, exp_status, exp_avail, exp_source, exp_manual_expired in cases:
            with self.subTest(now=ts.isoformat()):
                resolved = se.resolve_status(cfg, self.presets, eff, now=ts, strict=True)
                self.assertEqual(se.validate_resolved_status(resolved), [])
                self.assertEqual(resolved["status"], exp_status)
                self.assertEqual(resolved["availability"], exp_avail)
                self.assertEqual(resolved["resolution_source"], exp_source)
                self.assertEqual(resolved["computed"]["priority_source"], exp_source)
                self.assertEqual(resolved["computed"]["active_status"], exp_status)
                # Underlying manual status is always preserved
                self.assertEqual(resolved["manual_input"], {"status": "travelling", "until": "07:00"})
                self.assertEqual(resolved["computed"]["configured_status"], "travelling")
                self.assertEqual(resolved["computed"]["manual_expired"], exp_manual_expired)
                self.assertEqual(resolved["is_override"], exp_source == "night_override")
                self.assertEqual(resolved["computed"]["night_override_active"], exp_source == "night_override")

    def test_night_override_when_manual_status_expires_overnight(self):
        # Manual status expires at 02:00 IST overnight
        cfg = {"status": "working", "until": "02:00"}
        eff = date(2026, 11, 18)

        # 21:59 -> active manual working (with predefined project)
        r_2159 = se.resolve_status(cfg, self.presets, eff, now=datetime(2026, 11, 18, 21, 59, tzinfo=se.IST))
        self.assertEqual(r_2159["status"], "working")
        self.assertEqual(r_2159["project"], self.presets["working"]["project"])

        # 22:00 -> night override sleeping (underlying manual still unexpired)
        r_2200 = se.resolve_status(cfg, self.presets, eff, now=datetime(2026, 11, 18, 22, 0, tzinfo=se.IST))
        self.assertEqual(r_2200["status"], "sleeping")
        self.assertEqual(r_2200["availability"], "offline")
        self.assertEqual(r_2200["manual_input"], {"status": "working", "until": "02:00"})
        self.assertEqual(r_2200["computed"]["configured_status"], "working")
        self.assertFalse(r_2200["computed"]["manual_expired"])

        # 03:00 -> night override sleeping (underlying manual expired at 02:00)
        r_0300 = se.resolve_status(cfg, self.presets, eff, now=datetime(2026, 11, 19, 3, 0, tzinfo=se.IST))
        self.assertEqual(r_0300["status"], "sleeping")
        self.assertEqual(r_0300["availability"], "offline")
        self.assertTrue(r_0300["computed"]["manual_expired"])

        # 06:00 -> night override ends; because manual expired overnight, must fall back to offline
        r_0600 = se.resolve_status(cfg, self.presets, eff, now=datetime(2026, 11, 19, 6, 0, tzinfo=se.IST))
        self.assertEqual(r_0600["status"], "offline")
        self.assertEqual(r_0600["availability"], "offline")
        self.assertEqual(r_0600["resolution_source"], "offline_fallback")
        self.assertTrue(r_0600["expired"])
        self.assertTrue(r_0600["is_fallback"])
        self.assertEqual(r_0600["computed"]["fallback_reason"], "status_expired")
        self.assertEqual(r_0600["manual_input"], {"status": "working", "until": "02:00"})

    def test_night_override_works_when_daily_pr_was_never_merged(self):
        # Stale effective_date from 5 days ago
        stale_cfg = {"status": "working", "until": "21:00"}
        stale_date = date(2026, 11, 13)

        r_day = se.resolve_status(stale_cfg, self.presets, stale_date, now=datetime(2026, 11, 18, 15, 0, tzinfo=se.IST))
        self.assertEqual(r_day["status"], "offline")
        self.assertEqual(r_day["resolution_source"], "offline_fallback")

        # Even without a daily PR merge, 22:00-05:59:59 IST resolves to sleeping
        for ts in (
            datetime(2026, 11, 18, 22, 0, tzinfo=se.IST),
            datetime(2026, 11, 19, 0, 0, tzinfo=se.IST),
            datetime(2026, 11, 19, 5, 59, 59, tzinfo=se.IST),
        ):
            r_night = se.resolve_status(stale_cfg, self.presets, stale_date, now=ts, strict=False)
            self.assertEqual(r_night["status"], "sleeping")
            self.assertEqual(r_night["availability"], "offline")
            self.assertEqual(r_night["resolution_source"], "night_override")
            self.assertEqual(r_night["computed"]["configured_status"], "working")

        # At 06:00 IST -> falls back to offline
        r_morning = se.resolve_status(stale_cfg, self.presets, stale_date, now=datetime(2026, 11, 19, 6, 0, tzinfo=se.IST))
        self.assertEqual(r_morning["status"], "offline")
        self.assertEqual(r_morning["resolution_source"], "offline_fallback")

        # Even if status.json is placeholder/missing in non-strict mode, night override still applies
        r_placeholder_night = se.resolve_status(
            {"status": se.PLACEHOLDER_STATUS, "until": se.PLACEHOLDER_UNTIL},
            self.presets,
            None,
            now=datetime(2026, 11, 18, 23, 0, tzinfo=se.IST),
            strict=False,
        )
        self.assertEqual(r_placeholder_night["status"], "sleeping")
        self.assertEqual(r_placeholder_night["availability"], "offline")
        self.assertEqual(r_placeholder_night["resolution_source"], "night_override")

    def test_night_override_when_selected_status_is_already_offline(self):
        cfg = {"status": "offline", "until": "21:00"}
        eff = date(2026, 11, 18)

        r_night = se.resolve_status(
            cfg, self.presets, eff, now=datetime(2026, 11, 18, 22, 30, tzinfo=se.IST), strict=True
        )
        self.assertEqual(r_night["status"], "sleeping")
        self.assertEqual(r_night["availability"], "offline")
        self.assertEqual(r_night["resolution_source"], "night_override")
        self.assertEqual(r_night["manual_input"], {"status": "offline", "until": "21:00"})
        self.assertEqual(r_night["computed"]["configured_status"], "offline")
        self.assertEqual(r_night["computed"]["active_status"], "sleeping")

        # At 06:00 IST -> offline fallback (since 21:00 expired)
        r_0600 = se.resolve_status(
            cfg, self.presets, eff, now=datetime(2026, 11, 19, 6, 0, tzinfo=se.IST), strict=True
        )
        self.assertEqual(r_0600["status"], "offline")
        self.assertEqual(r_0600["resolution_source"], "offline_fallback")

    def test_strict_validation_still_rejects_invalid_config_during_night_hours(self):
        night_ts = datetime(2026, 11, 18, 23, 30, tzinfo=se.IST)
        with self.assertRaises(se.StatusValidationError):
            se.resolve_status(
                {"status": se.PLACEHOLDER_STATUS, "until": se.PLACEHOLDER_UNTIL},
                self.presets,
                date(2026, 11, 18),
                now=night_ts,
                strict=True,
            )

    def test_night_override_does_not_modify_manual_status_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            status_path = tmp_dir / "status.json"
            meta_path = tmp_dir / "status_meta.json"
            out_path = tmp_dir / "status_resolved.json"

            original_status_text = json.dumps({"status": "working", "until": "21:00"}, indent=2) + "\n"
            status_path.write_text(original_status_text, encoding="utf-8")
            meta_path.write_text(
                json.dumps({"effective_date": "2026-11-18", "timezone": "Asia/Kolkata"}),
                encoding="utf-8",
            )

            rc = resolve_cli.main([
                "--status-file", str(status_path),
                "--meta-file", str(meta_path),
                "--out-file", str(out_path),
                "--now", "2026-11-18T22:30:00+05:30",
            ])
            self.assertEqual(rc, 0)
            # data/status.json must remain byte-for-byte identical
            self.assertEqual(status_path.read_text(encoding="utf-8"), original_status_text)
            resolved = json.loads(out_path.read_text(encoding="utf-8"))
            self.assertEqual(resolved["status"], "sleeping")
            self.assertEqual(resolved["availability"], "offline")
            self.assertEqual(resolved["manual_input"], {"status": "working", "until": "21:00"})


class TestCLIAndIdempotentOutput(unittest.TestCase):
    def test_idempotent_write_skips_timestamp_only_changes(self):
        presets, _ = se.load_presets(REPO_ROOT / "data" / "status_presets.json")
        cfg = {"status": "working", "until": "21:00"}
        eff = date(2026, 11, 18)

        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "status_resolved.json"
            r1 = se.resolve_status(cfg, presets, eff, now=datetime(2026, 11, 18, 10, 0, tzinfo=se.IST))
            r2 = se.resolve_status(cfg, presets, eff, now=datetime(2026, 11, 18, 11, 30, tzinfo=se.IST))
            r3 = se.resolve_status(cfg, presets, eff, now=datetime(2026, 11, 18, 21, 5, tzinfo=se.IST))

            self.assertTrue(se.write_resolved_if_changed(r1, out_path))
            first_text = out_path.read_text(encoding="utf-8")

            # Only resolved_at changed -> should NOT rewrite
            self.assertFalse(se.write_resolved_if_changed(r2, out_path))
            self.assertEqual(out_path.read_text(encoding="utf-8"), first_text)

            # Status expired -> MUST rewrite
            self.assertTrue(se.write_resolved_if_changed(r3, out_path))
            saved = json.loads(out_path.read_text(encoding="utf-8"))
            self.assertTrue(saved["expired"])
            self.assertEqual(saved["status"], "offline")

    def test_cli_strict_passes_and_rejects_placeholders(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            status_path = tmp_dir / "status.json"
            meta_path = tmp_dir / "status_meta.json"
            out_path = tmp_dir / "status_resolved.json"

            status_path.write_text(
                json.dumps({"status": "travelling", "until": "22:00"}),
                encoding="utf-8",
            )
            meta_path.write_text(
                json.dumps({"effective_date": "2026-11-18", "timezone": "Asia/Kolkata"}),
                encoding="utf-8",
            )

            rc = resolve_cli.main([
                "--status-file", str(status_path),
                "--meta-file", str(meta_path),
                "--out-file", str(out_path),
                "--branch", "status/2026-11-18",
                "--now", "2026-11-18T14:00:00+05:30",
                "--strict",
            ])
            self.assertEqual(rc, 0)
            resolved = json.loads(out_path.read_text(encoding="utf-8"))
            self.assertEqual(resolved["status"], "travelling")
            self.assertFalse(resolved["expired"])

            # Branch date mismatch fails strict validation
            rc_mismatch = resolve_cli.main([
                "--status-file", str(status_path),
                "--meta-file", str(meta_path),
                "--branch", "status/2026-11-19",
                "--strict",
                "--check-only",
            ])
            self.assertEqual(rc_mismatch, 1)

            # Placeholders fail strict validation, but non-strict safely falls back to offline
            status_path.write_text(
                json.dumps(prepare_daily_status_pr.build_placeholder_status()),
                encoding="utf-8",
            )
            rc_placeholder_strict = resolve_cli.main([
                "--status-file", str(status_path),
                "--meta-file", str(meta_path),
                "--strict",
                "--check-only",
            ])
            self.assertEqual(rc_placeholder_strict, 1)

            rc_placeholder_safe = resolve_cli.main([
                "--status-file", str(status_path),
                "--meta-file", str(meta_path),
                "--out-file", str(out_path),
                "--now", "2026-11-18T14:00:00+05:30",
            ])
            self.assertEqual(rc_placeholder_safe, 0)
            safe_resolved = json.loads(out_path.read_text(encoding="utf-8"))
            self.assertEqual(safe_resolved["status"], "offline")
            self.assertTrue(safe_resolved["is_fallback"])

    def test_repo_committed_status_files_are_valid(self):
        rc = resolve_cli.main(["--strict", "--check-only"])
        self.assertEqual(rc, 0)
        user_cfg = se.load_json_file(REPO_ROOT / "data" / "status.json")
        self.assertEqual(set(user_cfg.keys()), {"status", "until"})
        resolved_path = REPO_ROOT / "data" / "status_resolved.json"
        self.assertTrue(resolved_path.exists())
        self.assertEqual(se.validate_resolved_status(se.load_json_file(resolved_path)), [])


class TestDailyPRPreparation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.presets, _ = se.load_presets(REPO_ROOT / "data" / "status_presets.json")

    def test_branch_title_and_body_include_ist_date_and_all_presets(self):
        d = date(2026, 11, 18)
        self.assertEqual(prepare_daily_status_pr.daily_branch_name(d), "status/2026-11-18")
        self.assertEqual(se.parse_daily_branch_date("status/2026-11-18"), d)
        self.assertIsNone(se.parse_daily_branch_date("feat/status"))

        title = prepare_daily_status_pr.daily_pr_title(d)
        self.assertIn("2026-11-18", title)
        self.assertIn("IST", title)

        body = prepare_daily_status_pr.build_pr_body(d, self.presets)
        self.assertIn("2026-11-18", body)
        self.assertIn("data/status.json", body)
        for preset_id in self.presets:
            self.assertIn(f"`{preset_id}`", body)

    def test_idempotent_planner_prevents_duplicates(self):
        d = date(2026, 11, 18)

        # Already open -> skip
        plan_open = prepare_daily_status_pr.plan_daily_pr_action(
            target_date=d,
            existing_prs_for_branch=[{"number": 42, "state": "open", "merged_at": None}],
            open_daily_prs=[],
            branch_exists=True,
        )
        self.assertEqual(plan_open["action"], "skip_already_open")

        # Already merged -> skip
        plan_merged = prepare_daily_status_pr.plan_daily_pr_action(
            target_date=d,
            existing_prs_for_branch=[{"number": 42, "state": "closed", "merged_at": "2026-11-18T02:00:00Z"}],
            open_daily_prs=[],
            branch_exists=False,
        )
        self.assertEqual(plan_merged["action"], "skip_already_merged")

        # Already closed unmerged -> skip (no duplicate PR for same date)
        plan_closed = prepare_daily_status_pr.plan_daily_pr_action(
            target_date=d,
            existing_prs_for_branch=[{"number": 42, "state": "closed", "merged_at": None}],
            open_daily_prs=[],
            branch_exists=False,
        )
        self.assertEqual(plan_closed["action"], "skip_already_closed")

    def test_existing_branch_manual_edits_are_preserved(self):
        d = date(2026, 11, 18)
        plan = prepare_daily_status_pr.plan_daily_pr_action(
            target_date=d,
            existing_prs_for_branch=[],
            open_daily_prs=[],
            branch_exists=True,
            existing_branch_status={"status": "working", "until": "21:00"},
        )
        self.assertEqual(plan["action"], "open_pr_for_existing_branch")
        self.assertTrue(plan["preserve_branch_contents"])
        self.assertTrue(plan["existing_branch_has_manual_edits"])

    def test_unmerged_previous_daily_prs_handled_safely(self):
        d = date(2026, 11, 18)
        unfilled_draft = {
            "number": 10,
            "state": "open",
            "draft": True,
            "head": {"ref": "status/2026-11-17"},
            "_status_config": prepare_daily_status_pr.build_placeholder_status(),
        }
        edited_draft = {
            "number": 11,
            "state": "open",
            "draft": True,
            "head": {"ref": "status/2026-11-16"},
            "_status_config": {"status": "travelling", "until": "22:00"},
        }
        ready_pr = {
            "number": 12,
            "state": "open",
            "draft": False,
            "head": {"ref": "status/2026-11-15"},
            "_status_config": prepare_daily_status_pr.build_placeholder_status(),
        }
        future_pr = {
            "number": 13,
            "state": "open",
            "draft": True,
            "head": {"ref": "status/2026-11-19"},
            "_status_config": prepare_daily_status_pr.build_placeholder_status(),
        }

        plan = prepare_daily_status_pr.plan_daily_pr_action(
            target_date=d,
            existing_prs_for_branch=[],
            open_daily_prs=[unfilled_draft, edited_draft, ready_pr, future_pr],
            branch_exists=False,
        )
        self.assertEqual(plan["action"], "create_branch_and_pr")
        self.assertEqual([p["number"] for p in plan["stale_prs_to_close"]], [10])
        self.assertEqual([p["number"] for p in plan["stale_prs_to_preserve"]], [11, 12])

    def test_write_daily_status_files_creates_real_diff_and_two_field_placeholder(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data_dir = root / "data"
            data_dir.mkdir()
            (data_dir / "status.json").write_text(
                json.dumps({"status": "working", "until": "21:00"}, indent=2) + "\n",
                encoding="utf-8",
            )
            (data_dir / "status_meta.json").write_text(
                json.dumps({"effective_date": "2026-11-17"}, indent=2) + "\n",
                encoding="utf-8",
            )

            before_status = (data_dir / "status.json").read_text(encoding="utf-8")
            before_meta = (data_dir / "status_meta.json").read_text(encoding="utf-8")

            prepare_daily_status_pr.write_daily_status_files(
                root,
                date(2026, 11, 18),
                prepared_at=datetime(2026, 11, 18, 7, 0, tzinfo=se.IST),
            )

            after_status_text = (data_dir / "status.json").read_text(encoding="utf-8")
            after_meta_text = (data_dir / "status_meta.json").read_text(encoding="utf-8")
            self.assertNotEqual(before_status, after_status_text)
            self.assertNotEqual(before_meta, after_meta_text)

            after_status = json.loads(after_status_text)
            self.assertEqual(set(after_status.keys()), {"status", "until"})
            self.assertTrue(prepare_daily_status_pr.is_placeholder_status_config(after_status))

            after_meta = json.loads(after_meta_text)
            self.assertEqual(after_meta["effective_date"], "2026-11-18")
            self.assertEqual(after_meta["timezone"], "Asia/Kolkata")
            self.assertEqual(after_meta["source_branch"], "status/2026-11-18")

    def test_dry_run_cli_succeeds(self):
        rc = prepare_daily_status_pr.main([
            "--date", "2026-11-18",
            "--now", "2026-11-18T07:00:00+05:30",
            "--dry-run",
        ])
        self.assertEqual(rc, 0)

    def test_main_orchestration_skips_duplicates_and_preserves_existing_branch(self):
        from unittest.mock import MagicMock, patch

        # 1. Existing open PR for today -> skip cleanly, never calls git or create_draft_pr
        fake_gh = MagicMock()
        fake_gh.list_prs_for_branch.return_value = [
            {"number": 99, "state": "open", "merged_at": None, "html_url": "https://example/99"}
        ]
        fake_gh.list_open_prs.return_value = []
        fake_gh.branch_exists.return_value = True
        fake_gh.fetch_json_file_on_ref.return_value = {"status": "working", "until": "21:00"}

        with patch.dict(os.environ, {"GITHUB_TOKEN": "test-token"}), \
                patch.object(prepare_daily_status_pr, "GitHubClient", return_value=fake_gh), \
                patch.object(prepare_daily_status_pr, "_run_git") as run_git:
            rc = prepare_daily_status_pr.main([
                "--date", "2026-11-18",
                "--now", "2026-11-18T07:00:00+05:30",
                "--repo", "ayushcmd/ayushcmd",
            ])
        self.assertEqual(rc, 0)
        run_git.assert_not_called()
        fake_gh.create_draft_pr.assert_not_called()

        # 2. Branch already exists with manual edits, no PR yet -> open Draft PR without touching git
        fake_gh2 = MagicMock()
        fake_gh2.list_prs_for_branch.return_value = []
        fake_gh2.list_open_prs.return_value = [
            {"number": 70, "state": "open", "draft": True, "head": {"ref": "status/2026-11-17"}},
            {"number": 71, "state": "open", "draft": True, "head": {"ref": "status/2026-11-16"}},
        ]
        fake_gh2.branch_exists.return_value = True

        def fake_fetch(_path, ref):
            if ref == "status/2026-11-18":
                return {"status": "travelling", "until": "22:00"}
            if ref == "status/2026-11-17":
                return prepare_daily_status_pr.build_placeholder_status()
            if ref == "status/2026-11-16":
                return {"status": "working", "until": "20:00"}
            return None

        fake_gh2.fetch_json_file_on_ref.side_effect = fake_fetch
        fake_gh2.create_draft_pr.return_value = {"number": 100, "html_url": "https://example/100"}

        with patch.dict(os.environ, {"GITHUB_TOKEN": "test-token"}), \
                patch.object(prepare_daily_status_pr, "GitHubClient", return_value=fake_gh2), \
                patch.object(prepare_daily_status_pr, "_run_git") as run_git:
            rc = prepare_daily_status_pr.main([
                "--date", "2026-11-18",
                "--now", "2026-11-18T07:00:00+05:30",
                "--repo", "ayushcmd/ayushcmd",
            ])
        self.assertEqual(rc, 0)
        run_git.assert_not_called()
        fake_gh2.create_draft_pr.assert_called_once()
        # Only unfilled stale draft #70 is closed; manually edited #71 is preserved
        fake_gh2.comment_and_close_pr.assert_called_once()
        self.assertEqual(fake_gh2.comment_and_close_pr.call_args.args[0], 70)


class TestStatusWorkflows(unittest.TestCase):
    def _load(self, filename: str) -> dict:
        path = REPO_ROOT / ".github" / "workflows" / filename
        self.assertTrue(path.exists(), f"Missing workflow: {path}")
        return yaml.safe_load(path.read_text(encoding="utf-8"))

    def test_daily_status_pr_workflow(self):
        wf = self._load("daily-status-pr.yml")
        triggers = wf.get("on", wf.get(True))
        self.assertEqual(triggers["schedule"], [{"cron": "30 1 * * *"}])
        self.assertIn("workflow_dispatch", triggers)
        self.assertEqual(
            wf["permissions"],
            {"contents": "write", "pull-requests": "write"},
        )
        raw = (REPO_ROOT / ".github" / "workflows" / "daily-status-pr.yml").read_text(encoding="utf-8")
        self.assertNotIn("gh pr merge", raw)
        self.assertNotIn("/merge", raw)

    def test_validate_status_workflow(self):
        wf = self._load("validate-status.yml")
        triggers = wf.get("on", wf.get(True))
        self.assertIn("pull_request", triggers)
        self.assertIn("ready_for_review", triggers["pull_request"]["types"])
        self.assertIn("synchronize", triggers["pull_request"]["types"])
        # No path filter so required status checks never stall unrelated PRs
        self.assertNotIn("paths", triggers["pull_request"])
        self.assertIn("workflow_dispatch", triggers)
        self.assertEqual(wf["permissions"], {"contents": "read"})

    def test_resolve_status_workflow(self):
        wf = self._load("resolve-status.yml")
        triggers = wf.get("on", wf.get(True))
        self.assertEqual(triggers["push"]["branches"], ["main"])
        self.assertIn("scripts/render_status_svg.py", triggers["push"]["paths"])
        self.assertIn("schedule", triggers)
        self.assertIn("workflow_dispatch", triggers)
        self.assertEqual(wf["permissions"], {"contents": "write"})
        steps = wf["jobs"]["resolve-status"]["steps"]
        run_cmds = "\n".join(step.get("run", "") for step in steps)
        self.assertIn("scripts/resolve_status.py", run_cmds)
        self.assertIn("scripts/render_status_svg.py", run_cmds)
        self.assertEqual(
            steps[-1]["with"]["file_pattern"],
            "data/status_resolved.json img/status/available.svg",
        )


class TestStatusSVGRendererAndReadme(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.presets, _ = se.load_presets(REPO_ROOT / "data" / "status_presets.json")

    def test_renderer_supports_all_six_statuses_with_valid_xml_and_text(self):
        import xml.etree.ElementTree as ET

        eff = date(2026, 11, 18)
        day_now = datetime(2026, 11, 18, 14, 0, tzinfo=se.IST)

        for status_id in sorted(EXPECTED_PRESETS):
            with self.subTest(status=status_id):
                resolved = se.resolve_status(
                    {"status": status_id, "until": "21:00"},
                    self.presets,
                    eff,
                    now=day_now,
                    strict=True,
                )
                svg_text = render_status_svg.render_status_svg_markup(resolved)
                root = ET.fromstring(svg_text)
                self.assertTrue(root.tag.endswith("svg"))
                self.assertEqual(root.attrib.get("width"), "860")

                # Must clearly display title, availability, message, and expiry time
                rendered_text = " ".join(root.itertext())
                self.assertIn(resolved["title"], rendered_text)
                self.assertIn(resolved["availability"].upper(), rendered_text)
                self.assertIn(resolved["message"], rendered_text)
                self.assertIn("21:00 IST", rendered_text)
                self.assertEqual(root.attrib.get("height"), "172")
                self.assertEqual(root.attrib.get("viewBox"), "0 0 860 172")
                if status_id == "working":
                    self.assertIn(resolved["project"], rendered_text)

                # Offline-only scene selection: only `offline` uses the animated Dino Runner scene
                if status_id == "offline":
                    self.assertIn('id="offline-dino-scene"', svg_text)
                else:
                    self.assertNotIn('id="offline-dino-scene"', svg_text)

    def test_offline_dino_scene_geometry_animations_and_dynamic_expiry(self):
        import xml.etree.ElementTree as ET

        eff = date(2026, 11, 18)
        manual_offline = se.resolve_status(
            {"status": "offline", "until": "18:30"},
            self.presets,
            eff,
            now=datetime(2026, 11, 18, 14, 0, tzinfo=se.IST),
            strict=True,
        )
        svg_text = render_status_svg.render_status_svg_markup(manual_offline)
        root = ET.fromstring(svg_text)

        # Correct dimensions and viewBox
        self.assertEqual(root.attrib.get("width"), "860")
        self.assertEqual(root.attrib.get("height"), "172")
        self.assertEqual(root.attrib.get("viewBox"), "0 0 860 172")

        # Pure vector SVG: no raster images, base64 data URIs, or JavaScript
        self.assertNotIn("<image", svg_text.lower())
        self.assertNotIn("data:image", svg_text.lower())
        self.assertNotIn("<script", svg_text.lower())

        # Offline Dino Runner scene components & pixel-art crispEdges rendering
        for expected_id in (
            'id="offline-dino-scene"',
            'id="offline-dino-runner"',
            'id="offline-ground"',
            'id="offline-cacti"',
            'id="offline-clouds"',
            'id="offline-moon"',
            'id="offline-stars"',
            'id="offline-hills"',
        ):
            self.assertIn(expected_id, svg_text)
        self.assertIn('shape-rendering="crispEdges"', svg_text)
        self.assertIn("prefers-reduced-motion: reduce", svg_text)

        # Native SVG animation elements present (<animate> and <animateTransform>)
        ns = {"svg": "http://www.w3.org/2000/svg"}
        animates = root.findall(".//svg:animate", ns)
        animate_transforms = root.findall(".//svg:animateTransform", ns)
        self.assertGreaterEqual(len(animates), 6)
        self.assertGreaterEqual(len(animate_transforms), 6)

        # Dynamic expiry and status text (not hardcoded to 07:00)
        rendered_text = " ".join(root.itertext())
        self.assertIn("Offline", rendered_text)
        self.assertIn("OFFLINE", rendered_text)
        self.assertIn("Away from the keyboard — catch you next cycle", rendered_text)
        self.assertIn("Expires at 18:30 IST", rendered_text)
        self.assertIn("STATUS · OFFLINE", rendered_text)

        # Night override (`sleeping`) must NOT use the offline dino scene
        night_resolved = se.resolve_status(
            {"status": "working", "until": "21:00"},
            self.presets,
            eff,
            now=datetime(2026, 11, 18, 22, 30, tzinfo=se.IST),
            strict=True,
        )
        night_svg = render_status_svg.render_status_svg_markup(night_resolved)
        self.assertNotIn('id="offline-dino-scene"', night_svg)

        # Expired fallback resolves to `offline` and displays the Dino scene with fallback expiry text
        expired_resolved = se.resolve_status(
            {"status": "working", "until": "21:00"},
            self.presets,
            eff,
            now=datetime(2026, 11, 18, 21, 5, tzinfo=se.IST),
            strict=True,
        )
        expired_svg = render_status_svg.render_status_svg_markup(expired_resolved)
        self.assertIn('id="offline-dino-scene"', expired_svg)
        self.assertIn("Expired · Offline until next update", expired_svg)
        self.assertIn("FALLBACK · OFFLINE", expired_svg)

    def test_renderer_handles_night_override_and_expired_fallback(self):
        import xml.etree.ElementTree as ET

        eff = date(2026, 11, 18)
        night_resolved = se.resolve_status(
            {"status": "working", "until": "21:00"},
            self.presets,
            eff,
            now=datetime(2026, 11, 18, 22, 30, tzinfo=se.IST),
            strict=True,
        )
        night_svg = render_status_svg.render_status_svg_markup(night_resolved)
        self.assertTrue(ET.fromstring(night_svg).tag.endswith("svg"))
        self.assertIn("Recharging", night_svg)
        self.assertIn("OFFLINE", night_svg)
        self.assertIn("06:00 IST", night_svg)

        expired_resolved = se.resolve_status(
            {"status": "working", "until": "21:00"},
            self.presets,
            eff,
            now=datetime(2026, 11, 18, 21, 5, tzinfo=se.IST),
            strict=True,
        )
        expired_svg = render_status_svg.render_status_svg_markup(expired_resolved)
        self.assertTrue(ET.fromstring(expired_svg).tag.endswith("svg"))
        self.assertIn("Offline", expired_svg)
        self.assertIn("OFFLINE", expired_svg)
        self.assertIn("Expired", expired_svg)

    def test_renderer_refuses_invalid_or_placeholder_status_and_preserves_existing_svg(self):
        resolved = se.resolve_status(
            {"status": "available", "until": "21:00"},
            self.presets,
            date(2026, 11, 18),
            now=datetime(2026, 11, 18, 14, 0, tzinfo=se.IST),
            strict=True,
        )

        # Placeholder status rejected
        bad_placeholder = dict(resolved)
        bad_placeholder["status"] = se.PLACEHOLDER_STATUS
        with self.assertRaises(ValueError):
            render_status_svg.render_status_svg_markup(bad_placeholder)

        # Unknown status ID rejected
        bad_unknown = dict(resolved)
        bad_unknown["status"] = "coding"
        with self.assertRaises(ValueError):
            render_status_svg.render_status_svg_markup(bad_unknown)

        # Placeholder title rejected
        bad_title = dict(resolved)
        bad_title["title"] = "TODO"
        with self.assertRaises(ValueError):
            render_status_svg.render_status_svg_markup(bad_title)

        # CLI refuses to overwrite an existing valid SVG when input JSON is invalid
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            in_path = tmp_dir / "resolved.json"
            out_path = tmp_dir / "available.svg"

            self.assertTrue(render_status_svg.render_status_svg_file(resolved, out_path))
            # Idempotent second write returns False
            self.assertFalse(render_status_svg.render_status_svg_file(resolved, out_path))
            good_svg = out_path.read_text(encoding="utf-8")

            in_path.write_text(json.dumps(bad_placeholder), encoding="utf-8")
            rc = render_status_svg.main(["--in-file", str(in_path), "--out-file", str(out_path)])
            self.assertEqual(rc, 1)
            self.assertEqual(out_path.read_text(encoding="utf-8"), good_svg)

    def test_local_svg_and_readme_live_status_section(self):
        import xml.etree.ElementTree as ET

        svg_path = REPO_ROOT / "img" / "status" / "available.svg"
        self.assertTrue(svg_path.exists(), f"Missing generated SVG: {svg_path}")
        root = ET.fromstring(svg_path.read_text(encoding="utf-8"))
        self.assertTrue(root.tag.endswith("svg"))

        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        heading = "### `ayushcmd@github ~ $ ./status --live`"
        img_tag = '<img src="./img/status/available.svg" width="860" alt="Ayush Live Status" />'
        whoami_heading = "### `ayushcmd@github ~ $ whoami`"
        whoami_table = "<table><tr>"
        about_heading = "### `ayushcmd@github ~ $ cat about-me.md`"

        self.assertTrue(readme.startswith(heading))
        self.assertIn(img_tag, readme)
        self.assertLess(readme.index(heading), readme.index(img_tag))
        self.assertLess(readme.index(img_tag), readme.index(whoami_heading))
        self.assertLess(readme.index(whoami_heading), readme.index(whoami_table))
        self.assertLess(readme.index(whoami_table), readme.index(about_heading))

        # Preserve all existing README sections
        for section in (
            "### `ayushcmd@github ~ $ whoami`",
            "### `ayushcmd@github ~ $ cat about-me.md`",
            "### `ayushcmd@github ~ $ ls featured-projects/`",
            "### `ayushcmd@github ~ $ ./analytics --overview`",
            "### `ayushcmd@github ~ $ ./stars --all`",
            "### `ayushcmd@github ~ $ ./support.sh`",
        ):
            self.assertIn(section, readme)


if __name__ == "__main__":
    unittest.main()
