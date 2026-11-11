#!/usr/bin/env python3
"""
Unit and integration tests for profile README scripts:
- Contribution scraper parsing and error handling
- Streak calculation (current streak with today handling, longest streak)
- Monthly aggregations and average calculations
- SVG XML validity for all generated SVGs
- Workflow YAML validity
"""
import os
import sys
import unittest
from datetime import date
import xml.etree.ElementTree as ET
import yaml

# Add scripts directory to path
HERE = os.path.dirname(__file__)
SCRIPTS_DIR = os.path.join(HERE, "..", "scripts")
sys.path.insert(0, SCRIPTS_DIR)

from fetch_contributions import compute_current_streak, compute_longest_streak, build_data
from render_heatmap_svg import render as render_heatmap
from make_info_card import render_info_card
import make_loc_stats
import make_star_stats


class TestContributionCalculations(unittest.TestCase):
    def test_streaks_with_today_active(self):
        days = [
            {"date": "2026-10-01", "count": 1},
            {"date": "2026-10-02", "count": 2},
            {"date": "2026-10-03", "count": 0},
            {"date": "2026-10-04", "count": 3},
            {"date": "2026-10-05", "count": 4},  # today
        ]
        cur_len, start, end = compute_current_streak(days)
        self.assertEqual(cur_len, 2)
        self.assertEqual(start, "2026-10-04")
        self.assertEqual(end, "2026-10-05")

    def test_streaks_with_today_inactive_but_yesterday_active(self):
        days = [
            {"date": "2026-10-01", "count": 1},
            {"date": "2026-10-02", "count": 2},
            {"date": "2026-10-03", "count": 3},  # yesterday
            {"date": "2026-10-04", "count": 0},  # today in progress
        ]
        cur_len, start, end = compute_current_streak(days)
        self.assertEqual(cur_len, 3)
        self.assertEqual(start, "2026-10-01")
        self.assertEqual(end, "2026-10-03")

    def test_streaks_with_broken_streak(self):
        days = [
            {"date": "2026-10-01", "count": 5},
            {"date": "2026-10-02", "count": 0},
            {"date": "2026-10-03", "count": 0},
        ]
        cur_len, start, end = compute_current_streak(days)
        self.assertEqual(cur_len, 0)
        self.assertIsNone(start)
        self.assertIsNone(end)

    def test_longest_streak(self):
        days = [
            {"date": "2026-10-01", "count": 2},
            {"date": "2026-10-02", "count": 1},
            {"date": "2026-10-03", "count": 3},
            {"date": "2026-10-04", "count": 0},
            {"date": "2026-10-05", "count": 1},
        ]
        long_len, start, end = compute_longest_streak(days)
        self.assertEqual(long_len, 3)
        self.assertEqual(start, "2026-10-01")
        self.assertEqual(end, "2026-10-03")

    def test_build_data_zero_division(self):
        days = [
            {"date": "2026-10-01", "count": 0},
            {"date": "2026-10-02", "count": 0},
        ]
        data = build_data(days, username="testuser")
        self.assertEqual(data["total_contributions"], 0)
        self.assertEqual(data["active_days"], 0)
        self.assertEqual(data["avg_per_active_day"], 0.0)

    def test_build_data_with_account_stats(self):
        days = [{"date": "2026-10-01", "count": 1}]
        stats = {
            "public_repos": 22,
            "total_stars": 58,
            "total_prs": 6,
            "merged_prs": 6,
            "commits_365d": 280,
            "code_reviews": 0,
            "total_issues": 30,
        }
        data = build_data(days, username="ayushcmd", account_stats=stats)
        self.assertEqual(data["github_stats"]["public_repos"], 22)
        self.assertEqual(data["github_stats"]["total_prs"], 6)
        self.assertEqual(data["github_stats"]["merged_prs"], 6)
        self.assertEqual(data["github_stats"]["commits_365d"], 280)
        self.assertEqual(data["github_stats"]["code_reviews"], 0)


class TestSVGValidity(unittest.TestCase):
    def test_generated_svgs_are_valid_xml(self):
        assets = [
            os.path.join(HERE, "..", "assets", "contrib-heatmap.svg"),
            os.path.join(HERE, "..", "assets", "ayush-ascii.svg"),
            os.path.join(HERE, "..", "assets", "info-card.svg"),
            os.path.join(HERE, "..", "assets", "codebase-analytics.svg"),
            os.path.join(HERE, "..", "assets", "star-stats.svg"),
            os.path.join(HERE, "..", "assets", "star-stats-portfoliostore99.svg"),
            os.path.join(HERE, "..", "assets", "star-stats-readmify.svg"),
            os.path.join(HERE, "..", "assets", "star-stats-automate-workflow.svg"),
        ]
        for path in assets:
            if os.path.exists(path):
                try:
                    tree = ET.parse(path)
                    root = tree.getroot()
                    self.assertTrue(root.tag.endswith("svg"), f"Root tag of {path} is not svg")
                except ET.ParseError as err:
                    self.fail(f"XML parse error in {path}: {err}")


