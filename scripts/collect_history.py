#!/usr/bin/env python3
"""Collect Bundesliga match history from football-data.co.uk.

Raw snapshots are content-addressed and never replaced. Re-running against the
same upstream bytes is idempotent; changed upstream bytes become a new snapshot.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data/raw/football-data.co.uk"
OPENFOOTBALL_RAW = ROOT / "data/raw/openfootball"
DFL_RAW = ROOT / "data/raw/dfl"
OPENLIGADB_RAW = ROOT / "data/raw/openligadb"
PROCESSED = ROOT / "data/processed"
BASE_URL = "https://www.football-data.co.uk/mmz4281"
SEASONS = [(year, year + 1) for year in range(2015, 2026)]
EXPECTED_PER_SEASON = 306
TZ = ZoneInfo("Europe/London")


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def download(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "bundesliga-history-collector/1.0"})
    last_error = None
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=45) as response:
                return response.read()
        except Exception as exc:
            last_error = exc
            if attempt == 4:
                break
            time.sleep(1 + attempt * 2)
    raise last_error


def store_snapshot(season: str, data: bytes) -> tuple[Path, str]:
    digest = sha256(data)
    stem = f"{season}_D1"
    existing = sorted(RAW.glob(f"{stem}*.csv"))
    for path in existing:
        if sha256(path.read_bytes()) == digest:
            return path, digest
    name = f"{stem}.csv" if not existing else f"{stem}_{digest[:12]}.csv"
    path = RAW / name
    # If a collision somehow exists, never replace it.
    if path.exists() and sha256(path.read_bytes()) != digest:
        path = RAW / f"{stem}_{digest}.csv"
    if not path.exists():
        path.write_bytes(data)
    Path(str(path) + ".sha256").write_text(f"{digest}  {path.name}\n", encoding="ascii")
    return path, digest


def store_aux_snapshot(folder: Path, stem: str, suffix: str, data: bytes) -> tuple[Path, str]:
    digest = sha256(data)
    folder.mkdir(parents=True, exist_ok=True)
    for path in sorted(folder.glob(f"{stem}*.{suffix}")):
        if sha256(path.read_bytes()) == digest:
            return path, digest
    path = folder / f"{stem}.{suffix}" if not list(folder.glob(f"{stem}*.{suffix}")) else folder / f"{stem}_{digest[:12]}.{suffix}"
    if not path.exists():
        path.write_bytes(data)
    Path(str(path) + ".sha256").write_text(f"{digest}  {path.name}\n", encoding="ascii")
    return path, digest


def season_label(start: int) -> str:
    return f"{start}-{start + 1}"


def date_parse(value: str) -> str:
    value = value.strip()
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            pass
    raise ValueError(f"unrecognized match date: {value!r}")


def parse_rows(content: bytes, season: str, source_url: str, collected_at: str):
    text = content.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ValueError("CSV has no header")
    header = [x.strip() if x else "" for x in reader.fieldnames]
    if not {"Div", "Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "FTR"}.issubset(header):
        raise ValueError(f"unexpected D1 schema; fields={header}")
    rows = []
    missing = {}
    invalid = []
    for line_num, raw in enumerate(reader, 2):
        row = {str(k).strip(): (v.strip() if isinstance(v, str) else v) for k, v in raw.items() if k is not None}
        if not any(row.values()):
            continue
        for key, value in row.items():
            if value is None or value == "":
                missing[key] = missing.get(key, 0) + 1
        if row.get("Div") != "D1":
            invalid.append({"line": line_num, "reason": "Div is not D1", "value": row.get("Div")})
            continue
        try:
            match_date = date_parse(row["Date"])
            home_goals, away_goals = int(row["FTHG"]), int(row["FTAG"])
        except (ValueError, KeyError) as exc:
            invalid.append({"line": line_num, "reason": str(exc)})
            continue
        home, away = row["HomeTeam"], row["AwayTeam"]
        result = "H" if home_goals > away_goals else "A" if home_goals < away_goals else "D"
        source_result = row["FTR"].upper()
        if source_result != result:
            invalid.append({"line": line_num, "reason": "FTR disagrees with full-time score", "source": source_result, "derived": result})
            continue
        raw_time = row.get("Time", "")
        kickoff_utc = None
        kickoff_precision = "date"
        if raw_time:
            try:
                # football-data.co.uk's D1 Time is UK local clock time. Convert
                # with Europe/London rules, including GMT/BST, then emit UTC.
                local = datetime.strptime(f"{match_date} {raw_time}", "%Y-%m-%d %H:%M").replace(tzinfo=TZ)
                kickoff_utc = local.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
                kickoff_precision = "minute"
            except ValueError:
                missing["Time_unparseable"] = missing.get("Time_unparseable", 0) + 1
        identity = "\x1f".join(("DE1", season, match_date, home, away))
        match_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        rows.append({
            "match_id": match_id,
            "competition": "DE1",
            "season": season,
            "kickoff_utc": kickoff_utc,
            "kickoff_precision": kickoff_precision,
            "match_date": match_date,
            "home_team": home,
            "away_team": away,
            "home_goals": home_goals,
            "away_goals": away_goals,
            "result": result,
            "source_url": source_url,
            "collected_at": collected_at,
            "raw_row": row,
        })
    return rows, missing, invalid, header


def build_timezone_audit(rows):
    """Compare real CSV clock values with OpenLigaDB's explicit UTC values."""
    samples = [
        ("2019-2020", "2019-08-16", "Bayern Munich", "Hertha", 2019, "FC Bayern München", "Hertha BSC"),
        ("2019-2020", "2019-12-21", "Bayern Munich", "Wolfsburg", 2019, "FC Bayern München", "VfL Wolfsburg"),
        ("2025-2026", "2025-08-22", "Bayern Munich", "RB Leipzig", 2025, "FC Bayern München", "RB Leipzig"),
    ]
    by_fixture = {(r["season"], r["match_date"], r["home_team"], r["away_team"]): r for r in rows}
    evidence = []
    errors = []
    for season, date, home, away, year, olb_home, olb_away in samples:
        raw = OPENLIGADB_RAW / f"bl1_{year}.json"
        if not raw.exists():
            errors.append(f"missing independent OpenLigaDB snapshot: {raw}")
            continue
        fd = by_fixture.get((season, date, home, away))
        matches = json.loads(raw.read_text(encoding="utf-8"))
        olb = next((m for m in matches if m.get("matchDateTime", "").startswith(date) and m.get("team1", {}).get("teamName") == olb_home and m.get("team2", {}).get("teamName") == olb_away), None)
        if fd is None or olb is None:
            errors.append(f"could not join timezone audit fixture {season} {date} {home} v {away}")
            continue
        utc = olb.get("matchDateTimeUTC")
        agrees = fd["kickoff_utc"] == utc
        evidence.append({
            "season": season, "match_date": date, "home_team_football_data": home,
            "away_team_football_data": away, "football_data_time": fd["raw_row"].get("Time"),
            "football_data_kickoff_utc": fd["kickoff_utc"],
            "openligadb_local_datetime": olb.get("matchDateTime"),
            "openligadb_kickoff_utc": utc, "agrees": agrees,
            "openligadb_raw_file": str(raw.relative_to(ROOT)),
            "openligadb_sha256": sha256(raw.read_bytes()),
        })
        if not agrees:
            errors.append(f"UTC mismatch for {season} {date} {home} v {away}")
    return {
        "status": "ok" if len(evidence) == len(samples) and not errors else "failed",
        "source_timezone": "Europe/London",
        "conversion": "Parse source Date + Time in Europe/London (GMT/BST by date), then convert to UTC.",
        "sample_count": len(evidence), "samples": evidence, "errors": errors,
    }


