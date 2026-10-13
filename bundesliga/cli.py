import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import urllib.error
from . import model, store
from .backtest import run
from .feeds import fixtures, rss
from .pipeline import analyze, render_markdown


def write_json(path, value):
    p=Path(path); p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')


def audit(conn):
    rows=store.history(conn)
    seasons={}
    issues=[]
    for s in sorted({r['season'] for r in rows}):
        group=[r for r in rows if r['season']==s]
        teams={r[k] for r in group for k in ('home_team','away_team')}
        full=len(group)==306
        counts={t:{'home':sum(r['home_team']==t for r in group),'away':sum(r['away_team']==t for r in group)} for t in teams}
        if full and (len(teams)!=18 or any(c!={'home':17,'away':17} for c in counts.values())):
            issues.append({'season':s,'issue':'invalid_full_season_schedule_grain'})
        for r in group:
            try: store.validate_match(r)
            except (ValueError,KeyError,TypeError): issues.append({'match_id':r['match_id'],'issue':'invalid_match'})
        seasons[s]={'matches':len(group),'teams':len(teams),'complete':full,'team_counts':counts}
    integrity=conn.execute('PRAGMA integrity_check').fetchone()[0]
    foreign=list(conn.execute('PRAGMA foreign_key_check'))
    if integrity!='ok' or foreign:
        issues.append({'issue':'database_integrity'})
    return {'matches':len(rows),'seasons':seasons,'issues':issues,'integrity':integrity,
            'historical_odds':conn.execute('SELECT COUNT(*) FROM historical_odds').fetchone()[0],
            'evidence':conn.execute('SELECT COUNT(*) FROM evidence').fetchone()[0],
            'status':'pass' if not issues else 'fail'}


def main(argv=None):
    p=argparse.ArgumentParser(description='独立德甲研究数据库、模型、多智能体和冷门审查')
    p.add_argument('--db',default='data/bundesliga.sqlite')
    sub=p.add_subparsers(dest='command',required=True)
    imp=sub.add_parser('import-history'); imp.add_argument('path')
    sub.add_parser('audit')
    tr=sub.add_parser('train'); tr.add_argument('--cutoff',required=True); tr.add_argument('--output',default='models/poisson-v0.1.json')
    bt=sub.add_parser('backtest'); bt.add_argument('--output',default='reports/backtest')
    ev=sub.add_parser('import-evidence'); ev.add_argument('path')
    an=sub.add_parser('analyze'); an.add_argument('--fixture',required=True); an.add_argument('--as-of',required=True)
    an.add_argument('--model',default='models/poisson-v0.1.json'); an.add_argument('--agents',choices=('local','llm'),default='local')
    an.add_argument('--agent-config',default='config/agents.example.json'); an.add_argument('--output',default='reports/analysis')
    feed=sub.add_parser('collect-rss'); feed.add_argument('url'); feed.add_argument('--output',default='data/raw/rss')
    fs=sub.add_parser('collect-fixtures'); fs.add_argument('--output',default='data/processed/provider_fixtures.json')
    args=p.parse_args(argv)
    try:
        if args.command=='collect-rss':
            result=rss(args.url,args.output)
        elif args.command=='collect-fixtures':
            result=fixtures(args.output)
        else:
            conn=store.connect(args.db)
            try:
                if args.command=='import-history':
                    result={'inserted':store.import_history(conn,args.path)}
                elif args.command=='audit':
                    result=audit(conn)
                elif args.command=='train':
                    trained=model.fit(store.history(conn),args.cutoff); model.save(trained,args.output)
                    result={k:v for k,v in trained.items() if k not in ('attack','defence')}
                elif args.command=='backtest':
                    report=run(store.history(conn),args.output)
                    result={'validation':report['validation'],'test':report['test']}
                elif args.command=='import-evidence':
                    data=json.loads(Path(args.path).read_text(encoding='utf-8'))
                    if not isinstance(data,list):
                        raise ValueError('Evidence file must contain a JSON list')
                    # Validate the whole batch first; invalid files never partly import.
                    for r in data: store.validate_evidence(r)
                    result={'evidence_ids':[store.insert_evidence(conn,r) for r in data]}
                elif args.command=='analyze':
                    fx=json.loads(Path(args.fixture).read_text(encoding='utf-8'))
                    config=json.loads(Path(args.agent_config).read_text(encoding='utf-8')) if args.agents=='llm' else None
                    report=analyze(conn,fx,args.as_of,model.load(args.model),args.agents,config)
                    write_json(args.output+'.json',report)
                    Path(args.output+'.md').write_text(render_markdown(report),encoding='utf-8')
                    result={'run_id':report['run_id'],'status':report['status'],'probabilities':report['baseline']['probabilities'],
                            'upset':report['upset']['risk_level'],'agents':report['agents']['status'],'output':args.output+'.json'}
            finally:
                conn.close()
        print(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False))
        return 1 if result.get('status')=='fail' else 0
    except urllib.error.HTTPError as e:
        print(f'HTTP {e.code}; request failed, no fallback data generated',file=sys.stderr)
    except (ValueError,KeyError,TypeError,OSError,urllib.error.URLError) as e:
        print(f'{type(e).__name__}: {e}',file=sys.stderr)
    return 2


if __name__=='__main__':
    raise SystemExit(main())
