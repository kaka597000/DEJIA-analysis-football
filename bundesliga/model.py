"""Recency-weighted, regularized Poisson attack/defence fitted only to DE1 outcomes."""
import hashlib
import json
import math
from datetime import date
from pathlib import Path
from .timeutil import utc

VERSION = 'DE1-POISSON-0.1'
PARAMS = {'half_life_days': 540, 'ridge': 0.03, 'iterations': 240, 'learning_rate': 1.0,
          'max_history_days': 1461}


def fit(rows, cutoff_date):
    cutoff = date.fromisoformat(cutoff_date)
    selected = [r for r in rows if 0 < (cutoff-date.fromisoformat(r['match_date'])).days <= PARAMS['max_history_days']]
    if len(selected) < 306 or any(r['competition'] != 'DE1' for r in selected):
        raise ValueError('Need at least 306 past Bundesliga matches')
    teams = sorted({r[t] for r in selected for t in ('home_team','away_team')})
    attack = dict.fromkeys(teams, 0.0)
    defence = dict.fromkeys(teams, 0.0)
    data = [(r, 2**(-(cutoff-date.fromisoformat(r['match_date'])).days/PARAMS['half_life_days'])) for r in selected]
    total = sum(w for _,w in data)
    # DE1 home and away mean goals are learned, not borrowed from other competitions.
    mu_h = math.log(max(.1, sum(w*r['home_goals'] for r,w in data)/total))
    mu_a = math.log(max(.1, sum(w*r['away_goals'] for r,w in data)/total))
    counts = {t:sum(w for r,w in data if t in (r['home_team'],r['away_team'])) for t in teams}
    def objective(hmu, amu, att, dfn):
        loss = 0.0
        for r,w in data:
            h,a = r['home_team'],r['away_team']
            hlog,alog = hmu+att[h]+dfn[a], amu+att[a]+dfn[h]
            if max(hlog, alog) > 20:
                return float('inf')
            loss += w*(math.exp(hlog)-r['home_goals']*hlog+math.exp(alog)-r['away_goals']*alog)
        loss += .5*PARAMS['ridge']*sum(counts[t]*(att[t]**2+dfn[t]**2) for t in teams)
        return loss/total
    losses = [objective(mu_h,mu_a,attack,defence)]
    for _ in range(PARAMS['iterations']):
        ga, gd = dict.fromkeys(teams, 0.0), dict.fromkeys(teams, 0.0)
        gh = gv = 0.0
        for r,w in data:
            h,a = r['home_team'],r['away_team']
            eh = math.exp(mu_h+attack[h]+defence[a])
            ea = math.exp(mu_a+attack[a]+defence[h])
            rh,ra = w*(eh-r['home_goals']), w*(ea-r['away_goals'])
            ga[h] += rh; ga[a] += ra
            gd[a] += rh; gd[h] += ra
            gh += rh; gv += ra
        # Backtracking prevents simultaneous attack/defence updates from oscillating.
        lr = PARAMS['learning_rate']
        for _trial in range(24):
            new_h,new_a = mu_h-lr*gh/total,mu_a-lr*gv/total
            new_att = {t:attack[t]-lr*(ga[t]/counts[t]+PARAMS['ridge']*attack[t]) for t in teams}
            new_def = {t:defence[t]-lr*(gd[t]/counts[t]+PARAMS['ridge']*defence[t]) for t in teams}
            candidate = objective(new_h,new_a,new_att,new_def)
            if candidate <= losses[-1]:
                break
            lr *= .5
        else:
            break
        mu_h,mu_a,attack,defence = new_h,new_a,new_att,new_def
        losses.append(candidate)
        if losses[-2]-losses[-1] < 1e-9:
            break
    ma,md = sum(attack.values())/len(teams),sum(defence.values())/len(teams)
    attack = {t:v-ma for t,v in attack.items()}
    defence = {t:v-md for t,v in defence.items()}
    mu_h += ma+md; mu_a += ma+md
    digest = hashlib.sha256(json.dumps([(r['match_id'],r['home_goals'],r['away_goals']) for r,_ in data],sort_keys=True).encode()).hexdigest()
    return {'version':VERSION,'competition':'DE1','parameters':PARAMS.copy(),'training_cutoff_date':cutoff_date,
            'training_last_date':max(r['match_date'] for r,_ in data),'training_matches':len(data),
            'training_sha256':digest,'mu_home':mu_h,'mu_away':mu_a,'attack':attack,'defence':defence,
            'training_available_at':max(utc(r['collected_at']) for r,_ in data).isoformat(),
            'optimizer':{'initial_loss':losses[0],'final_loss':losses[-1],'accepted_steps':len(losses)-1,'monotone':all(a>=b for a,b in zip(losses,losses[1:]))},
            'status':'research_baseline_uncalibrated'}


def predict(model, home_team, away_team):
    if home_team == away_team or model['competition'] != 'DE1':
        raise ValueError('Invalid fixture/model competition')
    at,df = model['attack'],model['defence']
    lh = math.exp(max(-3,min(3,model['mu_home']+at.get(home_team,0)+df.get(away_team,0))))
    la = math.exp(max(-3,min(3,model['mu_away']+at.get(away_team,0)+df.get(home_team,0))))
    # 0..60 covers virtually all mass under the bounded rate model; normalize explicitly.
    def pois(lam):
        v = [math.exp(-lam)]
        for i in range(1,61):
            v.append(v[-1]*lam/i)
        return v
    hp,ap = pois(lh),pois(la)
    scores = [(h,a,ph*pa) for h,ph in enumerate(hp) for a,pa in enumerate(ap)]
    mass = sum(p for _,_,p in scores)
    probs = {s:sum(p for h,a,p in scores if ('H' if h>a else 'A' if a>h else 'D')==s)/mass for s in ('H','D','A')}
    top = sorted(scores,key=lambda r:r[2],reverse=True)[:3]
    return {'probabilities':probs,'expected_goals':{'home':lh,'away':la},
            'top_scores':[{'score':f'{h}-{a}','probability':p/mass} for h,a,p in top],
            'unseen_teams':[t for t in (home_team,away_team) if t not in at],
            'probability_status':'uncalibrated_research_estimate'}


def save(model,path):
    Path(path).parent.mkdir(parents=True,exist_ok=True)
    Path(path).write_text(json.dumps(model,ensure_ascii=False,indent=2),encoding='utf-8')


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))
