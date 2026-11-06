# 🍚 青龙面板部署指南（mifan_optimized v2.0）

> 目标：把 `mifan_optimized.py` 丢进青龙就能跑，签到 / 点赞 / AI 评论 / 补签 / 推送全部由环境变量驱动，**不会出现任何阻塞式输入**。

---

## 1. 部署前须知

| 项目 | 说明 |
| --- | --- |
| 脚本文件 | `mifan_optimized.py`（单文件，无本地依赖模块） |
| Python 依赖 | `requests`（见 `requirements.txt`） |
| 数据文件 | `request_state.json`（点赞断点）、`comment_progress.json`（评论去重）、`mifan_optimized.log`（日志），默认写在脚本同目录，可用 `MIFAN_DATA_DIR` 改到 `/ql/data/mifan` |
| 退出码 | `0` 全部成功 · `1` 有账号执行失败 · `2` 配置缺失（没账号 / 没任务 / LLM 未配置） |
| 阻塞输入 | 仅当 stdin 是真实 TTY 且没有任何环境变量时才进入交互向导；青龙里永远不会卡 `input()` |

---

## 2. 部署方式

### 方式 A：订阅拉取（推荐）

青龙 → 订阅管理 → 新建订阅：

```
名称：mifan
类型：公开仓库
链接：https://github.com/<你的用户名>/<仓库名>          # 指向本仓库
分支：main
定时规则：0 0 * * *
白名单：mifan_optimized\.py
保存后勾选「自动添加任务」，并在任务列表把命令改成：
python3 mifan_optimized.py
```

### 方式 B：手动上传

1. 青龙 → 脚本管理 → 新建文件 `mifan_optimized.py`，把仓库里的文件内容粘进去；
2. 青龙 → 依赖管理 → Python3 → 新增依赖 `requests`（保存后自动安装）；
3. 青龙 → 定时任务 → 新建任务：
   - 名称：`米饭APP日常`
   - 命令：`python3 /ql/data/scripts/mifan_optimized.py`
   - 定时规则：`30 8 * * *`（每天 8:30；脚本内 `cron:` 注释也是这个值）
   - 勾选「立即执行一次」验证。

### 方式 C：容器内手动放文件

```bash
docker cp mifan_optimized.py qinglong:/ql/data/scripts/mifan_optimized.py
docker exec -it qinglong bash -lc "pip3 install requests && python3 /ql/data/scripts/mifan_optimized.py --check-llm"
```

---

## 3. 环境变量（青龙 → 环境变量 → 新增）

### 3.1 账号（必填二选一）

| 变量名 | 示例 | 说明 |
| --- | --- | --- |
| `MIFAN_ACCOUNTS` | `user1:pass1;user2:pass2` | **推荐**，一行多账号；也支持 `user:pass:gid`、`user#pass#gid` |
| `MIFAN_USER` | `user1;user2` | 账号列表（`;` 或 `；` 或换行分隔） |
| `MIFAN_PASSWORD` | `pass1;pass2` | 密码列表，与账号一一对应且数量一致 |
| `MIFAN_GID` | `689` | 区服 ID，默认 689；`MIFAN_ACCOUNTS` 中可逐账号覆盖 |

### 3.2 任务与节奏

| 变量名 | 默认 | 说明 |
| --- | --- | --- |
| `MIFAN_TASK` | `sign,like,comment` | `sign`/`like`/`comment`/`repair`，可写 `0-4`、`all`、中文名（签到/点赞/评论/补签） |
| `ENABLE_REPAIR` | `false` | `true` 时自动追加补签任务 |
| `MIFAN_LIKE_COUNT` | `20` | 单次点赞上限 |
| `MIFAN_COMMENT_COUNT` | `10` | 单次评论上限 |
| `MIFAN_LIKE_SLEEP_MIN/MAX` | `3/10` | 点赞间隔（秒） |
| `MIFAN_COMMENT_SLEEP_MIN/MAX` | `30/60` | 评论间隔（秒），防风控 |
| `MIFAN_ACCOUNT_GAP` | `3` | 多账号之间的等待秒数 |

### 3.3 LLM 自动评论（可选，OpenAI 兼容即可）

| 变量名 | 说明 |
| --- | --- |
| `OPENAI_BASE_URL` | 网关地址，可写 `https://xx/v1`、`https://xx`、甚至完整 `https://xx/v1/chat/completions` |
| `OPENAI_API_KEY` | 任意兼容服务的 Key |
| `OPENAI_MODEL` | 可选；**留空则自动请求 `/models` 探测**并优先挑对话模型 |
| `AI_PROVIDER` | 可选；`openai`/`custom`/`oneapi`/`deepseek`/`moonshot` 等，未设置就按 Key 自动匹配 |
| `AI_TEMPERATURE` | 默认 `0.9` |
| `AI_MAX_TOKENS` | 默认不限制 |
| `AI_TIMEOUT` | 默认 `20` 秒 |
| `AI_EXTRA_HEADERS` | JSON 字符串，附加请求头（Azure/自建网关鉴权用），如 `{"api-key":"xxx"}` |
| `AI_SYSTEM_PROMPT` / `AI_USER_TEMPLATE` | 覆盖默认人设与提示词模板 |
| `MIFAN_COMMENT_MAX_LEN` | 评论最大字数（默认 30，超出自动截断） |
| `DEEPSEEK_API_KEY` / `MOONSHOT_API_KEY` | 老配置继续可用（厂商专属变量） |

