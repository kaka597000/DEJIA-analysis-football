"""Expanding-time evaluation: each complete season is predicted by a past-only fit."""
import json
import math
from pathlib import Path
from .model import fit, predict


def metrics(rows):
    n=len(rows)
    if not n:
        return {'matches':0}
    losses=[-math.log(max(1e-12,r['probabilities'][r['actual_result']])) for r in rows]
    brier=[sum((p-(s==r['actual_result']))**2 for s,p in r['probabilities'].items()) for r in rows]
    hit=sum(max(r['probabilities'],key=r['probabilities'].get)==r['actual_result'] for r in rows)
    score_hit=sum(r['top_score']==r['actual_score'] for r in rows)
    favorite_cases=[]
    for r in rows:
        p=r['probabilities']; favorite=max(('H','A'),key=lambda s:p[s])
        ordered=sorted(p.values(),reverse=True)
        if p[favorite]>=.45 and p[favorite]==ordered[0] and ordered[0]-ordered[1]>=.1:
            underdog='A' if favorite=='H' else 'H'
            favorite_cases.append((1-p[favorite],int(r['actual_result']!=favorite),p[underdog],int(r['actual_result']==underdog)))
    calibration=[]
    for low in (0,.1,.2,.3,.4,.5,.6,.7,.8,.9):
        # Exact bins through integer index, without overlapping boundary cases.
        cells=[(p,int(s==r['actual_result'])) for r in rows for s,p in r['probabilities'].items() if min(9,int(p*10))==round(low*10)]
        if cells:
            calibration.append({'from':low,'to':round(low+.1,1),'class_observations':len(cells),
                                'mean_probability':sum(p for p,y in cells)/len(cells),
                                'observed_rate':sum(y for p,y in cells)/len(cells)})
    return {'matches':n,'correct_1x2':hit,'accuracy_1x2':hit/n,'top_score_hits':score_hit,'top_score_accuracy':score_hit/n,
            'log_loss':sum(losses)/n,'multiclass_brier_sum':sum(brier)/n,'reliability_bins':calibration,
            'favorite_nonwin':{'eligible_favorites':len(favorite_cases),
                               'actual_nonwins':sum(y for p,y,q,z in favorite_cases),
                               'actual_underdog_wins':sum(z for p,y,q,z in favorite_cases),
                               'binary_brier':sum((p-y)**2 for p,y,q,z in favorite_cases)/len(favorite_cases) if favorite_cases else None,
                               'underdog_win_binary_brier':sum((q-z)**2 for p,y,q,z in favorite_cases)/len(favorite_cases) if favorite_cases else None},
            'upset_rule_validation':'not_evaluated_without_timestamped_multisource_evidence'}


def run(rows, output):
    output=Path(output); output.mkdir(parents=True,exist_ok=True)
    seasons=sorted({r['season'] for r in rows if r['season']>='2022-2023'})
    all_predictions=[]; summaries=[]
    for season in seasons:
        group=[r for r in rows if r['season']==season]
        if len(group)!=306:
            summaries.append({'season':season,'status':'excluded_incomplete_season','matches':len(group)})
            continue
        cutoff=min(r['match_date'] for r in group)
        trained=fit(rows,cutoff)
        prefix=[r for r in rows if r['match_date']<cutoff]
        total=len(prefix)
        priors={s:sum(r['result']==s for r in prefix)/total for s in ('H','D','A')}
        predictions=[]; prior_preds=[]; market_preds=[]
        for r in sorted(group,key=lambda r:(r['match_date'],r['match_id'])):
            p=predict(trained,r['home_team'],r['away_team'])
            entry={'match_id':r['match_id'],'season':season,'match_date':r['match_date'],
                   'training_cutoff_date':cutoff,'training_last_date':trained['training_last_date'],
                   'training_sha256':trained['training_sha256'],'probabilities':p['probabilities'],
                   'top_score':p['top_scores'][0]['score'],'actual_result':r['result'],
                   'actual_score':f"{r['home_goals']}-{r['away_goals']}"}
            predictions.append(entry)
            prior_preds.append(dict(entry,probabilities=priors))
            raw=json.loads(r['raw_row']) if isinstance(r['raw_row'],str) else r['raw_row']
            for keys in (('AvgH','AvgD','AvgA'),('B365H','B365D','B365A')):
                try:
                    odds=[float(raw[k]) for k in keys]
                    if not all(math.isfinite(x) and x>1 for x in odds):
                        continue
                    inv=[1/x for x in odds]; norm=sum(inv)
                    market_preds.append(dict(entry,probabilities=dict(zip(('H','D','A'),[x/norm for x in inv]))))
                    break
                except (KeyError,ValueError,TypeError):
                    continue
        all_predictions.extend(predictions)
        # Raw history odds have no observation times; comparison is retrospective, separate from model.
        summaries.append({'season':season,'split':'validation' if season=='2022-2023' else 'test',
                          'model':metrics(predictions),'historical_frequency_baseline':metrics(prior_preds),
                          'market_retrospective_unverified_timestamps':metrics(market_preds)})
    validation=[p for p in all_predictions if p['season']=='2022-2023']
    test=[p for p in all_predictions if p['season']>'2022-2023']
    report={'model':'DE1-POISSON-0.1','method':'season-frozen chronological fits; previous four years, no test tuning',
            'status':'retrospective_reconstruction','validation':metrics(validation),'test':metrics(test),'seasons':summaries,
            'limitations':['模型只用历史赛果；没有验证外部LLM共识或冷门规则的准确率。',
                           '历史赔率缺少发布时间，不进入严格赛前预测；赔率对照仅为回顾性。',
                           '本次历史数据为当前获取，没有当时冻结存档；不报告收益或宣传性命中率。']}
    (output/'backtest.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    (output/'predictions.jsonl').write_text('\n'.join(json.dumps(r,ensure_ascii=False) for r in all_predictions)+'\n',encoding='utf-8')
    return report