def _sample_records():
    months = make_loc_stats.month_keys(date(2026, 11, 5))
    def record(name, private, live, authored):
        return {
            "name": name,
            "private": private,
            "live_lines": live,
            "live_by_language": {"Python": live // 2, "TypeScript": live - live // 2},
            "authored": {
                "total": authored,
                "last_30_days": authored // 10,
                "by_month": {months[-1]: authored // 10},
                "by_language": {},
            },
        }
    return [
        record("public-small", False, 1000, 2000),
        record("secret-big", True, 9000, 12000),
        record("public-big", False, 5000, 6000),
    ]


class TestCodebaseAnalytics(unittest.TestCase):
    def test_classification_excludes_vendor_lock_and_minified(self):
        self.assertEqual(make_loc_stats.classify("src/app.py"), "Python")
        self.assertEqual(make_loc_stats.classify("Dockerfile"), "Dockerfile")
        self.assertIsNone(make_loc_stats.classify("node_modules/lib/index.js"))
        self.assertIsNone(make_loc_stats.classify("package-lock.json"))
        self.assertIsNone(make_loc_stats.classify("static/app.min.js"))
        self.assertIsNone(make_loc_stats.classify("README.md"))

    def test_numstat_rename_paths(self):
        norm = make_loc_stats.normalize_numstat_path
        self.assertEqual(norm("src/{old => new}/file.py"), "src/new/file.py")
        self.assertEqual(norm("old.py => new.py"), "new.py")
        self.assertEqual(norm("plain.py"), "plain.py")

    def test_parse_numstat_filters_and_buckets(self):
        log = "\n".join([
            "@@2026-11-01T10:00:00+05:30",
            "10\t2\tapp.py",
            "500\t0\tpackage-lock.json",
            "-\t-\timage.png",
            "99999\t0\tgenerated.js",
            "7\t0\tnotebook.ipynb",
            "",
            "@@2026-09-01T10:00:00+05:30",
            "4\t1\tmain.ts",
        ])
        stats = make_loc_stats.parse_numstat(log, since="2026-10-06")
        self.assertEqual(stats["total"], 14)
        self.assertEqual(stats["last_30_days"], 10)
        self.assertEqual(stats["by_month"], {"2026-11": 10, "2026-09": 4})
        self.assertEqual(stats["by_language"], {"Python": 10, "TypeScript": 4})

    def test_count_live_skips_excluded_and_counts_notebook_code_cells(self):
        import json
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.py").write_text("x = 1\n\ny = 2\n")
            (root / "node_modules").mkdir()
            (root / "node_modules" / "dep.js").write_text("a\n" * 50)
            (root / "yarn.lock").write_text("x\n" * 50)
            (root / "nb.ipynb").write_text(json.dumps({"cells": [
                {"cell_type": "code", "source": ["a = 1\n", "\n", "b = 2\n"]},
                {"cell_type": "markdown", "source": ["# title"]},
            ]}))
            counts = make_loc_stats.count_live(root)
        self.assertEqual(counts, {"Python": 3, "Jupyter Notebook": 2})

    def test_authored_stats_uses_real_git_history(self):
        import shutil
        import subprocess
        import tempfile
        from pathlib import Path
        if shutil.which("git") is None:
            self.skipTest("git is not installed")
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            def git(*args):
                subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            git("config", "user.name", "Ayush Raj")
            git("config", "user.email", "me@example.com")
            (repo / "mine.py").write_text("a = 1\n" * 5)
            git("add", "-A")
            git("commit", "-qm", "mine")
            (repo / "theirs.py").write_text("b = 1\n" * 40)
            git("add", "-A")
            git("-c", "user.name=Someone Else", "-c", "user.email=else@example.com",
                "commit", "-qm", "theirs")
            stats = make_loc_stats.authored_stats(repo, ["Ayush Raj"])
        self.assertEqual(stats["total"], 5)

    def test_report_masks_private_names_and_ranks_by_live_lines(self):
        report = make_loc_stats.build_report(
            _sample_records(), today=date(2026, 11, 5), show_private_names=False
        )
        names = [repo["name"] for repo in report["top_repositories"]]
        self.assertEqual(names, ["private-project-1", "public-big", "public-small"])
        self.assertNotIn("secret-big", str(report))
        self.assertEqual(report["totals"]["authored_lines"], 20000)
        self.assertEqual(report["totals"]["live_lines"], 15000)
        self.assertEqual(report["totals"]["private"], 1)
        self.assertEqual(len(report["monthly_authored"]), 12)
        self.assertEqual(report["monthly_authored"][-1]["month"], "2026-11")

    def test_rendered_svg_is_valid_and_leaks_no_private_name(self):
        report = make_loc_stats.build_report(
            _sample_records(), today=date(2026, 11, 5), show_private_names=False
        )
        for svg in (make_loc_stats.render_svg(report), make_loc_stats.render_placeholder()):
            root = ET.fromstring(svg)
            self.assertTrue(root.tag.endswith("svg"))
            self.assertNotIn("secret-big", svg)


def _raw_repos():
    return [
        {"name": "zeta", "stargazers_count": 3, "fork": False, "private": False},
        {"name": "Alpha", "stargazers_count": 3, "fork": False, "private": False},
        {"name": "big", "stargazers_count": 40, "fork": False, "private": False},
        {"name": "empty", "stargazers_count": 0, "fork": False, "private": False},
        {"name": "forked", "stargazers_count": 2, "fork": True, "private": False},
        {"name": "secret", "stargazers_count": 99, "fork": False, "private": True},
        {"name": "skipme", "stargazers_count": 7, "fork": False, "private": False},
    ]


class TestStarStats(unittest.TestCase):
    def test_parse_repos_filters_and_ranks(self):
        repos = make_star_stats.parse_repos(_raw_repos(), include_forks=True, excluded={"skipme"})
        self.assertEqual(
            [repo["name"] for repo in repos],
            ["big", "Alpha", "zeta", "forked", "empty"],
        )
        self.assertNotIn("secret", str(repos))

    def test_parse_repos_can_hide_forks(self):
        repos = make_star_stats.parse_repos(_raw_repos(), include_forks=False)
        self.assertNotIn("forked", [repo["name"] for repo in repos])

    def test_report_totals_and_first_snapshot(self):
        repos = make_star_stats.parse_repos(_raw_repos(), include_forks=True, excluded={"skipme"})
        report = make_star_stats.build_report(repos, previous={}, today="2026-11-11")
        self.assertEqual(report["totals"]["stars"], 48)
        self.assertEqual(report["totals"]["repositories"], 5)
        self.assertEqual(report["totals"]["starred_repositories"], 4)
        self.assertEqual(report["top_repository"], {"name": "big", "stars": 40})
        self.assertIsNone(report["change"]["stars"])
        self.assertEqual(report["history"], [{"date": "2026-11-11", "stars": 48}])

    def test_change_is_measured_against_previous_day_and_rerun_is_stable(self):
        repos = [{"name": "big", "stars": 50}]
        previous = {"history": [
            {"date": "2026-11-09", "stars": 40},
            {"date": "2026-11-10", "stars": 47},
            {"date": "2026-11-11", "stars": 49},
        ]}
        report = make_star_stats.build_report(repos, previous=previous, today="2026-11-11")
        self.assertEqual(report["change"], {"since": "2026-11-10", "stars": 3})
        self.assertEqual(
            [entry["date"] for entry in report["history"]],
            ["2026-11-09", "2026-11-10", "2026-11-11"],
        )
        self.assertEqual(report["history"][-1]["stars"], 50)

    def test_history_is_capped(self):
        previous = {"history": [
            {"date": f"2026-09-{day:02d}", "stars": day} for day in range(1, 31)
        ] + [{"date": f"2026-10-{day:02d}", "stars": day} for day in range(1, 11)]}
        report = make_star_stats.build_report(
            [{"name": "a", "stars": 1}], previous=previous, today="2026-11-11"
        )
        self.assertEqual(len(report["history"]), make_star_stats.HISTORY_DAYS)
        self.assertEqual(report["history"][-1]["date"], "2026-11-11")

    def test_negative_change_is_reported(self):
        previous = {"history": [{"date": "2026-11-10", "stars": 10}]}
        report = make_star_stats.build_report(
            [{"name": "a", "stars": 8}], previous=previous, today="2026-11-11"
        )
        self.assertEqual(report["change"]["stars"], -2)
        self.assertEqual(make_star_stats.change_tile(report["change"])[0], "-2")

    def test_rendered_svg_is_valid_and_lists_every_repository(self):
        repos = [{"name": f"repo-{index:02d}", "stars": 30 - index} for index in range(31)]
        repos.append({"name": "x" * 40, "stars": 0})
        report = make_star_stats.build_report(repos, previous={}, today="2026-11-11")
        svg = make_star_stats.render_svg(report)
        root = ET.fromstring(svg)
        self.assertTrue(root.tag.endswith("svg"))
        self.assertEqual(root.attrib["height"], str(make_star_stats.card_height(32)))
        for index in range(31):
            self.assertIn(f"repo-{index:02d}", svg)
        self.assertNotIn("x" * 40, svg)
        self.assertIn("updated 2026-11-11", svg)

    def test_placeholder_svg_is_valid(self):
        root = ET.fromstring(make_star_stats.render_placeholder())
        self.assertTrue(root.tag.endswith("svg"))

    def test_fetch_repos_paginates(self):
        pages = {
            1: [{"name": f"r{i}"} for i in range(100)],
            2: [{"name": "last"}],
        }
        calls = []

        def fake_fetch(url):
            page = int(url.rsplit("page=", 1)[1])
            calls.append(page)
            return pages.get(page, [])

        original = make_star_stats.fetch_json
        make_star_stats.fetch_json = fake_fetch
        try:
            repos = make_star_stats.fetch_repos()
        finally:
            make_star_stats.fetch_json = original
        self.assertEqual(len(repos), 101)
        self.assertEqual(calls, [1, 2])


class TestStarStatsOrganizations(unittest.TestCase):
    def test_profiles_cover_personal_account_and_three_organizations(self):
        profiles = make_star_stats.all_profiles()
        self.assertEqual(
            [(profile["owner"], profile["theme"]) for profile in profiles],
            [
                (make_star_stats.USERNAME, "gold"),
                ("PortfolioStore99", "orange"),
                ("Readmify", "sky"),
                ("Automate-Workflow", "red"),
            ],
        )
        self.assertEqual(len({profile["slug"] for profile in profiles}), 4)

    def test_each_card_uses_its_own_theme_colour(self):
        report = make_star_stats.build_report(
            [{"name": "tool", "stars": 5}], previous={}, today="2026-11-11", owner="X"
        )
        svgs = {}
        for profile in make_star_stats.all_profiles():
            svg = make_star_stats.render_svg(report, profile)
            ET.fromstring(svg)
            self.assertIn(make_star_stats.THEMES[profile["theme"]]["accent"], svg)
            self.assertIn(profile["title"], svg)
            svgs[profile["owner"]] = svg
        gold = make_star_stats.THEMES["gold"]["accent"]
        for owner in ("PortfolioStore99", "Readmify", "Automate-Workflow"):
            self.assertNotIn(gold, svgs[owner])

    def test_placeholders_are_valid_for_every_profile(self):
        for profile in make_star_stats.all_profiles():
            root = ET.fromstring(make_star_stats.render_placeholder(profile))
            self.assertTrue(root.tag.endswith("svg"))

    def test_empty_organization_still_renders(self):
        report = make_star_stats.build_report([], previous={}, today="2026-11-11", owner="Readmify")
        profile = make_star_stats.organization_profile("Readmify", "sky")
        root = ET.fromstring(make_star_stats.render_svg(report, profile))
        self.assertTrue(root.tag.endswith("svg"))

    def test_one_failing_account_does_not_block_the_others(self):
        import contextlib
        import io
        import tempfile
        import urllib.error
        from pathlib import Path

        def fake_fetch(owner=None):
            if owner == "Readmify":
                raise urllib.error.URLError("unreachable")
            return [{"name": f"{owner}-repo", "stargazers_count": 2, "fork": False, "private": False}]

        original_fetch = make_star_stats.fetch_repos
        original_root = make_star_stats.ROOT
        with tempfile.TemporaryDirectory() as tmp:
            make_star_stats.fetch_repos = fake_fetch
            make_star_stats.ROOT = Path(tmp)
            try:
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as raised:
                        make_star_stats.main()
            finally:
                make_star_stats.fetch_repos = original_fetch
                make_star_stats.ROOT = original_root
            written = sorted(path.name for path in (Path(tmp) / "assets").glob("*.svg"))
        self.assertEqual(raised.exception.code, 1)
        self.assertEqual(
            written,
            ["star-stats-automate-workflow.svg", "star-stats-portfoliostore99.svg", "star-stats.svg"],
        )


class TestWorkflowYAML(unittest.TestCase):
    def test_workflow_yaml_syntax(self):
        wf_path = os.path.join(HERE, "..", ".github", "workflows", "update-profile-art.yml")
        self.assertTrue(os.path.exists(wf_path))
        with open(wf_path, "r", encoding="utf-8") as f:
            content = yaml.safe_load(f)
        self.assertIn("name", content)
        self.assertIn("jobs", content)
        steps = content["jobs"]["refresh-profile-art"]["steps"]
        self.assertIn("python scripts/make_star_stats.py", [step.get("run", "").strip() for step in steps])
        self.assertIn("schedule", content.get("on", content.get(True, {})))


if __name__ == "__main__":
    unittest.main()
