# Bundesliga data sources and collection notes

This file records observed source access and data limits for the collection made on 2026-10-13 UTC. `data/processed/manifest.json` is the machine-readable inventory; its SHA-256 entries identify the exact immutable source snapshots. The collector stores a new content-addressed raw file when an upstream response changes and does not replace prior CSV/JSON/XML snapshots.

## Match history and odds

| Source | Access observed | Coverage captured | What it contains | Limits |
|---|---|---|---|---|
| [football-data.co.uk, German Bundesliga D1 CSV](https://www.football-data.co.uk/mmz4281/) | Direct HTTPS through the configured proxy; all archive endpoints returned data after retrying transient HTTP 503s | 2015/16–2025/26: 11 complete seasons, 3,366 matches. Each season has 306 rows. Separate 2026/27 snapshot: 45 completed matches at collection time. | Full-time scores, dates, local kickoff time where supplied, team labels, match statistics, 1X2 odds and Asian handicap fields where populated | The files are snapshots, and some odds fields are absent in older seasons. Closing odds columns (`*C`) have no per-quote publication timestamp; they cannot be treated as proven pre-kickoff information without further timing evidence. Quotes are prices, not wager volume or money flow. |
| [openfootball/football.json](https://github.com/openfootball/football.json), [2015/16 file](https://raw.githubusercontent.com/openfootball/football.json/master/2015-16/de.1.json) | Public raw GitHub file downloaded successfully | Independent full-season check for 2015/16: 306/306 fixtures and final scores agreed after an explicit source-name crosswalk | Date, teams, halftime and fulltime scores | Only used as a validation sample; it was not merged into the primary records. |
| [OpenLigaDB API](https://api.openligadb.de/getmatchdata/bl1/2026) | Public HTTPS endpoint returned HTTP 200 | 2026/27 season schedule snapshot: 306 fixtures, 45 marked finished; 45/45 completed scores matched the separate football-data current-season file | Fixture times, final/halftime results, goal events, and other match metadata | The response had no lineup field/data. Saved as a current-season snapshot; it is not mixed into 2015/16–2025/26 history. Check the API's current usage terms and rate guidance before repeated collection. |

The main normalized file is `data/processed/matches.jsonl`. Every record keeps the source row in `raw_row`; odds have not been coerced or removed. The historical source includes `AvgH/AvgD/AvgA`, `B365H/B365D/B365A`, closing fields such as `AvgCH/AvgCD/AvgCA`, and Asian handicap fields such as `AHh`, `AHCh`, `B365AHH/B365AHA`, and their closing variants when present. The closing marker says which quote family the source describes; it does not provide a precise observation timestamp.

In this captured history, the average-odds and Asian-line fields `AvgH`, `AvgD`, `AvgA`, `AvgCH`, `AvgCD`, `AvgCA`, `AHh`, and `AHCh` are present on 2,142 of 3,366 matches (63.636%). This reflects source schema/coverage changes over the archive; see per-season headers and missing counts in `manifest.json` and `quality.json`. `B365AHH` and `B365AHA` are each missing one additional row among the matches where those fields are generally present. Never fill these gaps with inferred prices.

### Cross-source team-name mapping used for validation only

The primary team spellings remain exactly as football-data supplied them. These explicit mappings were used only to join the independent validation sample; they are recorded here so there are no hidden or fuzzy team matches.

| openfootball label | football-data label |
|---|---|
| Bayern München | Bayern Munich |
| Hamburger SV | Hamburg |
| 1. FSV Mainz 05 | Mainz |
| FC Ingolstadt 04 | Ingolstadt |
| SV Darmstadt 98 | Darmstadt |
| Hannover 96 | Hannover |
| Werder Bremen | Werder Bremen |
| FC Schalke 04 | Schalke 04 |
| FC Augsburg | Augsburg |
| Hertha BSC | Hertha |
| Bayer Leverkusen | Leverkusen |
| 1899 Hoffenheim | Hoffenheim |
| Borussia Dortmund | Dortmund |
| Bor. Mönchengladbach | M'gladbach |
| VfL Wolfsburg | Wolfsburg |
| Eintracht Frankfurt | Ein Frankfurt |
| VfB Stuttgart | Stuttgart |
| 1. FC Köln | FC Koln |

The 2026/27 OpenLigaDB crosswalk is stored in the manifest alongside the comparison result. Team IDs/aliases should be moved into a canonical club dimension before merging more sources; do not use fuzzy text matching.

## News, lineups, markets, and money flow

| Data need | Source checked and access result | Practical use and limit |
|---|---|---|
| Current official news | [DFL English newsroom RSS](https://www.dfl.de/en/feed/) returned HTTP 200 as RSS and was saved verbatim at `data/raw/dfl/news_en.xml` with SHA-256. | A current feed suitable for low-frequency collection. It is not a historical archive of club news from 2015–2025; retain each future feed response as a separate dated snapshot. Respect the DFL site's terms for storage and reuse. |
| Official Bundesliga news pages | Direct request to [Bundesliga news](https://www.bundesliga.com/en/bundesliga/news) returned HTTP 403 in this runtime. | Do not bypass the block or scrape this page. The DFL RSS is the accessible official news path collected here. |
| Confirmed lineups | OpenLigaDB's current-season response was accessible but contains no lineup records. Bundesliga's news surface has matchday lineup articles, but the direct page request was blocked; no reusable official structured lineup feed was established in this collection. | Lineups are not in the database. Do not infer starting elevens from news text. A future feed/API needs explicit access terms and a timestamp showing when a lineup became public. |
| API-backed match data | [football-data.org documentation](https://www.football-data.org/documentation/quickstart) returned HTTP 200. The managed runtime reported the optional football-data.org credential state as `unknown`, not ready; no API key was read or used. | The public documentation is useful for evaluating a future licensed integration. No API data was collected. |
| Market prices | The historical football-data CSV provides pre-match/closing-style bookmaker prices in separate columns, with incomplete archive coverage and no precise quote timestamps. | Treat as odds only. Do not label odds movement as money flow, matched volume, liability, or betting sentiment. |
| Traded volume / exchange flow | [Betfair Historical Data](https://historicdata.betfair.com/) and [Betfair developer access guidance](https://support.developer.betfair.com/hc/en-us/articles/360002407712-How-can-I-access-the-Betfair-Historical-Data-service) describe exchange data that can contain traded volume. Access requires a registered account and purchase/entitlement for historical files. | No account, entitlement, purchase, or data download was used. There is no verified public, free Bundesliga historical money-flow feed in this collection. Money-flow fields remain absent rather than fabricated. |

## Time and modeling cautions

- The D1 `Time` field is UK local clock time. It is parsed as `Europe/London` (GMT/BST according to the match date) and converted to UTC. This was checked against independent OpenLigaDB local and UTC timestamps: Bayern–Hertha on 2019-08-16 is `19:30` in the D1 CSV, `20:30` CEST locally, and `18:30Z`; Bayern–Wolfsburg on 2019-12-21 is `14:30` in the CSV, `15:30` CET locally, and `14:30Z`; Bayern–Leipzig on 2025-08-22 is `19:30` in the CSV, `20:30` CEST locally, and `18:30Z`. The corresponding OpenLigaDB JSON snapshots and SHA-256 values are recorded in `data/processed/manifest.json` under `kickoff_timezone_audit`.
- `collected_at` is the UTC collection timestamp, not a market observation time.
- Historical odds are kept in the unmodified `raw_row`. Quote families marked as closing can include information unavailable earlier in the day; strict pre-match work must exclude them unless a timestamped source verifies when each value became available.
- The 2026/27 current file and raw RSS/API snapshots are separate from the completed 11-season historical result set.
