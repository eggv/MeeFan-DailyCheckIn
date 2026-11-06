# 🍚 Meefan-dailysign

米饭 APP 日常自动签到脚本（仓库：`eggv/MeeFan-DailyCheckIn`）—— 适配**青龙面板**部署，同时支持本地终端交互。
v2.0 起 LLM 评论层自适应**任意 OpenAI 兼容接口**（自动探测 endpoint 与模型名）。

> 青龙面板详细部署步骤见 [QINGLONG.md](QINGLONG.md)。

## 功能特性

| 功能 | 说明 |
| --- | --- |
| ✅ 每日签到 | 先查 `dailysign/status` 再签，已签到自动跳过；青龙模式零阻塞输入 |
| 👍 自动点赞 | 从动态流取文章 ID 点赞，记录断点续传，断点文件原子写入 |
| 💬 AI 评论 | 任意 OpenAI 兼容接口（OpenAI / one-api / new-api / 硅基流动 / DeepSeek / Moonshot / 智谱 / Ollama / vLLM …），无 Key 或调用失败时回退内置文案库 |
| 🛠️ 自动补签 | 扫描最近签到记录，自动补签遗漏日期（`ENABLE_REPAIR=true`） |
| 📊 米粒统计 | 任务前后查询米粒余额，输出每账号收益变化 |
| 🔐 安全认证 | 环境变量传参，无硬编码密钥；表单 + JSON 双通道登录，用完即登出 |
| 👥 多账号 | `MIFAN_ACCOUNTS=user:pass;user:pass` 一行搞定，或 `;` 分隔的两行式 |
| 🔔 通知推送 | 青龙 `notify` 模块 → Server 酱 / PushPlus → 日志，三级兜底 |
| 🧩 任务裁剪 | `MIFAN_TASK` / `--tasks` 任选签到、点赞、评论、补签 |
| 🩺 可运维 | `--check-llm` 连通性自检、明确退出码（0/1/2）、`MIFAN_DEBUG` 请求级日志 |

## 环境要求

- Python 3.8+
- `requests`（`pip install -r requirements.txt`）

## 快速开始

### 青龙面板（推荐）

1. 脚本管理放入 `mifan_optimized.py`，依赖管理安装 `requests`；
2. 环境变量添加账号与（可选）LLM 配置；
3. 定时任务：`python3 /ql/data/scripts/mifan_optimized.py`，规则 `30 8 * * *`（脚本头部已内置 `cron` 声明）。

```bash
# 最小可用配置（只做签到 + 点赞 + 内置文案评论）
MIFAN_ACCOUNTS=user1:pass1;user2:pass2

# 加上 AI 评论（任意 OpenAI 兼容网关）
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_API_KEY=sk-xxxxx
OPENAI_MODEL=gpt-4o-mini        # 留空则自动调 /models 探测

# 通知
MIFAN_SUCCESS_NOTIFY=true
MIFAN_FAIL_NOTIFY=true
```

### 本地终端交互模式

无任何账号环境变量、且 stdin 是终端时会进入向导：

```bash
python mifan_optimized.py
```

```
========================================================
      🍚 米饭APP日常脚本 — 交互模式
========================================================

请输入账号（用户名）: your_username
请输入密码: ******
请输入区服 GID（回车默认 689）:

可选任务:
  0. 全部执行（签到+点赞+评论+补签）
  1. 每日签到
  2. 自动点赞
  3. 自动评论
  4. 自动补签
  5. 签到+点赞+评论（青龙默认组合）

请选择 (0-5，回车默认 5):
```

### 命令行直跑

```bash
python mifan_optimized.py --check-llm                 # 只测 LLM 连通性
python mifan_optimized.py --tasks sign                # 只签到
python mifan_optimized.py -u 账号 -p 密码 -t like,comment
python mifan_optimized.py --no-notify                 # 本次不推送
python mifan_optimized.py --help
```

## 环境变量

