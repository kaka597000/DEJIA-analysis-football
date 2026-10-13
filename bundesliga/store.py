"""SQLite event store: historical outcomes and immutable pre-match evidence."""
import hashlib
import json
import math
import sqlite3
from pathlib import Path
from .timeutil import utc, stamp

KINDS = {"euro", "asian", "funds", "news", "lineup", "context"}
SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS matches (
 match_id TEXT PRIMARY KEY, competition TEXT NOT NULL CHECK(competition='DE1'),
 season TEXT NOT NULL, match_date TEXT NOT NULL, kickoff_utc TEXT,
 kickoff_precision TEXT NOT NULL, home_team TEXT NOT NULL, away_team TEXT NOT NULL,
 home_goals INTEGER NOT NULL CHECK(home_goals>=0), away_goals INTEGER NOT NULL CHECK(away_goals>=0),
 result TEXT NOT NULL CHECK(result IN ('H','D','A')), source_url TEXT NOT NULL,
 collected_at TEXT NOT NULL, raw_row TEXT NOT NULL,
 UNIQUE(season,match_date,home_team,away_team), CHECK(home_team<>away_team)
);
CREATE INDEX IF NOT EXISTS matches_date ON matches(match_date);
CREATE TABLE IF NOT EXISTS historical_odds (
 match_id TEXT NOT NULL REFERENCES matches(match_id), bookmaker TEXT NOT NULL,
 kind TEXT NOT NULL, stage TEXT NOT NULL, payload TEXT NOT NULL,
 quote_at TEXT, collected_at TEXT NOT NULL,
 timing_status TEXT NOT NULL DEFAULT 'unverified_historical_snapshot',
 PRIMARY KEY(match_id,bookmaker,kind,stage)
);
CREATE TABLE IF NOT EXISTS evidence (
 evidence_id TEXT PRIMARY KEY, match_id TEXT NOT NULL, kind TEXT NOT NULL,
 source_url TEXT NOT NULL, published_at TEXT NOT NULL, observed_at TEXT NOT NULL,
 trust REAL NOT NULL CHECK(trust>=0 AND trust<=1), payload TEXT NOT NULL,
 inserted_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS evidence_fixture ON evidence(match_id,kind,published_at,observed_at);
CREATE TABLE IF NOT EXISTS runs (
 run_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, match_id TEXT NOT NULL,
 as_of TEXT NOT NULL, report TEXT NOT NULL
);
"""


def connect(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def validate_match(row):
    if row['competition'] != 'DE1':
        raise ValueError('Only Bundesliga DE1 is accepted')
    h, a = row['home_goals'], row['away_goals']
    if type(h) is not int or type(a) is not int or min(h, a) < 0:
        raise ValueError('Invalid goals')
    expected = 'H' if h > a else 'A' if a > h else 'D'
    if row['result'] != expected or row['home_team'] == row['away_team']:
        raise ValueError('Invalid teams/result')
    utc(row['collected_at'])
    if row.get('kickoff_utc'):
        utc(row['kickoff_utc'])


def import_history(conn, path):
    count = 0
    with conn:
        for line in Path(path).read_text(encoding='utf-8').splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            validate_match(r)
            existing = conn.execute('SELECT * FROM matches WHERE match_id=?', (r['match_id'],)).fetchone()
            if existing:
                for key in ('season', 'match_date', 'home_team', 'away_team', 'home_goals', 'away_goals', 'result'):
                    if existing[key] != r[key]:
                        raise ValueError(f"Historical conflict {r['match_id']} {key}; retain snapshots and review")
                archive_odds(conn, r)
                continue
            cols = ['match_id','competition','season','match_date','kickoff_utc','kickoff_precision',
                    'home_team','away_team','home_goals','away_goals','result','source_url','collected_at']
            values = [r.get(k, 'date' if k == 'kickoff_precision' else None) for k in cols]
            conn.execute(f"INSERT INTO matches ({','.join(cols)},raw_row) VALUES ({','.join('?' for _ in range(14))})",
                         values + [json.dumps(r['raw_row'], ensure_ascii=False)])
            count += 1
            archive_odds(conn, r)
    return count


def archive_odds(conn, r):
    raw = r['raw_row']
    for bookmaker, prefix in (('Bet365','B365'),('market_average','Avg')):
        for stage, c in (('non_closing',''),('closing','C')):
            for kind in ('euro','asian'):
                try:
                    if kind == 'euro':
                        payload = {s:float(raw[prefix+c+s]) for s in ('H','D','A')}
                        valid = all(math.isfinite(v) and v>1 for v in payload.values())
                    else:
                        payload = {'home_handicap':float(raw['AH'+c+'h']),
                                   'home_odds':float(raw[prefix+c+'AHH']),
                                   'away_odds':float(raw[prefix+c+'AHA'])}
                        valid = all(math.isfinite(v) for v in payload.values()) and min(payload['home_odds'],payload['away_odds'])>1
                    if valid:
                        conn.execute('INSERT OR IGNORE INTO historical_odds (match_id,bookmaker,kind,stage,payload,quote_at,collected_at) VALUES (?,?,?,?,?,NULL,?)',
                                     (r['match_id'],bookmaker,kind,stage,json.dumps(payload),r['collected_at']))
                except (KeyError,ValueError,TypeError):
                    continue


def history(conn, before_date=None):
    sql, args = 'SELECT * FROM matches', []
    if before_date:
        sql += ' WHERE match_date < ?'
        args.append(before_date)
    return [dict(r) for r in conn.execute(sql+' ORDER BY match_date,match_id', args)]


def positive(value, allow_zero=False):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
        raise ValueError('Expected a finite number')
    if value < 0 or (not allow_zero and value <= 0):
        raise ValueError('Invalid non-positive value')


def validate_evidence(r):
    if r['kind'] not in KINDS or not r['match_id'] or not r['source_url'].startswith('https://'):
        raise ValueError('Invalid kind/match/source')
    if utc(r['published_at']) > utc(r['observed_at']):
        raise ValueError('Observation cannot precede publication')
    if isinstance(r['trust'], bool) or not math.isfinite(r['trust']) or not 0 <= r['trust'] <= 1:
        raise ValueError('Invalid trust')
    p = r['payload']
    if not isinstance(p, dict):
        raise ValueError('Payload must be an object')
    if r['kind'] in ('euro', 'asian', 'funds') and not p.get('bookmaker'):
        raise ValueError('Bookmaker/exchange is required')
    if r['kind'] == 'euro':
        for side in ('H','D','A'):
            positive(p[side])
            if p[side] <= 1:
                raise ValueError('Decimal odds must exceed 1')
    if r['kind'] == 'asian':
        if not math.isfinite(p['home_handicap']) or abs(p['home_handicap']*4-round(p['home_handicap']*4)) > 1e-8:
            raise ValueError('Quarter-goal handicap required')
        for key in ('home_odds', 'away_odds'):
            positive(p[key])
            if p[key] <= 1:
                raise ValueError('Use decimal Asian odds')
    if r['kind'] == 'funds':
        if p.get('metric') != 'amount_share' or p.get('side') not in ('H','D','A') or not p.get('window') or not p.get('currency'):
            raise ValueError('Funds need actual amount share, side, window and currency')
        positive(p['amount_share'], allow_zero=True)
        positive(p['total_amount'])
        if p['amount_share'] > 1:
            raise ValueError('Funds share must be in [0,1]')
    if r['kind'] in ('news','lineup','context'):
        if p.get('side') not in ('H','A') or p.get('direction') not in ('positive','negative','neutral') or not p.get('summary'):
            raise ValueError('Context evidence needs side/direction/summary')
        if p.get('severity') not in ('minor','major'):
            raise ValueError('Explicit severity required')
    json.dumps(p, allow_nan=False)


def insert_evidence(conn, r):
    validate_evidence(r)
    # Content-derived IDs prevent replacement of old snapshots by revised reports.
    body = {k:r[k] for k in ('match_id','kind','source_url','published_at','observed_at','trust','payload')}
    eid = hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:24]
    with conn:
        conn.execute('INSERT OR IGNORE INTO evidence VALUES (?,?,?,?,?,?,?,?,?)',
                     (eid, r['match_id'], r['kind'], r['source_url'], utc(r['published_at']).isoformat(),
                      utc(r['observed_at']).isoformat(), r['trust'], json.dumps(r['payload'], ensure_ascii=False), stamp()))
    return eid


def evidence_at(conn, match_id, as_of):
    cutoff = utc(as_of)
    rows = conn.execute('SELECT * FROM evidence WHERE match_id=? ORDER BY published_at,evidence_id', (match_id,))
    return [dict(r, payload=json.loads(r['payload'])) for r in rows
            if utc(r['published_at']) <= cutoff and utc(r['observed_at']) <= cutoff]


def save_run(conn, report):
    body = json.dumps(report, ensure_ascii=False, sort_keys=True, allow_nan=False)
    rid = hashlib.sha256(body.encode()).hexdigest()[:24]
    with conn:
        conn.execute('INSERT OR IGNORE INTO runs VALUES (?,?,?,?,?)',
                     (rid, stamp(), report['fixture']['match_id'], report['as_of'], body))
    return rid
