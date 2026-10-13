# DeepSeek 外部智能体配置

官方文档核对日期：2026-10-13（北京时间）。[接口说明](https://api-docs.deepseek.com/)当前列出OpenAI兼容地址 `https://api.deepseek.com`，模型 `deepseek-flash`、`deepseek-v4-pro`；[Chat API文档](https://api-docs.deepseek.com/api/create-chat-completion)支持 `response_format={"type":"json_object"}`。模型列表和权限仍须用自己的账户实际验证，不把文档核对称作鉴权成功。

`https://platform.deepseek.com/api_keys` 是创建/管理密钥的网页，不是API地址。程序会在发出请求前拒绝该地址。

已提供 `config/agents.deepseek.json`：五个角色均使用 `deepseek-flash`，角色提示词仍分别处理统计、战术、市场、资讯、风险。同一模型的五角色不等于五种不同模型，也不能由一致性推断准确率。

在云环境的**密钥**配置中增加环境变量绑定：

- 环境变量名：`BUNDESLIGA_LLM_API_KEY`
- 值：在服务商后台取得的真实密钥；使用安全配置界面填写，不发到聊天、不放入代码或Git。
- 允许访问的目标域名（如果配置有此字段）：`api.deepseek.com`。

聊天里提供凭证不会自动建立环境绑定。本次配置检查未发现 `BUNDESLIGA_LLM_API_KEY`；没有将聊天凭证写到工具参数或文件，没有发出带密钥的请求。当前状态为**配置就绪、鉴权未验证**。聊天中已披露的凭证建议在后台更换后再绑定。

接口地址已包含在JSON中，无需另外设置 `BUNDESLIGA_LLM_BASE_URL`；如果环境里已有该变量，其值优先于JSON，请删除错误地址或改成 `https://api.deepseek.com`。

绑定完成后在仓库根目录运行：

```bash
python -m bundesliga analyze \
  --fixture examples/fixture.json \
  --as-of "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --agents llm \
  --agent-config config/agents.deepseek.json \
  --output reports/deepseek-analysis
```

此命令每场最多尝试10次真实API调用，按账户计费；确认账户额度与当前模型权限。默认保存两轮结果、错误状态和服务返回的用量，无随机兜底；缺乏足够有效概率分析者时标记共识不足。当前数据样例缺资金/阵容等资料，接通LLM后仍不能凭空补齐。
