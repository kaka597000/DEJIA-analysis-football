"""Evidence-based upset screening. Scores are rules, never calibrated probabilities."""
from .timeutil import utc

RULE_VERSION = 'DE1-UPSET-RULES-0.1'
MAX_AGE_HOURS = {'euro':24,'asian':24,'funds':12,'lineup':24,'news':168,'context':168}


def implied(payload):
    inv = {s:1/payload[s] for s in ('H','D','A')}
    z = sum(inv.values())
    return {s:p/z for s,p in inv.items()}


def screen(prediction, evidence, as_of):
    cutoff = utc(as_of)
    fresh = [e for e in evidence if e['trust'] >= .7 and
             0 <= (cutoff-utc(e['published_at'])).total_seconds() <= MAX_AGE_HOURS[e['kind']]*3600]
    euro = [e for e in fresh if e['kind']=='euro']
    latest_euro = max(euro,key=lambda e:(utc(e['published_at']),utc(e['observed_at']))) if euro else None
    market = implied(latest_euro['payload']) if latest_euro else None
    reference = market or prediction['probabilities']
    favorite = max(('H','A'), key=lambda s:reference[s])
    ordered = sorted(reference.values(),reverse=True)
    strong = reference[favorite] >= .45 and reference[favorite] == ordered[0] and ordered[0]-ordered[1] >= .1
    available = {e['kind'] for e in fresh}
    signals, counters = [], []
    def add(family, title, ids, points, counter=False):
        (counters if counter else signals).append({'family':family,'description':title,'evidence_ids':ids,'rule_points':points})
    if latest_euro:
        # Movement requires same bookmaker and temporally distinct, trusted snapshots.
        same = [e for e in evidence if e['kind']=='euro' and e['trust']>=.7 and
                e['payload']['bookmaker']==latest_euro['payload']['bookmaker'] and
                utc(e['published_at']) <= utc(latest_euro['published_at']) and
                (utc(latest_euro['published_at'])-utc(e['published_at'])).total_seconds() <= 7*86400]
        first = min(same,key=lambda e:utc(e['published_at']))
        delta = market[favorite]-implied(first['payload'])[favorite]
        if utc(first['published_at']) < utc(latest_euro['published_at']) and abs(delta)>=.03:
            add('market',f'去水欧赔热门概率变化 {delta:+.3f}',[first['evidence_id'],latest_euro['evidence_id']],20,delta>0)
        gap = market[favorite]-prediction['probabilities'][favorite]
        if gap>=.08:
            add('market',f'市场热门概率比独立模型高 {gap:.3f}',[latest_euro['evidence_id']],15)
        # Actual money share only; require same venue as the market reference.
        flows = [e for e in fresh if e['kind']=='funds' and e['payload']['side']==favorite and
                 e['payload']['bookmaker']==latest_euro['payload']['bookmaker']]
        if flows:
            f = max(flows,key=lambda e:utc(e['published_at']))
            if abs((utc(f['published_at'])-utc(latest_euro['published_at'])).total_seconds())<=3600:
                gap = f['payload']['amount_share']-market[favorite]
                if gap>=.15:
                    add('funds',f'同一场所真实资金占比高于去水概率 {gap:.3f}；不代表操盘', [f['evidence_id'],latest_euro['evidence_id']],20)
    asian = [e for e in fresh if e['kind']=='asian']
    if asian:
        last = max(asian,key=lambda e:utc(e['published_at']))
        same = [e for e in evidence if e['kind']=='asian' and e['trust']>=.7 and
                e['payload']['bookmaker']==last['payload']['bookmaker'] and
                utc(e['published_at'])<=utc(last['published_at']) and
                (utc(last['published_at'])-utc(e['published_at'])).total_seconds()<=7*86400]
        first = min(same,key=lambda e:utc(e['published_at']))
        delta = last['payload']['home_handicap']-first['payload']['home_handicap']
        if favorite=='A':
            delta = -delta
        if utc(first['published_at']) < utc(last['published_at']) and abs(delta)>=.25:
            add('market',f'热门方亚盘让步减弱 {delta:+.2f} 球；已统一主队盘口符号', [first['evidence_id'],last['evidence_id']],20,delta<0)
    # Revisions to the same source replace earlier assertions for the current view.
    contextual = {}
    for e in fresh:
        if e['kind'] in ('news','lineup','context'):
            key = (e['source_url'],e['payload']['side'])
            if key not in contextual or (utc(e['published_at']),utc(e['observed_at'])) > (utc(contextual[key]['published_at']),utc(contextual[key]['observed_at'])):
                contextual[key] = e
    for e in contextual.values():
        p = e['payload']
        if p['severity']!='major' or p['direction']=='neutral':
            continue
        adverse = (p['side']==favorite and p['direction']=='negative') or (p['side']!=favorite and p['direction']=='positive')
        family = 'team' if e['kind'] in ('news','lineup') else 'context'
        add(family,p['summary'],[e['evidence_id']],20,not adverse)
    # Correlated Asian/European movement, repeated news and lineup news count once per family.
    families = {s['family'] for s in signals}
    points = sum(max(s['rule_points'] for s in signals if s['family']==f) for f in families)
    opposition = {s['family'] for s in counters}
    points = max(0,min(100,points-10*len(opposition)))
    coverage = len(available)/6
    if not strong:
        level = 'no_clear_favorite'
    elif len(available)<3:
        level = 'insufficient_evidence'
    elif points>=50 and len(families)>=3:
        level = 'high_research_risk'
    elif points>=20:
        level = 'watch'
    else:
        level = 'no_elevated_signal'
    probs = prediction['probabilities']
    underdog = 'A' if favorite=='H' else 'H'
    return {'version':RULE_VERSION,'status':'uncalibrated_rules','favorite':favorite if strong else None,
            'favorite_basis':'de_vig_european_odds' if market else 'poisson_model_only',
            'model_favorite_nonwin_probability':1-probs[favorite] if strong else None,
            'model_underdog_win_probability':probs[underdog] if strong else None,
            'risk_score':points,'risk_level':level,'signals':signals,'counterevidence':counters,
            'signal_families':sorted(families),'coverage':coverage,
            'missing_dimensions':sorted({'euro','asian','funds','news','lineup','context'}-available),
            'note':'冷门定义区分热门不胜（含平局）与弱队获胜；规则分数不等于发生概率。无真实资金不能由赔率推断资金。'}
