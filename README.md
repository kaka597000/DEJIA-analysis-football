# 独立德甲赛事研究

专门研究德国甲级联赛（`DE1`）。数据库、球队参数、来源、回测、证据与运行记录均在本仓库，不依赖其他赛事分析项目。参考用户指定的 [WINNER12 网站](https://www.winner12.ai/?lang=zh-CN) 和 [W-5 源码](https://github.com/essowedeo/football-prediction)，详见[方法核验](docs/winner12-reference.md)。本次信息采集由 **Luna（gpt-6-luna）智能体**执行。

已交付：

- 2015/16–2025/26：11个完整赛季、3366场；2026/27单列45场已完赛与261场待赛赛程快照。
- 可直接查询的 [SQLite 数据库](data/bundesliga.sqlite)，包含赛事、可查询历史欧亚赔率、赛前证据和分析台账；[JSONL](data/processed/matches.jsonl)与原始快照可重建数据库。
- 只使用德甲历史赛果训练的泊松攻防基线、按赛季时间隔离回测、概率分箱、热门不胜/弱队获胜评估。
- 五角色独立分析→双轮同行审查→共识；默认运行本地证据审计，配置真实外部服务后可运行LLM模式，两个模式明确区分。
- 结合资金、球队资讯、阵容、亚盘、欧赔及情境的[冷门模块](docs/upset-rules.md)，保留来源、时间、反证、缺失、分数与基线概率。

这是研究版。测试段918场，胜平负453场（49.35%），第一比分104场（11.33%）。没有验证冷门规则或外部LLM共识的命中率；没有把 WINNER12 宣传准确率或倒推演示数据当成效果。完整限制见[模型卡](docs/model-card.md)。

## 快速运行

Python 3.11+，运行不需要第三方库。从仓库根目录执行：

```bash
python -m unittest discover -s tests -v
python -m bundesliga audit
python -m bundesliga train --cutoff 2026-10-13
python -m bundesliga backtest
```

分析已采集的待赛样例，信息截止必须是实际当前或更早时间、早于开球，且不能早于训练数据和赛程的首次采集时间：

```bash
python -m bundesliga analyze \
  --fixture examples/fixture.json \
  --as-of "2026-10-13T10:00:00+08:00" \
  --output reports/my-analysis
```

首次采集时间为2026-10-13北京时间09:51:39；若命令执行时尚未到上述样例截止，请填写实际当前时间。样例JSON使用已核实的主来源球队名。输出 `.json`、中文 `.md` 和数据库运行台账。已保存的[实际样例](reports/example-analysis.md)缺少足够赛前市场/阵容/资金证据，正确输出信息不足；不生成虚构冷门结论。

## 采集与重建

```bash
python scripts/collect_history.py
# 已采集快照的离线重解析，无需网络
python scripts/collect_history.py --reprocess-raw
# 重建到新文件，保留原数据库中的证据和历史运行记录
python -m bundesliga --db work/rebuilt.sqlite import-history data/processed/matches.jsonl
python -m bundesliga --db work/rebuilt.sqlite import-history data/processed/matches_2026-2027_current.jsonl
python -m bundesliga --db work/rebuilt.sqlite audit
```

上游快照内容变化时追加新的原始文件；赛果冲突阻止默默覆盖。导入相同赛事幂等。数据库 `matches` 与 `historical_odds` 分离；历史赔率没有逐条发布时间，`quote_at`为NULL，`timing_status`为未核验，不能作为严格赛前证据自动使用。[来源清单](data/processed/manifest.json)、[质量报告](data/processed/quality.json)、[来源说明](docs/sources.md)记录覆盖率、哈希和独立核对。

CSV `Time` 经独立核对按 `Europe/London`（GMT/BST）转UTC；没有时间仅保留日期。2015/16独立比分核对306/306，当前赛季45/45；这不代表所有赛季都逐场核验。

## 新增赛前信息

```bash
python -m bundesliga collect-rss https://www.dfl.de/en/feed/
# 可选：football-data.org赛程，使用已配置环境变量凭证
python -m bundesliga collect-fixtures
# 用真实资料替换模板里的所有示例数值、占位符及时间，核实后导入
python -m bundesliga import-evidence work/verified-evidence.json
```

RSS原文先存为未匹配、未审核资料；不会把新闻自动绑定到比赛、猜阵容或修改概率。API赛程需核实球队别名（`config/team-aliases.json`已有OpenLigaDB映射）；陌生名称会标记未见球队。当前未实测API赛程鉴权；DFL RSS和OpenLigaDB已采集成功，官方新闻页访问限制见来源说明。

[证据模板](examples/evidence.template.json)仅说明字段，**数字均是示意值，不可直接导入**。`news`、`lineup`、`context`使用同样的球队方向、重要程度和原文摘要合同。每条证据必须包含原始HTTPS来源、发布时间、首次采集时间、审核评级；两种时间均需早于分析截止。阵容变化可附真实球员和阵型详情，未验证不补。

真实资金必须提供币种、统计窗、总金额、金额占比和场所；未知数据保持缺失。欧赔/亚赔必须使用十进制价格；主队盘口负数表示主队让球。不同机构、时间窗的数据不拼接为资金/盘口变动。

## 外部多智能体模式

```bash
cp config/agents.example.json work/agents.json
# 编辑work/agents.json：每个角色填写外部服务实际支持的模型ID
# 通过运行环境配置BUNDESLIGA_LLM_API_KEY，必要时配置HTTPS的BASE_URL
python -m bundesliga analyze \
  --fixture examples/fixture.json \
  --as-of "2026-10-13T10:00:00+08:00" \
  --agents llm --agent-config work/agents.json \
  --output reports/my-llm-analysis
```

支持OpenAI兼容 `/chat/completions` 接口。每场五角色×两轮，最多十次调用尝试，可能产生服务费用；密钥只读取环境变量。模型ID不预设为已可用，也不把Codex Luna名字冒充API模型ID。本次Luna采集已真实执行；外部五LLM接口尚未实际调用，仅完成接口及失败路径测试。失败、非法概率、未知来源引用均剔除，至少三位有效概率分析者才生成研究共识；不生成随机兜底。

两个模式始终保留原数值基线，共识、冷门风险和定性审查分别展示。所有外部证据都作为数据输入，材料中的指令不可信。历史分析显式标记为重建，不伪装赛前冻结。

## 结构

```text
bundesliga/            独立数据库、模型、证据筛选、冷门、多智能体及CLI
scripts/               Luna采集和快照重解析
config/                模型ID配置与明确的球队别名
models/                已训练德甲参数及训练来源摘要
 data/raw/             原始CSV、公开JSON、RSS与SHA-256
 data/processed/       历史/当前赛季分离、赛程、清单和质量
 data/bundesliga.sqlite 赛事数据库
reports/               时间隔离回测、逐场预测、数据库审核、样例
```

所有数据权利属于原提供方；公开可下载不等于无条件商业再分发。来源链接与使用限制见 `docs/sources.md`。GitHub Actions仅进行测试和数据库检查，不自动调用收费服务或收集器。
