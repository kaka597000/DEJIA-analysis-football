"""Explicitly invoked public RSS/Atom and authenticated fixture adapters."""
import hashlib
import json
import os
from pathlib import Path
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from .timeutil import stamp, utc


def rss(url, output):
    if not url.startswith('https://'):
        raise ValueError('HTTPS source required')
    with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'BundesligaResearch/0.1'}),timeout=30) as r:
        body=r.read(5_000_001)
    if len(body)>5_000_000:
        raise ValueError('Feed exceeds size limit')
    digest=hashlib.sha256(body).hexdigest()
    dest=Path(output); dest.mkdir(parents=True,exist_ok=True)
    (dest/(digest+'.xml')).write_bytes(body)
    root=ET.fromstring(body)
    records=[]
    for item in root.findall('.//item'):
        published=item.findtext('pubDate')
        try:
            published=utc(parsedate_to_datetime(published).isoformat()).isoformat()
        except (ValueError,TypeError,AttributeError):
            published=None
        records.append({'title':item.findtext('title'),'url':item.findtext('link'),'published_at':published,
                        'observed_at':stamp(),'source_url':url,'raw_sha256':digest,
                        'status':'unmatched_unreviewed_news'})
    (dest/(digest+'.json')).write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf-8')
    return {'items':len(records),'raw_sha256':digest,'status':'requires_fixture_matching_and_evidence_review'}


def fixtures(output):
    key=next((os.environ.get(k) for k in ('football_data','Football_data_api','BUNDESLIGA_FOOTBALL_DATA_KEY') if os.environ.get(k)),None)
    if not key:
        raise ValueError('No football-data.org key configured')
    url='https://api.football-data.org/v4/competitions/BL1/matches?status=SCHEDULED'
    req=urllib.request.Request(url,headers={'X-Auth-Token':key})
    with urllib.request.urlopen(req,timeout=30) as r:
        data=json.load(r)
    result=[]
    for m in data.get('matches',[]):
        if m.get('competition',{}).get('code')!='BL1':
            raise ValueError('Provider returned non-Bundesliga fixture')
        result.append({'match_id':'fdorg-'+str(m['id']),'competition':'DE1','home_team':m['homeTeam']['name'],
                       'away_team':m['awayTeam']['name'],'kickoff_utc':utc(m['utcDate']).isoformat(),
                       'source_url':url,'observed_at':stamp(),'requires_canonical_team_alias_review':True})
    Path(output).parent.mkdir(parents=True,exist_ok=True)
    Path(output).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return {'fixtures':len(result),'status':'review_team_aliases_before_prediction'}