后端兼容性（都走 OpenAI 协议，无需改代码）：
OpenAI · Azure（用 `AI_EXTRA_HEADERS` 传 `api-key`，`base_url` 写全 deployments 路径）· one-api / new-api · 硅基流动 · DeepSeek · Moonshot(Kimi) · 智谱 GLM · 通义 Qwen · Groq · vLLM · Ollama(`http://host:11434/v1`) · LM Studio。

示例（自建 one-api 网关）：

```
OPENAI_BASE_URL=http://192.168.1.10:3000
OPENAI_API_KEY=sk-xxxxxxxx
OPENAI_MODEL=gpt-4o-mini
AI_PROVIDER=custom
```

示例（Ollama 本机）：

```
OPENAI_BASE_URL=http://172.17.0.1:11434/v1
OPENAI_API_KEY=ollama
OPENAI_MODEL=qwen2.5:7b
```

### 3.4 通知与调试

| 变量名 | 默认 | 说明 |
| --- | --- | --- |
| `MIFAN_SUCCESS_NOTIFY` | `false` | 有成功账号时推送 |
| `MIFAN_FAIL_NOTIFY` | `false` | 有失败账号时推送 |
| `MIFAN_NOTIFY` | `auto` | `always` / `success` / `fail` / `never`，一键覆盖上面两个开关 |
| `SERVERCHAN_SENDKEY` / `PUSHPLUS_TOKEN` | — | 面板没配通知时的直连兜底（可选） |
| `MIFAN_DEBUG` | `false` | 打印每个请求的状态码与响应片段 |
| `MIFAN_LOG_LEVEL` | `INFO` | `DEBUG`/`INFO`/`WARNING`/`ERROR` |
| `MIFAN_LOG_FILE` | `mifan_optimized.log` | 置空则只输出到青龙日志 |
| `MIFAN_DATA_DIR` | 脚本同目录 | 状态/进度/日志统一落盘位置，建议 `/ql/data/mifan` |
| `MIFAN_TIMEOUT` / `MIFAN_RETRY` / `MIFAN_RETRY_DELAY` | `15` / `3` / `4` | 请求超时、重试次数、重试间隔 |
| `MIFAN_HOST` | `https://mifan.61.com` | 站点地址（走反代时改这里） |
| `MIFAN_AUTO_INSTALL` | `true` | 缺 `requests` 时自动 `pip install` 一次 |

---

## 4. 验证与排查

```bash
# 1) 只测 LLM 连通性（不碰账号，最常用）
python3 mifan_optimized.py --check-llm

# 2) 只跑签到，快速验证账号与接口
python3 mifan_optimized.py --tasks sign

# 3) 临时单账号（不写环境变量）
python3 mifan_optimized.py -u 账号 -p 密码 --tasks sign,like

# 4) 本次不推送
python3 mifan_optimized.py --no-notify
```

| 现象 | 处理 |
| --- | --- |
| `exit=2` + “未配置账号” | 环境变量名写错或没生效；确认是 `MIFAN_USER` + `MIFAN_PASSWORD` 或 `MIFAN_ACCOUNTS` |
| 登录失败 | 检查账号密码是否含空格；`AI`/账号变量不要带引号；`MIFAN_GID` 是否与所在区服一致 |
| LLM `HTTP 401/403` | Key 无效或无该模型权限 |
| LLM `HTTP 404` | 脚本会自动换 `/v1/chat/completions`、`/chat/completions`；两条都 404 说明 `base_url` 路径不对，直接写完整 `.../v1/chat/completions` |
| 模型不存在 | 留空 `OPENAI_MODEL` 让它自动探测，或显式填网关里真实存在的模型名 |
| 中文/emoji 乱码 | v2.0 已强制 stdout 为 UTF-8(`errors=replace`)，老容器请升级青龙镜像 |
| 点赞/评论没动作 | 动态流为空或断点已是最新；删掉状态文件重跑：`rm request_state.json comment_progress.json` |

---

## 5. 与旧版的差异（迁移要点）

- 新增青龙头部 `cron: 30 8 * * *` + `new Env('米饭APP日常')`；
- 状态/进度/日志改为按可写目录解析（脚本目录 → `$QL_DIR/data/mifan` → 临时目录），避免只读脚本目录报错；
- 账号新增 `MIFAN_ACCOUNTS` 单行写法与逐账号 `gid`；
- 任务可裁剪（`MIFAN_TASK` / `--tasks` / CLI 参数）；
- 登录由「表单」升级为「表单 + JSON 双通道重试」，并真正读取 `MIFAN_GID`；
- 签到先查 status 再签，避免重复签到报错；
- LLM 抽象成 `LLMClient`：任意 OpenAI 兼容接口自适应（endpoint 探测 + 模型自动发现 + 输出清洗 + 失败回退内置文案）；
- 点赞断点、评论进度改为原子写，青龙中途 kill 不会写坏 JSON；
- 通知链路：青龙 `notify` 模块 → Server 酱 / PushPlus → 日志；
- 新增明确的退出码、`--check-llm`、`--help`、`--no-notify`，便于任务编排与告警联动。
