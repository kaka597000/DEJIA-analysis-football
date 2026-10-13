"""Regression checks for real football-data.co.uk kickoff-time semantics."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from collect_history import build_timezone_audit, parse_rows  # noqa: E402


class KickoffTimezoneTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = []
        for season in ("2019-2020", "2025-2026"):
            path = ROOT / "data/raw/football-data.co.uk" / f"{season}_D1.csv"
            url = f"https://www.football-data.co.uk/mmz4281/{season[2:4]}{season[7:9]}/D1.csv"
            parsed, _, invalid, _ = parse_rows(path.read_bytes(), season, url, "2026-10-13T01:50:39Z")
            if invalid:
                raise AssertionError(f"unexpected invalid rows in {path}: {invalid[:3]}")
            cls.rows.extend(parsed)

    def find(self, season, date, home, away):
        return next(row for row in self.rows if (row["season"], row["match_date"], row["home_team"], row["away_team"]) == (season, date, home, away))

    def test_bst_time_converts_to_utc_using_london_rules(self):
        row = self.find("2019-2020", "2019-08-16", "Bayern Munich", "Hertha")
        self.assertEqual(row["raw_row"]["Time"], "19:30")
        self.assertEqual(row["kickoff_utc"], "2019-08-16T18:30:00Z")

    def test_gmt_time_converts_to_utc_using_london_rules(self):
        row = self.find("2019-2020", "2019-12-21", "Bayern Munich", "Wolfsburg")
        self.assertEqual(row["raw_row"]["Time"], "14:30")
        self.assertEqual(row["kickoff_utc"], "2019-12-21T14:30:00Z")

    def test_summer_and_winter_examples_match_saved_openligadb_utc(self):
        audit = build_timezone_audit(self.rows)
        self.assertEqual(audit["status"], "ok", audit["errors"])
        self.assertEqual(audit["source_timezone"], "Europe/London")
        self.assertEqual(audit["sample_count"], 3)
        self.assertTrue(all(sample["agrees"] for sample in audit["samples"]))
        self.assertEqual(
            [sample["openligadb_kickoff_utc"] for sample in audit["samples"]],
            ["2019-08-16T18:30:00Z", "2019-12-21T14:30:00Z", "2025-08-22T18:30:00Z"],
        )


if __name__ == "__main__":
    unittest.main()
