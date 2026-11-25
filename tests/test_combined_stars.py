"""Combined ranking coverage: complete data, dated failures and growing lists."""
import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import make_star_stats as stars


class TestCombinedStars(unittest.TestCase):
    def reports(self, count=41):
        reports = {}
        for i, profile in enumerate(stars.all_profiles()):
            repos = [{"name": f"{profile['owner']}-long-repository-name-&-{j}", "stars": j}
                     for j in range(count if i == 0 else (2 if i == 1 else 12))]
            reports[profile["owner"]] = stars.build_report(
                repos, previous={}, today=f"2026-11-{23+i}", owner=profile["owner"])
        return reports

    def test_all_repositories_and_independent_dates_preserved(self):
        reports = self.reports()
        svg = stars.render_details(reports)
        root = ET.fromstring(svg)
        titles = [node.text for node in root.iter() if node.tag.endswith("}title")]
        for report in reports.values():
            self.assertIn("Snapshot " + report["generated"], svg)
            for repo in report["repositories"]:
                self.assertEqual(titles.count(f'{repo["name"]}: {stars.fmt(repo["stars"])} stars'), 1)
        self.assertIn("PERSONAL STARS", svg)
        self.assertIn("ORGANIZATION STARS", svg)
        self.assertNotIn("Automate-Workflow", svg)

    def test_missing_account_is_unavailable_not_zero(self):
        reports = self.reports()
        del reports["Readmify"]
        svg = stars.render_details(reports)
        ET.fromstring(svg)
        self.assertIn("N/A", svg)
        self.assertIn("Awaiting successful refresh", svg)
        self.assertIn("Snapshot 2026-11-23", svg)

    def test_empty_accounts_and_growth_fit_canvas(self):
        for count in (0, 1, 41, 101):
            reports = self.reports(count)
            root = ET.fromstring(stars.render_details(reports))
            height = float(root.attrib["height"])
            for node in root.iter():
                if node.tag.endswith("}text"):
                    self.assertLess(float(node.attrib["y"]), height)
        reports = {p["owner"]: stars.build_report([], previous={}, owner=p["owner"])
                   for p in stars.all_profiles()}
        svg = stars.render_details(reports)
        ET.fromstring(svg)
        self.assertEqual(svg.count("No public repositories"), 3)
        self.assertNotIn("N/A", svg)


if __name__ == "__main__":
    unittest.main()