完整表格（含调试、节奏、数据目录等）见 [QINGLONG.md](QINGLONG.md#3-环境变量青龙--环境变量--新增)，核心项：

| 变量名 | 必填 | 说明 |
| --- | --- | --- |
| `MIFAN_ACCOUNTS` | 二选一 | `user:pass;user:pass`（推荐，支持 `user:pass:gid`） |
| `MIFAN_USER` | 二选一 | 账号，多账号 `;` 分隔 |
| `MIFAN_PASSWORD` | 二选一 | 密码，与账号一一对应 |
| `MIFAN_TASK` | 否 | 默认 `sign,like,comment`；可选 `repair`、`0-4`、`all` |
| `MIFAN_GID` | 否 | 区服 ID，默认 `689` |
| `OPENAI_BASE_URL` / `OPENAI_API_KEY` / `OPENAI_MODEL` | 否 | OpenAI 兼容接口（启用 AI 评论） |
| `AI_PROVIDER` | 否 | `openai`/`custom`/`oneapi`/`deepseek`/`moonshot`，默认按 Key 自动匹配 |
| `ENABLE_REPAIR` | 否 | `true` 启用自动补签 |
| `MIFAN_SUCCESS_NOTIFY` / `MIFAN_FAIL_NOTIFY` | 否 | 成功 / 失败推送开关 |
| `MIFAN_NOTIFY` | 否 | `always`/`success`/`fail`/`never`，一键覆盖上面两个开关 |
| `MIFAN_DEBUG` | 否 | 打印请求级日志，排障用 |

> 老配置（`AI_PROVIDER=deepseek` + `DEEPSEEK_API_KEY`，或 `AI_PROVIDER=moonshot` + `MOONSHOT_API_KEY`）依旧兼容。

## LLM 自适应说明

`LLMClient` 只依赖 OpenAI 协议，配置面收窄到三个变量：

1. **base_url 自适应**：无论写 `https://gw/v1`、`https://gw`、还是完整 `https://gw/v1/chat/completions`，
   都会展开成候选地址并记住可用那条；命中 404/405 自动切换，命中 401/403 直接给出鉴权提示。
2. **模型自动发现**：`OPENAI_MODEL` 留空时请求 `/models`，过滤 `embedding/rerank/tts/whisper` 等非对话模型，
   优先挑选 `chat/instruct/gpt/qwen/glm/deepseek/moonshot/...`。
3. **输出清洗**：去掉引号、书名号、`评论：` 前缀、链接、`@提及`、换行与超长内容，并和历史评论去重（重复则重新生成）。
4. **失败回退**：LLM 不可用时自动使用内置文案库，脚本永远不会因为 AI 挂掉而中断。
5. **鉴权扩展**：`AI_EXTRA_HEADERS` 支持 Azure `api-key` 之类的自定义头。

支持的后端（均走 OpenAI 协议，无需改代码）：
OpenAI · Azure · one-api / new-api · 硅基流动 · DeepSeek · Moonshot(Kimi) · 智谱 GLM · 通义 Qwen · Groq · vLLM · Ollama · LM Studio。

## 点赞与评论的 ID 机制

- **点赞**：脚本自动调用 `api/v1/feed` 获取最新动态流，提取文章 ID 进行点赞。记录已赞过的最高 ID，下次只点赞更新的帖子，无需手动指定 ID。
- **评论**：同样从动态流筛选未评论帖子；`comment_progress.json` 记录已评论 ID、分页 offset 与最近 30 条评论内容（用于去重）。

## 本地文件说明

运行后会在数据目录（默认脚本同目录，可用 `MIFAN_DATA_DIR` 改到 `/ql/data/mifan`）生成：

| 文件 | 说明 |
| --- | --- |
| `mifan_optimized.log` | 运行日志（`MIFAN_LOG_FILE` 可改，置空则只输出 Console） |
| `request_state.json` | 点赞断点（原子写） |
| `comment_progress.json` | 评论去重与分页进度（原子写） |

以上文件已在 `.gitignore` 中排除，不会被提交。

## 自检与测试

```bash
python smoke_test.py     # 离线冒烟：stub 掉 requests，验证任务解析、账号合并、评论清洗、
                         # endpoint 自适应、模型探测、汇总与 CLI（无需联网）
python flow_test.py      # 离线全链路：登录→签到→点赞→AI评论→补签→登出→退出码（无需联网）
```

## 账户安全

- **所有凭证仅通过环境变量传入**，仓库中不包含任何硬编码密钥；
- 采用标准登录流程：用户名 → MD5 哈希 → 动态 Token → 操作 → 登出；
- 日志只记录 token 前缀，不记录完整 token / 密码，且日志文件默认被 git 忽略；
- 建议定期修改密码；如曾把密钥写入代码并提交，请立即在服务商控制台吊销重发。

## 免责声明

本项目仅供学习交流使用。请遵守米饭 APP 用户协议，合理使用，不要对服务器造成压力。

使用的开源库：

- [requests](https://github.com/psf/requests) — Apache 2.0 License
- [whyour/qinglong](https://github.com/whyour/qinglong) — 定时任务管理平台

## 许可证

[MIT](LICENSE) © 2026 eggv
