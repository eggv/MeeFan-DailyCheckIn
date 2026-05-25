# 🍚 meefan-dailysign

米饭 APP 日常自动签到脚本 —— 支持青龙面板与本地终端交互两种运行模式。

## 功能特性

| 功能          | 说明                                                                            |
| ------------- | ------------------------------------------------------------------------------- |
| ✅ 每日签到   | 自动完成每日签到，青龙模式下已签到自动跳过                                      |
| 👍 自动点赞   | 从动态流获取最新文章 ID 进行点赞，断点续传，无需手动指定 ID                     |
| 💬 自动评论   | 支持 Moonshot / DeepSeek AI 智能评论（有 API Key 时）或使用默认评论库，总能运行 |
| 🛠️ 自动补签   | 检查最近签到记录，自动补签遗漏日期                                              |
| 📊 米粒统计   | 任务前后自动查询米粒余额，展示收益变化                                          |
| 🔐 安全认证   | 用户名 + MD5 密码登录，动态获取 Token，用完即登出                               |
| 👥 多账号支持 | 青龙模式下支持 `;` 分隔的多账号批量执行                                         |
| 🔔 通知推送   | 青龙面板通知，支持成功/失败分别推送                                             |

## 环境要求

- Python 3.8+
- `requests` 库

```bash
pip install requests
```

## 快速开始

### 方式一：本地终端交互模式

直接运行脚本，按提示输入账号密码并选择任务：

```bash
python mifan_optimized.py
```

```
==================================================
      🍚 米饭APP日常脚本 — 交互模式
==================================================

请输入账号 (用户名): your_username
请输入密码: ******

可选任务:
  0. 全部执行（签到+点赞+评论+补签）
  1. 每日签到
  2. 自动点赞
  3. 自动评论
  4. 自动补签

请选择 (0-4):
```

### 方式二：青龙面板模式

在青龙面板中创建定时任务，通过环境变量配置账号：

```bash
# 配置环境变量
export MIFAN_USER="user1;user2"
export MIFAN_PASSWORD="pass1;pass2"
export AI_PROVIDER="deepseek"                # 可选（moonshot/deepseek），默认 deepseek
export MOONSHOT_API_KEY="sk-xxxxx"           # 使用 Moonshot 时必填
# export DEEPSEEK_API_KEY="sk-xxxxx"        # 使用 DeepSeek 时取消注释
export MIFAN_SUCCESS_NOTIFY="true"           # 可选，成功时推送通知
export MIFAN_FAIL_NOTIFY="true"              # 可选，失败时推送通知

# 执行
python mifan_optimized.py
```

#### 青龙面板定时任务配置

1. 前往 青龙面板 → 定时任务 → 创建任务
2. 命令：`python /path/to/mifan_optimized.py`
3. 定时规则：建议 `0 9 * * *`（每天早上 9 点）
4. 在环境变量中添加上述配置项

## 环境变量说明

| 变量名                 | 必填         | 说明                                                           |
| ---------------------- | ------------ | -------------------------------------------------------------- |
| `MIFAN_USER`           | 青龙模式必填 | 用户名，多账号用 `;` 分隔                                      |
| `MIFAN_PASSWORD`       | 青龙模式必填 | 密码，与用户名一一对应                                         |
| `MOONSHOT_API_KEY`     | 评论选填     | Moonshot AI API Key（`AI_PROVIDER=moonshot` 时需填）           |
| `DEEPSEEK_API_KEY`     | 评论选填     | DeepSeek AI API Key（`AI_PROVIDER=deepseek` 时需填）           |
| `AI_PROVIDER`          | 否           | AI 提供商：`deepseek`（默认）或 `moonshot`                     |
| `MOONSHOT_MODEL`       | 否           | Moonshot 模型名（默认 `moonshot-v1-32k`）                      |
| `DEEPSEEK_MODEL`       | 否           | DeepSeek 模型名（默认 `deepseek-v4-flash`）                    |
| `MIFAN_GID`            | 否           | 游戏区服 ID（默认 689）                                        |
| `MIFAN_SUCCESS_NOTIFY` | 否           | 签到成功通知（`true`/`false`，默认 `false`）                   |
| `ENABLE_REPAIR`        | 否           | 启用自动补签（`true`/`false`，默认 `false`，青龙模式默认关闭） |
| `MIFAN_FAIL_NOTIFY`    | 否           | 签到失败通知（`true`/`false`，默认 `false`）                   |

## 点赞与评论的 ID 机制

- **点赞**：脚本自动调用 `api/v1/feed` 获取最新动态流，提取文章 ID 进行点赞。记录已赞过的最高 ID，下次只点赞更新的帖子，无需手动指定 ID。
- **评论**：同样从动态流获取未评论的帖子，有 AI Key 时调用 AI 生成评论，无 Key 时使用内置默认评论库。已评论的帖子 ID 记录在 `comment_progress.json` 中，不会重复评论。

## AI 提供商

自动评论功能支持 **DeepSeek** 和 **Moonshot** 两个 AI 提供商，通过 `AI_PROVIDER` 环境变量切换：

### DeepSeek（默认）

```bash
export AI_PROVIDER=deepseek
export DEEPSEEK_API_KEY="sk-xxxxx"
# 可选：export DEEPSEEK_MODEL="deepseek-v4-flash"
```

### Moonshot

```bash
export AI_PROVIDER=moonshot
export MOONSHOT_API_KEY="sk-xxxxx"
# 可选：export MOONSHOT_MODEL="moonshot-v1-32k"
```

## 账户安全

- **所有凭证均通过环境变量传入**，代码中不包含任何硬编码密钥
- 采用标准登录流程：用户名 → MD5 哈希 → 动态 Token → 操作 → 登出
- 建议定期修改密码

## 本地文件说明

运行后会在当前目录生成以下文件：

- `mifan_optimized.log` — 运行日志
- `request_state.json` — 点赞进度（断点续传）
- `comment_progress.json` — 评论历史记录（避免重复评论）

## 典型使用场景

### 每日全自动签到（青龙面板，默认签到+点赞+评论）

```bash
export MIFAN_USER="my_account"
export MIFAN_PASSWORD="my_password"
export AI_PROVIDER="deepseek"                # 切换到 DeepSeek
export DEEPSEEK_API_KEY="sk-xxxxx"
python mifan_optimized.py
```

### 青龙面板 + 启用补签

```bash
export MIFAN_USER="my_account"
export MIFAN_PASSWORD="my_password"
export AI_PROVIDER="moonshot"
export MOONSHOT_API_KEY="sk-xxxxx"
export ENABLE_REPAIR="true"                  # 额外启用自动补签
python mifan_optimized.py
```

### 仅手动点赞（本地终端）

```bash
python mifan_optimized.py
# 选择 2. 自动点赞
```

## 免责声明

本项目仅供学习交流使用。请遵守米饭 APP 用户协议，合理使用，不要对服务器造成压力。

使用的开源库：

- [requests](https://github.com/psf/requests) — Apache 2.0 License
- [whyour/qinglong](https://github.com/whyour/qinglong) — 定时任务管理平台
