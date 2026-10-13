import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from . import model, store
from .agents import local_roles, run_llm
from .timeutil import utc
from .upset import screen

FIXTURE_FIELDS = ('match_id','competition','home_team','away_team','kickoff_utc')


def analyze(conn, fixture, as_of, trained, agent_mode='local', agent_config=None):
    # Outcome, raw historical odds and any user-added instructions never enter LLM packets.
    fx={k:fixture[k] for k in FIXTURE_FIELDS}
    cutoff,kickoff=utc(as_of),utc(fx['kickoff_utc'])
    historical=kickoff <= datetime.now(timezone.utc)
    if fixture.get('observed_at'):
        if utc(fixture['observed_at']) > cutoff:
            raise ValueError('Fixture first observed after analysis cutoff')
        fx['observed_at']=fixture['observed_at']
    if fixture.get('source_url'):
        fx['source_url']=fixture['source_url']
    if fx['competition']!='DE1' or fx['home_team']==fx['away_team']:
        raise ValueError('Only distinct Bundesliga teams accepted')
    if cutoff >= kickoff:
        raise ValueError('Analysis cutoff must precede kickoff')
    if cutoff > datetime.now(timezone.utc):
        raise ValueError('Analysis cutoff cannot be in the future')
    if trained['training_cutoff_date'] > cutoff.date().isoformat() or trained['training_last_date'] >= cutoff.date().isoformat():
        raise ValueError('Model trained after analysis cutoff')
    if not historical and utc(trained['training_available_at']) > cutoff:
        raise ValueError('Training source data first observed after analysis cutoff')
    eligible=store.evidence_at(conn,fx['match_id'],as_of)
    baseline=model.predict(trained,fx['home_team'],fx['away_team'])
    upset=screen(baseline,eligible,as_of)
    packet={'fixture':fx,'as_of':as_of,'baseline':baseline,'evidence':eligible,'upset':upset}
    if agent_mode=='local':
        agents=local_roles(packet)
    elif agent_mode=='llm':
        agents=run_llm(packet,agent_config)
    else:
        raise ValueError('Unknown agent mode')
    report={**packet,'model_version':trained['version'],'training_sha256':trained['training_sha256'],
            'training':{k:trained[k] for k in ('training_cutoff_date','training_last_date','training_matches','training_available_at')},
            'agents':agents,'status':'historical_reconstruction' if historical else 'pre_match_research',
            'limitations':['统计基线不含xG、阵容、伤停或资金学习权重；定性证据不自动改写基线概率。',
                            '冷门规则分数未校准；多智能体研究共识与基线分别保存。',
                            '历史重建由当前下载的赛果生成，不能冒充当时已存档的真实赛前预测。']}
    report['run_id']=store.save_run(conn,report)
    return report


def render_markdown(report):
    fx=report['fixture']; p=report['baseline']['probabilities']; u=report['upset']
    text=[f"# 德甲研究：{fx['home_team']} vs {fx['away_team']}",
          f"模型：{report['model_version']}；状态：{report['status']}。",
          f"信息截止（北京时间）：{utc(report['as_of']).astimezone(ZoneInfo('Asia/Shanghai')).isoformat()}",
          f"主胜 {p['H']:.1%} / 平局 {p['D']:.1%} / 客胜 {p['A']:.1%}（未校准研究估计）。",
          '前三比分：'+ '、'.join(f"{x['score']} ({x['probability']:.1%})" for x in report['baseline']['top_scores']),
          f"冷门：{u['risk_level']}；风险分 {u['risk_score']}/100（不是概率）。",
          '缺失维度：'+ '、'.join(u['missing_dimensions']),
          f"智能体模式：{report['agents']['mode']}；外部调用尝试：{report['agents']['external_llm_calls']}。",'']
    for s in u['signals']:
        text.append(f"- 信号：{s['description']}，证据：{', '.join(s['evidence_ids'])}")
    for s in u['counterevidence']:
        text.append(f"- 反证：{s['description']}，证据：{', '.join(s['evidence_ids'])}")
    text += ['']+[f"- 限制：{s}" for s in report['limitations']]
    text += ['','来源：','- [历史来源与快照清单](../data/processed/manifest.json)，训练标签哈希：'+report['training_sha256']]
    if fx.get('source_url'):
        text.append(f"- [赛程来源]({fx['source_url']})，首次采集 {fx.get('observed_at','未提供')}")
    text += [f"- [{e['kind']}]({e['source_url']})，发布时间 {e['published_at']}，采集 {e['observed_at']}，编号 {e['evidence_id']}" for e in report['evidence']]
    return '\n\n'.join(s for s in text if s)+'\n'
