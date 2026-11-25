"""Regression coverage for retired accounts and incomplete language refreshes."""
import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
import make_languages_donut as languages
import make_star_stats as stars


class TestProfileDataAudit(unittest.TestCase):
    def test_retired_cached_account_cannot_inflate_overview(self):
        reports = {}
        for profile in stars.all_profiles():
            reports[profile['owner']] = stars.build_report(
                [{'name': 'example', 'stars': 5}], previous={}, owner=profile['owner'])
        reports['Automate-Workflow'] = stars.build_report(
            [{'name': 'retired-repo', 'stars': 999}], previous={}, owner='Automate-Workflow')
        self.assertEqual(stars.overview_totals(reports),
                         {'personal': 5, 'organizations': 10, 'tracked': 2})
        svg = stars.render_overview(reports)
        self.assertNotIn('Automate-Workflow', svg)
        self.assertNotIn('retired-repo', svg)
        readme = (ROOT / 'README.md').read_text(encoding='utf-8')
        self.assertNotIn('star-stats-automate-workflow.svg', readme)

    def test_language_failure_preserves_previous_asset(self):
        repos = [{'languages_url': 'one'}, {'languages_url': 'two'}]
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'chart.svg'
            output.write_text('previous complete chart', encoding='utf-8')
            with patch.object(languages, 'OUTPUT_PATH', output), \
                 patch.object(languages, 'fetch_repos', return_value=repos), \
                 patch.object(languages, 'api_get', side_effect=[{'Python': 80}, urllib.error.URLError('unavailable')]):
                with self.assertRaises(urllib.error.URLError):
                    languages.main()
            self.assertEqual(output.read_text(), 'previous complete chart')

    def test_complete_language_sample_totals_and_empty_repositories(self):
        with patch.object(languages, 'api_get', side_effect=[{'Python': 80}, {}, {'Python': 20, 'HTML': 100}]):
            totals = languages.fetch_language_totals([{'languages_url': str(i)} for i in range(3)])
        self.assertEqual(totals, {'Python': 100, 'HTML': 100})
        self.assertEqual(sum(row[1] for row in languages.group_languages(totals)), 100)

    def test_zero_and_malformed_language_data(self):
        self.assertEqual(languages.group_languages({'Python': 0}), [])
        with self.assertRaises(ValueError):
            languages.build_svg({'Python': 0}, 1)
        for response in [{'Python': -1}, {'Python': '100'}, []]:
            with patch.object(languages, 'api_get', return_value=response):
                with self.assertRaises(ValueError):
                    languages.fetch_language_totals([{'languages_url': 'example'}])


if __name__ == '__main__':
    unittest.main()
