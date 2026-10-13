# WINNER12 方法参考与核验

用户指定：<https://www.winner12.ai/?lang=zh-CN>、<https://github.com/essowedeo/football-prediction>。2026-10-13 北京时间执行本次核验，源码成功克隆，固定 commit `d5e5112e725e77f1e68fc251bd505a7860bb788c`；网站直连返回 HTTP 403，仅以可检查的源码说明方法。这个仓库自行实现德甲模块，不依赖其他赛事项目或其历史研究快照。

## 采用的方法

源码 `src/consensus/agent.py` 定义 statistician、tactician、sentiment_analyst、news_analyst、risk_assessor 五种角色；`src/consensus/debate.py` 定义独立分析→同行讨论→汇总；`src/models/baseline.py` 展示数值基线和语言模型结合。本项目沿用职责拆分、数值基线、证据审阅和汇总流程，将市场角色细化为真实资金、欧赔、亚赔，并增加独立冷门筛查。

本项目的外部模式第一轮独立运行，第二轮真实传递所有可用同行意见；每轮保存概率、来源、反证、缺失、失败和用量。至少三位有效分析者提供合法概率才能输出等权研究共识；共识与基线各自保存，无强迫一致、无自报置信度权重。采集工作此次已由 `gpt-6-luna` 执行；Codex 的这个模型名并不自动等于任意外部服务的 API 模型 ID。

## 不作为效果证据的代码

- `src/consensus/agent.py::_query_llm` 遇到异常会调用 `_mock_response`，后者用随机数构造概率。本项目请求失败返回失败，禁止生成替代概率。
- `src/consensus/debate.py::_debate_round` 说明其讨论调整为演示模拟；本项目第二轮实际把同行结果写入请求。
- `src/models/baseline.py::train` 使用随机 `train_test_split`；本项目严格按时间分季，测试赛季始终由更早数据拟合的模型预测。
- `scripts/generate_backtest_results.py` 明确声明按 `TARGET_ACCURACY=0.859` 从实际结果倒推预测。该脚本产生的结果无法独立证明预测能力；未复制、运行或导入这些数据。
- README 的 86.3% 同时包含高置信度筛选与二分类口径，不能与本项目全量三分类胜平负或第一比分命中率直接对比。网站效果主张未独立复现。

链接均可固定到以上 commit 查看原文。本项目未复制上游实现或数据，只参考架构；数据来源和使用限制另见 `sources.md`。