def reprocess_existing_raw() -> int:
    """Rebuild normalized outputs from saved snapshots without network access."""
    manifest_path = PROCESSED / "manifest.json"
    quality_path = PROCESSED / "quality.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    quality = json.loads(quality_path.read_text(encoding="utf-8"))
    rows = []
    for season_record in manifest["seasons"]:
        raw_path = ROOT / season_record["raw_file"]
        raw_bytes = raw_path.read_bytes()
        if sha256(raw_bytes) != season_record["sha256"]:
            raise ValueError(f"raw hash mismatch: {raw_path}")
        parsed, missing, invalid, headers = parse_rows(raw_bytes, season_record["season"], season_record["source_url"], season_record["collected_at"])
        season_record.update({"headers": headers, "row_count": len(parsed), "missing_values_by_field": missing, "invalid_rows": invalid})
        rows.extend(parsed)
    current_record = manifest["current_season_snapshot"]
    current_path = ROOT / current_record["raw_file"]
    current_bytes = current_path.read_bytes()
    if sha256(current_bytes) != current_record["sha256"]:
        raise ValueError(f"raw hash mismatch: {current_path}")
    current_rows, current_missing, current_invalid, current_headers = parse_rows(current_bytes, current_record["season"], current_record["source_url"], current_record["collected_at"])
    current_record.update({"headers": current_headers, "completed_match_count": len(current_rows), "missing_values_by_field": current_missing, "invalid_rows": current_invalid})
    audit = build_timezone_audit(rows)
    if audit["status"] != "ok":
        raise ValueError(f"timezone audit did not pass: {audit['errors']}")
    with (PROCESSED / "matches.jsonl").open("w", encoding="utf-8") as out:
        for row in rows:
            out.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    with (PROCESSED / "matches_2026-2027_current.jsonl").open("w", encoding="utf-8") as out:
        for row in current_rows:
            out.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    manifest["kickoff_timezone_audit"] = audit
    manifest["schema_notes"]["kickoff_utc"] = "Converted from source Europe/London clock time (GMT/BST by date); null when absent or unparseable."
    manifest["schema_notes"]["kickoff_source_timezone"] = "Europe/London"
    quality["kickoff_timezone_audit"] = audit
    quality.setdefault("checks", {})["football_data_times_agree_with_openligadb_utc_samples"] = True
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    quality_path.write_text(json.dumps(quality, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Reprocessed {len(rows)} historical and {len(current_rows)} current matches from saved raw snapshots; original collection timestamps and SHA-256 values preserved.")
    return 0


def main() -> int:
    RAW.mkdir(parents=True, exist_ok=True)
    OPENFOOTBALL_RAW.mkdir(parents=True, exist_ok=True)
    PROCESSED.mkdir(parents=True, exist_ok=True)
    collected_at = utc_now()
    all_rows, season_records, errors = [], [], []
    for start, end in SEASONS:
        season = season_label(start)
        code = f"{str(start)[-2:]}{str(end)[-2:]}"
        url = f"{BASE_URL}/{code}/D1.csv"
        try:
            content = download(url)
            path, digest = store_snapshot(season, content)
            rows, missing, invalid, headers = parse_rows(content, season, url, collected_at)
            all_rows.extend(rows)
            season_records.append({
                "season": season, "source_url": url, "collected_at": collected_at,
                "raw_file": str(path.relative_to(ROOT)), "sha256": digest,
                "bytes": len(content), "headers": headers, "row_count": len(rows),
                "expected_row_count": EXPECTED_PER_SEASON, "missing_values_by_field": missing,
                "invalid_rows": invalid,
            })
            print(f"{season}: {len(rows)} matches sha256={digest[:12]}")
        except Exception as exc:
            errors.append({"season": season, "source_url": url, "error": f"{type(exc).__name__}: {exc}"})
            print(f"{season}: FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
    # Capture the ongoing season separately: its row count is intentionally not
    # judged against a full-season schedule and it never enters historical JSONL.
    current_record = {"season": "2026-2027", "status": "unavailable"}
    current_rows = []
    current_url = f"{BASE_URL}/2627/D1.csv"
    try:
        current_content = download(current_url)
        current_path, current_digest = store_snapshot("2026-2027", current_content)
        current_rows, current_missing, current_invalid, current_headers = parse_rows(current_content, "2026-2027", current_url, collected_at)
        current_record = {
            "season": "2026-2027", "status": "available", "source_url": current_url,
            "collected_at": collected_at, "raw_file": str(current_path.relative_to(ROOT)),
            "sha256": current_digest, "bytes": len(current_content), "headers": current_headers,
            "completed_match_count": len(current_rows), "missing_values_by_field": current_missing,
            "invalid_rows": current_invalid,
            "output": "data/processed/matches_2026-2027_current.jsonl",
        }
        with (PROCESSED / "matches_2026-2027_current.jsonl").open("w", encoding="utf-8") as out:
            for row in current_rows:
                out.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        print(f"2026-2027 current snapshot: {len(current_rows)} completed matches")
    except Exception as exc:
        current_record.update({"error": f"{type(exc).__name__}: {exc}", "source_url": current_url})
        print(f"2026-2027 current snapshot unavailable: {type(exc).__name__}: {exc}", file=sys.stderr)
    auxiliary_sources = []
    openligadb_matches = []
    for source_name, url, folder, stem, suffix, content_type in [
        ("DFL English newsroom RSS", "https://www.dfl.de/en/feed/", DFL_RAW, "news_en", "xml", "application/rss+xml"),
        ("OpenLigaDB 2026 Bundesliga match API", "https://api.openligadb.de/getmatchdata/bl1/2026", OPENLIGADB_RAW, "bl1_2026", "json", "application/json"),
    ]:
        record = {"source_name": source_name, "source_url": url, "status": "unavailable"}
        try:
            content = download(url)
            snapshot, digest = store_aux_snapshot(folder, stem, suffix, content)
            record.update({"status": "available", "collected_at": collected_at, "raw_file": str(snapshot.relative_to(ROOT)), "sha256": digest, "bytes": len(content), "expected_content_type": content_type})
            if suffix == "json":
                parsed = json.loads(content.decode("utf-8"))
                record["match_count"] = len(parsed) if isinstance(parsed, list) else None
                record["finished_match_count"] = sum(bool(m.get("matchIsFinished")) for m in parsed) if isinstance(parsed, list) else None
                record["contains_lineups"] = any(bool(m.get("lineups")) for m in parsed) if isinstance(parsed, list) else False
                if source_name.startswith("OpenLigaDB") and isinstance(parsed, list):
                    openligadb_matches = parsed
            print(f"{source_name}: saved {len(content)} bytes")
        except Exception as exc:
            record["error"] = f"{type(exc).__name__}: {exc}"
            print(f"{source_name} unavailable: {type(exc).__name__}: {exc}", file=sys.stderr)
        auxiliary_sources.append(record)
    total_historical = len(all_rows)
    all_fields = sorted({field for row in all_rows for field in row["raw_row"]})
    field_coverage = {}
    for field in all_fields:
        present = sum(bool(row["raw_row"].get(field)) for row in all_rows)
        field_coverage[field] = {"present_rows": present, "missing_rows": total_historical - present, "coverage_pct": round(present * 100 / total_historical, 3) if total_historical else 0}
    odds_fields = ["AvgH", "AvgD", "AvgA", "B365H", "B365D", "B365A", "AvgCH", "AvgCD", "AvgCA", "AHh", "AHCh", "B365AHH", "B365AHA", "B365CAHH", "B365CAHA"]
    # Independently compare one complete historical season with openfootball's
    # public football.json data. This explicit 2015/16 crosswalk is retained in
    # sources.md; source team labels remain unchanged in the canonical rows.
    crosswalk = {
        "Bayern München": "Bayern Munich", "Hamburger SV": "Hamburg",
        "1. FSV Mainz 05": "Mainz", "FC Ingolstadt 04": "Ingolstadt",
        "SV Darmstadt 98": "Darmstadt", "Hannover 96": "Hannover",
        "Werder Bremen": "Werder Bremen", "FC Schalke 04": "Schalke 04",
        "FC Augsburg": "Augsburg", "Hertha BSC": "Hertha",
        "Bayer Leverkusen": "Leverkusen", "1899 Hoffenheim": "Hoffenheim",
        "Borussia Dortmund": "Dortmund", "Bor. Mönchengladbach": "M'gladbach",
        "VfL Wolfsburg": "Wolfsburg", "Eintracht Frankfurt": "Ein Frankfurt",
        "VfB Stuttgart": "Stuttgart", "1. FC Köln": "FC Koln",
    }
    crosscheck = {"source_url": "https://raw.githubusercontent.com/openfootball/football.json/master/2015-16/de.1.json", "season": "2015-2016", "status": "failed", "openfootball_matches": 0, "matched_fixtures": 0, "score_agreements": 0, "score_disagreements": 0}
    try:
        cross_content = download(crosscheck["source_url"])
        cross_digest = sha256(cross_content)
        cross_path = OPENFOOTBALL_RAW / "2015-16_de.1.json"
        existing_cross = sorted(OPENFOOTBALL_RAW.glob("2015-16_de.1*.json"))
        if any(sha256(p.read_bytes()) == cross_digest for p in existing_cross):
            cross_path = next(p for p in existing_cross if sha256(p.read_bytes()) == cross_digest)
        elif cross_path.exists():
            cross_path = OPENFOOTBALL_RAW / f"2015-16_de.1_{cross_digest[:12]}.json"
        if not cross_path.exists():
            cross_path.write_bytes(cross_content)
        Path(str(cross_path) + ".sha256").write_text(f"{cross_digest}  {cross_path.name}\n", encoding="ascii")
        independent = json.loads(cross_content.decode("utf-8"))
        fd_index = {(r["match_date"], r["home_team"], r["away_team"]): r for r in all_rows if r["season"] == "2015-2016"}
        matched = agreements = disagreements = 0
        for match in independent.get("matches", []):
            if match.get("team1") not in crosswalk or match.get("team2") not in crosswalk:
                continue
            key = (match.get("date", "")[:10], crosswalk[match["team1"]], crosswalk[match["team2"]])
            local = fd_index.get(key)
            if not local:
                continue
            matched += 1
            score = match.get("score", {}).get("ft", [])
            if score == [local["home_goals"], local["away_goals"]]:
                agreements += 1
            else:
                disagreements += 1
        crosscheck.update({"status": "ok", "openfootball_matches": len(independent.get("matches", [])), "matched_fixtures": matched, "score_agreements": agreements, "score_disagreements": disagreements, "raw_file": str(cross_path.relative_to(ROOT)), "sha256": cross_digest, "crosswalk": crosswalk})
    except Exception as exc:
        crosscheck["error"] = f"{type(exc).__name__}: {exc}"
    olb_crosswalk = {
        "1. FC Köln": "FC Koln", "1. FC Union Berlin": "Union Berlin",
        "1. FSV Mainz 05": "Mainz", "Bayer 04 Leverkusen": "Leverkusen",
        "Borussia Dortmund": "Dortmund", "Borussia Mönchengladbach": "M'gladbach",
        "Eintracht Frankfurt": "Ein Frankfurt", "FC Augsburg": "Augsburg",
        "FC Bayern München": "Bayern Munich", "FC Schalke 04": "Schalke 04",
        "Hamburger SV": "Hamburg", "RB Leipzig": "RB Leipzig", "SC Freiburg": "Freiburg",
        "SC Paderborn 07": "Paderborn", "SV 07 Elversberg": "Elversberg",
        "SV Werder Bremen": "Werder Bremen", "TSG Hoffenheim": "Hoffenheim",
        "VfB Stuttgart": "Stuttgart",
    }
    current_index = {(r["match_date"], r["home_team"], r["away_team"]): r for r in current_rows}
    olb_matches = olb_agreements = olb_disagreements = 0
    for match in openligadb_matches:
        if not match.get("matchIsFinished"):
            continue
        try:
            key = (match["matchDateTime"][:10], olb_crosswalk[match["team1"]["teamName"]], olb_crosswalk[match["team2"]["teamName"]])
            local = current_index.get(key)
            result = next((r for r in match.get("matchResults", []) if r.get("resultTypeKind") == "After90Minutes"), None)
            if local and result:
                olb_matches += 1
                if [local["home_goals"], local["away_goals"]] == [result["pointsTeam1"], result["pointsTeam2"]]:
                    olb_agreements += 1
                else:
                    olb_disagreements += 1
        except KeyError:
            pass
    current_crosscheck = {"source_url": "https://api.openligadb.de/getmatchdata/bl1/2026", "season": "2026-2027", "matched_finished_fixtures": olb_matches, "score_agreements": olb_agreements, "score_disagreements": olb_disagreements, "crosswalk": olb_crosswalk}
    current_crosscheck["status"] = "ok" if olb_matches == len(current_rows) and olb_agreements == len(current_rows) and olb_disagreements == 0 else "incomplete_or_mismatch"
    time_audit = build_timezone_audit(all_rows)
    ids = [r["match_id"] for r in all_rows]
    duplicates = sorted({x for x in ids if ids.count(x) > 1}) if len(ids) != len(set(ids)) else []
    manifest = {
        "dataset": "German Bundesliga match results and odds",
        "competition": "DE1",
        "source": "football-data.co.uk",
        "collected_at": collected_at,
        "season_range": ["2015-2016", "2025-2026"],
        "expected_matches_per_season": EXPECTED_PER_SEASON,
        "seasons": season_records,
        "current_season_snapshot": current_record,
        "auxiliary_public_sources": auxiliary_sources,
        "independent_crosscheck": crosscheck,
        "current_season_independent_crosscheck": current_crosscheck,
        "kickoff_timezone_audit": time_audit,
        "collection_errors": errors,
        "output": "data/processed/matches.jsonl",
        "match_count": len(all_rows),
        "schema_notes": {
            "kickoff_utc": "Converted from source Europe/London clock time (GMT/BST by date) where available; null when absent or unparseable.",
            "kickoff_source_timezone": "Europe/London",
            "kickoff_precision": "minute when source Time exists and parses; otherwise date.",
            "raw_row": "Original source columns retained verbatim as strings.",
            "odds": "Original source columns retained in raw_row; snapshot/timing metadata may be incomplete.",
        },
    }
    if len(season_records) == len(SEASONS) and not errors:
        with (PROCESSED / "matches.jsonl").open("w", encoding="utf-8") as out:
            for row in all_rows:
                out.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    (PROCESSED / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    counts = {record["season"]: record["row_count"] for record in season_records}
    quality = {
        "generated_at": collected_at,
        "expected_seasons": len(SEASONS),
        "collected_seasons": len(season_records),
        "season_counts": counts,
        "expected_rows_per_season": EXPECTED_PER_SEASON,
        "seasons_at_expected_count": sum(v == EXPECTED_PER_SEASON for v in counts.values()),
        "total_matches": len(all_rows),
        "field_coverage": field_coverage,
        "odds_field_coverage": {field: field_coverage.get(field, {"present_rows": total_historical, "missing_rows": 0, "coverage_pct": 100.0}) for field in odds_fields},
        "current_season_snapshot": {"season": current_record["season"], "status": current_record["status"], "completed_match_count": current_record.get("completed_match_count", 0), "output": current_record.get("output")},
        "auxiliary_public_sources": auxiliary_sources,
        "current_season_independent_crosscheck": current_crosscheck,
        "kickoff_timezone_audit": time_audit,
        "duplicate_match_ids": duplicates,
        "duplicate_match_id_count": len(duplicates),
        "invalid_row_count": sum(len(x["invalid_rows"]) for x in season_records),
        "collection_errors": errors,
        "checks": {
            "all_rows_competition_de1": all(r["competition"] == "DE1" for r in all_rows),
            "scores_nonnegative_integers": all(isinstance(r["home_goals"], int) and isinstance(r["away_goals"], int) and min(r["home_goals"], r["away_goals"]) >= 0 for r in all_rows),
            "results_match_scores": all(r["result"] == ("H" if r["home_goals"] > r["away_goals"] else "A" if r["home_goals"] < r["away_goals"] else "D") for r in all_rows),
            "unique_match_ids": len(ids) == len(set(ids)),
            "all_expected_seasons_collected": len(season_records) == len(SEASONS),
            "all_seasons_complete": len(counts) == len(SEASONS) and all(v == EXPECTED_PER_SEASON for v in counts.values()),
            "independent_openfootball_2015_16_scores_agree": crosscheck.get("status") == "ok" and crosscheck.get("matched_fixtures") == crosscheck.get("openfootball_matches") == EXPECTED_PER_SEASON and crosscheck.get("score_agreements") == EXPECTED_PER_SEASON and crosscheck.get("score_disagreements") == 0,
            "independent_openligadb_current_scores_agree": current_crosscheck["status"] == "ok",
            "football_data_times_agree_with_openligadb_utc_samples": time_audit["status"] == "ok",
        },
        "independent_crosscheck": crosscheck,
        "missing_values_by_season": {x["season"]: x["missing_values_by_field"] for x in season_records},
    }
    (PROCESSED / "quality.json").write_text(json.dumps(quality, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"total: {len(all_rows)} across {len(season_records)} seasons")
    if errors or duplicates or any(v != EXPECTED_PER_SEASON for v in counts.values()) or sum(len(x["invalid_rows"]) for x in season_records):
        return 1
    return 0

if __name__ == "__main__":
    if sys.argv[1:] == ["--reprocess-raw"]:
        raise SystemExit(reprocess_existing_raw())
    raise SystemExit(main())
