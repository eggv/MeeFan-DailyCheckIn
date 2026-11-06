#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cron: 30 8 * * *
new Env('米饭APP日常')

===============================================================================
🍚 米饭 APP 日常脚本 · 青龙面板适配版 (v2.0)
===============================================================================
功能：
  • 每日签到（自动跳过已签到，青龙模式无阻塞）
  • 自动点赞（动态流断点续传，无需手填 ID）
  • LLM 自动评论（自适应任意 OpenAI 兼容接口）
  • 自动补签 / 米粒余额统计 / 多渠道通知推送

运行模式：
  1) 青龙面板 / crontab / CI —— 全部由环境变量驱动，任何情况下都不产生阻塞输入
  2) 本地终端 —— 无环境变量且 stdin 为 TTY 时进入交互向导

LLM 自适应：
  只要提供 OpenAI 兼容的 base_url + api_key 即可工作，无需改代码：
      OPENAI_BASE_URL=https://api.openai.com/v1
      OPENAI_API_KEY=sk-xxxx
      OPENAI_MODEL=gpt-4o-mini          # 可留空，脚本自动调 /models 探测
  自动尝试 {base}/v1/chat/completions 与 {base}/chat/completions，
  自动识别 404/405 并切换，兼容 one-api / new-api / vLLM / Ollama / 硅基流动 /
  智谱 / DeepSeek / Moonshot / Azure(自建 path) 等一切 OpenAI 协议网关。

青龙定时任务命令：
      python3 mifan_optimized.py
  或指定任务：
      python3 mifan_optimized.py --tasks sign,like,comment
===============================================================================
"""

import argparse
import gzip
import hashlib
import json
import logging
import os
import random
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

__version__ = "2.0.0"

SCRIPT_PATH = os.path.abspath(__file__)
SCRIPT_DIR = os.path.dirname(SCRIPT_PATH)
SCRIPT_NAME = os.path.basename(SCRIPT_PATH)


# =============================================================================
# 0. 运行环境准备（环境变量读取 / 编码安全 / 依赖自检）
# =============================================================================

_ENV: Dict[str, str] = {k.lower(): v for k, v in os.environ.items()}


def env(*names: str, default: Optional[str] = None) -> Optional[str]:
    """按优先级依次查找环境变量（大小写不敏感，青龙面板里变量名大小写混用也能读到）。"""
    for name in names:
        if not name:
            continue
        value = _ENV.get(name.lower())
        if value is not None and value.strip() != "":
            return value.strip()
    return default


def env_bool(*names: str, default: bool = False) -> bool:
    value = env(*names)
    if value is None:
        return default
    return value.strip().lower() in {
        "1", "true", "yes", "y", "on", "enable", "enabled", "是", "开启", "开",
    }


def env_int(*names: str, default: int) -> int:
    value = env(*names)
    if value is None:
        return default
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def env_float(*names: str, default: float) -> float:
    value = env(*names)
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _fix_stdio() -> None:
    """青龙日志收集器 + Windows GBK 控制台下，emoji / 中文都不会炸。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass


_fix_stdio()


def _load_requests():
    """导入 requests；容器缺依赖时静默补装一次（可用 MIFAN_AUTO_INSTALL=false 关闭）。"""
    try:
        import requests  # noqa: F401
        import urllib3  # noqa: F401
        return requests, urllib3
    except ImportError:
        if env_bool("MIFAN_AUTO_INSTALL", default=True):
            print("[依赖] 未检测到 requests，尝试自动安装 ...")
            try:
                subprocess.run(
                    [sys.executable, "-m", "pip", "install", "--no-cache-dir", "-q", "requests"],
                    check=False,
                    timeout=180,
                )
            except Exception as exc:  # 离线 / 无 pip 都不致命，下面再 import 一次
                print(f"[依赖] 自动安装失败：{exc}")
        try:
            import requests  # noqa: F401
            import urllib3  # noqa: F401
            return requests, urllib3
        except ImportError:
            print("[依赖] 缺少 requests 库，请在青龙「依赖管理 → Python3」添加 requests，"
                  "或执行：pip3 install requests")
            sys.exit(2)


requests, urllib3 = _load_requests()
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


# =============================================================================
# 1. 全局配置
# =============================================================================

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/145.0.0.0 Safari/537.36 Edg/145.0.0.0"
)

# 站点根地址（需要镜像/内网代理时用 MIFAN_HOST 覆盖）
API_HOST = (env("MIFAN_HOST", default="https://mifan.61.com") or "").rstrip("/")

URLS = {
    "LOGIN":       f"{API_HOST}/api/v1/login",
    "LOGOUT":      f"{API_HOST}/api/v1/logout",
    "PROFILE":     f"{API_HOST}/api/v1/profile",
    "DAILY_SIGN":  f"{API_HOST}/api/v1/event/dailysign/",
    "SIGN_STATUS": f"{API_HOST}/api/v1/event/dailysign/status/",
    "LIKE":        f"{API_HOST}/api/v1/article/likes/{{id}}/",
    "FEED":        f"{API_HOST}/api/v1/feed",
    "COMMENT":     f"{API_HOST}/api/v1/article/comment",
    "COMPLEMENT_RECENT": f"{API_HOST}/api/v1/event/dailysign/recent",
    "COMPLEMENT_DO":     f"{API_HOST}/api/v1/event/dailysign/complement",
}

# 网络参数
HTTP_TIMEOUT = env_int("MIFAN_TIMEOUT", default=15)
HTTP_RETRY = env_int("MIFAN_RETRY", default=3)
RETRY_DELAY_S = env_float("MIFAN_RETRY_DELAY", default=4.0)
DEBUG = env_bool("MIFAN_DEBUG", default=False)

# 业务参数
DEFAULT_GID = env_int("MIFAN_GID", default=689)
LIKE_COUNT = env_int("MIFAN_LIKE_COUNT", default=20)
COMMENT_COUNT = env_int("MIFAN_COMMENT_COUNT", default=10)
SLEEP_LIKE = (env_float("MIFAN_LIKE_SLEEP_MIN", default=3.0), env_float("MIFAN_LIKE_SLEEP_MAX", default=10.0))
SLEEP_COMMENT = (env_float("MIFAN_COMMENT_SLEEP_MIN", default=30.0), env_float("MIFAN_COMMENT_SLEEP_MAX", default=60.0))
ACCOUNT_GAP_S = env_float("MIFAN_ACCOUNT_GAP", default=3.0)

# 数据目录：状态 / 进度 / 日志都落在这里，保证青龙里脚本目录可直接持久化
def _resolve_data_dir() -> str:
    custom = env("MIFAN_DATA_DIR")
    candidates: List[str] = []
    if custom:
        candidates.append(custom)
    ql_dir = env("QL_DIR")
    if ql_dir:
        candidates.append(os.path.join(ql_dir, "data", "mifan"))
    candidates.extend([SCRIPT_DIR, os.getcwd()])

    for candidate in candidates:
        try:
            os.makedirs(candidate, exist_ok=True)
            probe = os.path.join(candidate, f".mifan_write_test_{os.getpid()}")
            with open(probe, "w", encoding="utf-8") as fp:
                fp.write("ok")
            os.remove(probe)
            return candidate
        except Exception:
            continue
    return tempfile.gettempdir()


DATA_DIR = _resolve_data_dir()
STATE_FILE = os.path.join(DATA_DIR, env("MIFAN_STATE_FILE", default="request_state.json") or "request_state.json")
PROGRESS_FILE = os.path.join(DATA_DIR, env("MIFAN_PROGRESS_FILE", default="comment_progress.json") or "comment_progress.json")
LOG_FILE = os.path.join(DATA_DIR, env("MIFAN_LOG_FILE", default="mifan_optimized.log") or "mifan_optimized.log")

# 运行时状态（main 里赋值，供交互模式判断）
RUNTIME: Dict[str, Any] = {"interactive": False, "notify_mode": "auto"}

# 内置兜底评论（无 LLM Key 或 LLM 调用失败时使用）
FALLBACK_COMMENTS = [
    "不错！", "学习到了！", "给力👍", "有意思！", "太棒了！",
    "好厉害呀！", "赞一个！", "收藏了～", "这个好！", "谢谢分享！",
    "确实如此！", "学到了！", "哈哈不错", "支持一下！",
]


# =============================================================================
# 2. 日志
# =============================================================================

class Logger:
    """统一日志：控制台（青龙收集）+ 文件（本地排查）双写，全程不抛异常。"""

    _logger: Optional[logging.Logger] = None

    @classmethod
    def init(cls, log_file: Optional[str] = None) -> None:
        if cls._logger is not None:
            return
        level_name = (env("MIFAN_LOG_LEVEL", default="INFO") or "INFO").upper()
        level = getattr(logging, level_name, logging.INFO)

        logger = logging.getLogger("mifan")
        logger.setLevel(level)
        logger.handlers.clear()
        logger.propagate = False

        fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S")
        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(fmt)
        logger.addHandler(console)

        path = log_file if log_file is not None else LOG_FILE
        if path:
            try:
                file_handler = logging.FileHandler(path, encoding="utf-8", delay=True)
                file_handler.setFormatter(fmt)
                logger.addHandler(file_handler)
            except Exception as exc:
                print(f"[日志] 文件日志不可用（{exc}），仅输出到控制台")

        cls._logger = logger

    @classmethod
    def _ensure(cls) -> logging.Logger:
        if cls._logger is None:
            cls.init()
        assert cls._logger is not None
        return cls._logger

    @classmethod
    def info(cls, msg: str, *args: Any) -> None:
        cls._ensure().info(msg, *args)

    @classmethod
    def success(cls, msg: str, *args: Any) -> None:
        cls._ensure().info("✅ " + msg, *args)

    @classmethod
    def warning(cls, msg: str, *args: Any) -> None:
        cls._ensure().warning("⚠️  " + msg, *args)

    @classmethod
    def error(cls, msg: str, *args: Any) -> None:
        cls._ensure().error("❌ " + msg, *args)

    @classmethod
    def debug(cls, msg: str, *args: Any) -> None:
        cls._ensure().debug("🔍 " + msg, *args)


# =============================================================================
# 3. 轻量 JSON 存储（原子写，避免青龙中途 kill 造成文件损坏）
# =============================================================================

def load_json(path: str, default: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as fp:
            return json.load(fp)
    except Exception:
        return default


def save_json(path: str, data: Any) -> bool:
    tmp = f"{path}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fp:
            json.dump(data, fp, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return True
    except Exception as exc:
        Logger.warning(f"保存 {os.path.basename(path)} 失败：{exc}")
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
        return False


# =============================================================================
# 4. 通知（青龙 notify 模块 → Server酱 / PushPlus → 本地日志）
# =============================================================================

class Notifier:
    _ql_available: Optional[bool] = None

    @classmethod
    def send(cls, title: str, content: str) -> bool:
        content = (content or "").strip()
        if cls._send_via_qinglong(title, content):
            return True
        if cls._send_via_webhook(title, content):
            return True
        Logger.info("=" * 56)
        Logger.info(f"📬 {title}")
        for line in content.splitlines():
            Logger.info(f"   {line}")
        Logger.info("=" * 56)
        return False

    @classmethod
    def _send_via_qinglong(cls, title: str, content: str) -> bool:
        """青龙面板自带 notify 模块（由面板的「通知设置」决定推送渠道）。"""
        if cls._ql_available is False:
            return False
        try:
            import notify  # type: ignore
        except Exception:
            cls._ql_available = False
            return False
        sender = getattr(notify, "send", None) or getattr(notify, "sendNotify", None)
        if sender is None:
            cls._ql_available = False
            return False
        try:
            sender(title, content)
            cls._ql_available = True
            Logger.info("📨 已通过青龙 notify 模块推送")
            return True
        except Exception as exc:
            cls._ql_available = False
            Logger.warning(f"青龙 notify 推送失败：{exc}")
            return False

    @classmethod
    def _send_via_webhook(cls, title: str, content: str) -> bool:
        """面板没配通知时的直连兜底，纯可选。"""
        sckey = env("SERVERCHAN_SENDKEY", "PUSH_KEY", "SCKEY")
        if sckey:
            resp = Http.request(
                "POST", f"https://sctapi.ftqq.com/{sckey}.send",
                data={"title": title, "desp": content}, timeout=15, retries=1,
            )
            if resp is not None and resp.status_code == 200:
                Logger.info("📨 已通过 Server 酱推送")
                return True
        token = env("PUSHPLUS_TOKEN")
        if token:
            resp = Http.request(
                "POST", "http://www.pushplus.plus/send",
                json_body={"token": token, "title": title, "content": content, "template": "txt"},
                timeout=15, retries=1,
            )
            if resp is not None and resp.status_code == 200:
                Logger.info("📨 已通过 PushPlus 推送")
                return True
        return False


# =============================================================================
# 5. HTTP 封装（连接池复用 + 重试 + 5xx/429 退避）
# =============================================================================

class Http:
    _session = None

    @classmethod
    def session(cls):
        if cls._session is None:
            session = requests.Session()
            session.headers.update({
                "User-Agent": USER_AGENT,
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "zh-CN,zh;q=0.9",
            })
            adapter = requests.adapters.HTTPAdapter(pool_connections=8, pool_maxsize=16, max_retries=0)
            session.mount("https://", adapter)
            session.mount("http://", adapter)
            cls._session = session
        return cls._session

    @classmethod
    def request(
        cls,
        method: str,
        url: str,
        headers: Optional[dict] = None,
        data: Any = None,
        json_body: Any = None,
        params: Optional[dict] = None,
        timeout: Optional[float] = None,
        retries: Optional[int] = None,
        quiet: bool = False,
    ):
        """返回 Response 或 None（全部重试失败）。quiet=True 用于探测类请求，不刷错误日志。"""
        timeout = timeout or HTTP_TIMEOUT
        retries = HTTP_RETRY if retries is None else max(1, retries)
        last_error = ""
        for attempt in range(1, retries + 1):
            try:
                resp = cls.session().request(
                    method=method.upper(),
                    url=url,
                    headers=headers or {},
                    data=data,
                    json=json_body,
                    params=params,
                    timeout=timeout,
                    verify=False,
                )
                if DEBUG:
                    Logger.debug(f"{method.upper()} {url} → {resp.status_code} | {resp.text[:300]}")
                if resp.status_code >= 500 or resp.status_code == 429:
                    last_error = f"HTTP {resp.status_code}"
                    if attempt < retries:
                        Logger.warning(f"服务端异常 {last_error}，{attempt}/{retries} 次重试中 ...")
                        time.sleep(RETRY_DELAY_S)
                        continue
                return resp
            except Exception as exc:
                last_error = str(exc)
                if attempt < retries:
                    if not quiet:
                        Logger.warning(f"请求异常 {exc}，{attempt}/{retries} 次重试中 ...")
                    time.sleep(RETRY_DELAY_S)
        if not quiet:
            Logger.error(f"请求最终失败：{method.upper()} {url} | {last_error}")
        elif DEBUG:
            Logger.debug(f"探测请求失败：{method.upper()} {url} | {last_error}")
        return None

    @classmethod
    def json(cls, resp) -> Optional[dict]:
        """解析响应为 dict，兼容手工 gzip 解压。"""
        if resp is None:
            return None
        try:
            return resp.json()
        except Exception:
            pass
        try:
            raw = resp.content
            if resp.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            return json.loads(raw.decode("utf-8", "replace"))
        except Exception as exc:
            Logger.warning(f"响应解析失败：{exc}（HTTP {resp.status_code}）")
            return None


# =============================================================================
# 6. LLM：任意 OpenAI 兼容接口自适应
# =============================================================================

# provider 顺序即自动探测顺序：优先通用 OpenAI 兼容变量，其次厂商专属变量
LLM_PROVIDERS: List[Dict[str, Any]] = [
    {
        "provider": "openai",
        "label": "OpenAI 兼容接口",
        "key_envs": ["OPENAI_API_KEY", "OPENAI_KEY", "AI_API_KEY", "LLM_API_KEY"],
        "base_envs": ["OPENAI_BASE_URL", "OPENAI_API_BASE", "OPENAI_ENDPOINT",
                      "AI_BASE_URL", "LLM_BASE_URL"],
        "model_envs": ["OPENAI_MODEL", "AI_MODEL", "LLM_MODEL"],
        "default_base": "https://api.openai.com/v1",
        "default_model": "gpt-4o-mini",
    },
    {
        "provider": "deepseek",
        "label": "DeepSeek",
        "key_envs": ["DEEPSEEK_API_KEY"],
        "base_envs": ["DEEPSEEK_BASE_URL"],
        "model_envs": ["DEEPSEEK_MODEL"],
        "default_base": "https://api.deepseek.com/v1",
        "default_model": "deepseek-chat",
    },
    {
        "provider": "moonshot",
        "label": "Moonshot",
        "key_envs": ["MOONSHOT_API_KEY", "KIMI_API_KEY"],
        "base_envs": ["MOONSHOT_BASE_URL"],
        "model_envs": ["MOONSHOT_MODEL"],
        "default_base": "https://api.moonshot.cn/v1",
        "default_model": "moonshot-v1-8k",
    },
]

_PROVIDER_ALIASES = {
    "openai": "openai", "openai-compatible": "openai", "compatible": "openai",
    "custom": "openai", "oneapi": "openai", "one-api": "openai", "newapi": "openai",
    "new-api": "openai", "azure": "openai", "siliconflow": "openai", "ollama": "openai",
    "vllm": "openai", "groq": "openai", "zhipu": "openai", "qwen": "openai",
    "deepseek": "deepseek", "moonshot": "moonshot", "kimi": "moonshot",
}

DEFAULT_SYSTEM_PROMPT = (
    "你是一个在摩尔庄园社区活跃多年的真实玩家。你的回复特点是："
    "1）简短自然 2）贴合帖子内容 3）语气像真人 4）多样化，避免重复句式。"
    "只输出评论本身，不要加引号、不要加前缀、不要换行。"
)
DEFAULT_USER_TEMPLATE = (
    "请为这篇帖子写一条中文评论（{min_len}-{max_len}字），要贴合帖子内容、语气自然：\n“{post}”"
)


class LLMClient:
    """OpenAI 协议自适应客户端：自动挑选可用 endpoint、自动探测模型名。"""

    def __init__(self, cfg: Dict[str, Any]):
        self.provider: str = cfg["provider"]
        self.label: str = cfg.get("label") or cfg["provider"]
        self.api_key: str = cfg["api_key"]
        self.base_url: str = cfg.get("base_url") or ""
        self.model: str = (cfg.get("model") or "").strip()
        self.temperature: float = float(cfg.get("temperature", 0.9))
        self.max_tokens: int = int(cfg.get("max_tokens", 0) or 0)
        self.timeout: int = int(cfg.get("timeout", 20) or 20)
        self.max_len: int = int(cfg.get("comment_max_len", 30) or 30)
        self.system_prompt: str = cfg.get("system_prompt") or DEFAULT_SYSTEM_PROMPT
        self.user_template: str = cfg.get("user_template") or DEFAULT_USER_TEMPLATE
        self.extra_headers: Dict[str, str] = cfg.get("extra_headers") or {}
        self._urls: List[str] = self.build_urls(self.base_url)
        self._url_idx: int = 0

    # ---- URL / headers ----

    @staticmethod
    def build_urls(base_url: str) -> List[str]:
        """把用户给的 base_url 展开成候选 /chat/completions 地址（保序去重）。"""
        base = (base_url or "").strip().rstrip("/")
        if not base:
            base = "https://api.openai.com/v1"
        if not re.match(r"^https?://", base, re.I):
            base = "https://" + base
        if base.lower().endswith("/chat/completions"):
            return [base]

        candidates: List[str] = []
        if not re.search(r"/v\d+[a-z]*$", base, re.I):
            candidates.append(f"{base}/v1/chat/completions")
        candidates.append(f"{base}/chat/completions")

        urls: List[str] = []
        for url in candidates:
            if url not in urls:
                urls.append(url)
        return urls

    @property
    def model_urls(self) -> List[str]:
        suffix = "/chat/completions"
        return [u[: -len(suffix)] + "/models" for u in self._urls if u.endswith(suffix)]

    def headers(self) -> Dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        headers.update(self.extra_headers)
        return headers

    @staticmethod
    def _short(resp, limit: int = 200) -> str:
        try:
            return re.sub(r"\s+", " ", resp.text or "")[:limit]
        except Exception:
            return ""

    # ---- 模型自动探测 ----

    _MODEL_BLACKLIST = re.compile(
        r"embed|rerank|whisper|tts|dall|image|moderation|audio|vision-encoder|stable-", re.I
    )
    _MODEL_PREFER = ("chat", "instruct", "gpt", "qwen", "glm", "deepseek", "moonshot",
                     "claude", "llama", "mistral", "gemini")

    def resolve_model(self) -> str:
        if self.model:
            return self.model
        for url in self.model_urls:
            resp = Http.request("GET", url, headers=self.headers(), timeout=self.timeout,
                                retries=1, quiet=True)
            if resp is None or resp.status_code != 200:
                continue
            data = Http.json(resp) or {}
            ids = [
                item.get("id") for item in (data.get("data") or [])
                if isinstance(item, dict) and item.get("id")
            ]
            ids = sorted({i for i in ids if not self._MODEL_BLACKLIST.search(i)})
            if not ids:
                continue
            for keyword in self._MODEL_PREFER:
                for model_id in ids:
                    if keyword in model_id.lower():
                        self.model = model_id
                        Logger.info(f"🤖 自动探测到模型：{model_id}")
                        return model_id
            self.model = ids[0]
            Logger.info(f"🤖 自动探测到模型：{self.model}")
            return self.model

        self.model = "gpt-4o-mini"
        Logger.warning(f"未能自动探测模型，回退默认：{self.model}（可用 OPENAI_MODEL 显式指定）")
        return self.model

    # ---- 调用 ----

    def chat(
        self,
        messages: Sequence[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> Optional[str]:
        model = self.resolve_model()
        payload: Dict[str, Any] = {
            "model": model,
            "messages": list(messages),
            "temperature": self.temperature if temperature is None else temperature,
            "stream": False,
        }
        tokens = self.max_tokens if max_tokens is None else max_tokens
        if tokens:
            payload["max_tokens"] = tokens

        errors: List[str] = []
        for idx in range(self._url_idx, len(self._urls)):
            url = self._urls[idx]
            for attempt in range(1, HTTP_RETRY + 1):
                try:
                    resp = requests.post(
                        url, headers=self.headers(), json=payload,
                        timeout=self.timeout, verify=False,
                    )
                except Exception as exc:
                    errors.append(f"{url} → {exc}")
                    if attempt < HTTP_RETRY:
                        time.sleep(2 * attempt)
                        continue
                    break

                code = resp.status_code
                if code in (404, 405):
                    errors.append(f"{url} → HTTP {code} 路径不存在")
                    break  # 换下一个候选地址
                if code in (401, 403):
                    Logger.error(f"LLM 鉴权失败（HTTP {code}），请检查 API Key：{self._short(resp)}")
                    return None
                if code == 429 or code >= 500:
                    errors.append(f"{url} → HTTP {code}")
                    if attempt < HTTP_RETRY:
                        time.sleep(2 * attempt)
                        continue
                    break
                if resp.ok:
                    text = self._extract(resp)
                    if text:
                        if idx != self._url_idx:
                            Logger.info(f"🤖 LLM 接口自适应到：{url}")
                        self._url_idx = idx
                        return text
                    errors.append(f"{url} → 响应中无可用文本")
                    break
                errors.append(f"{url} → HTTP {code} {self._short(resp)}")
                break

        Logger.warning("LLM 调用失败：" + " | ".join(errors[-3:]))
        return None

    @staticmethod
    def _extract(resp) -> str:
        data = Http.json(resp) or {}
        choices = data.get("choices") or []
        if not choices or not isinstance(choices[0], dict):
            return ""
        choice = choices[0]
        message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
        content = (message or {}).get("content") or choice.get("text") or ""
        if isinstance(content, list):
            content = "".join(
                part.get("text", "") for part in content if isinstance(part, dict)
            )
        text = str(content).strip()
        # 去掉模型习惯性包裹的成对引号 / 书名号
        for left, right in (('"', '"'), ("'", "'"), ("“", "”"), ("‘", "’"),
                            ("《", "》"), ("「", "」"), ("【", "】")):
            if len(text) > 1 and text.startswith(left) and text.endswith(right):
                text = text[1:-1].strip()
        return text

    # ---- 评论生成 ----

    def generate_comment(self, post_text: str, avoid: Optional[set] = None) -> Optional[str]:
        """生成一条干净、可直接发布的评论；失败返回 None（由调用方兜底）。"""
        avoid = avoid or set()
        user_prompt = self.user_template.format(
            post=post_text[:200],
            min_len=env_int("MIFAN_COMMENT_MIN_LEN", default=5),
            max_len=self.max_len,
        )
        for attempt in range(2):
            raw = self.chat([
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": user_prompt},
            ])
            if not raw:
                continue
            text = sanitize_comment(raw, self.max_len)
            if not text:
                continue
            if text in avoid:
                Logger.info(f"🤖 LLM 输出与历史评论重复，重试：{text}")
                continue
            return text
        return None


def sanitize_comment(text: str, max_len: int = 30) -> str:
    """把 LLM 输出清洗成一条可直接发布的短评论。"""
    if not text:
        return ""
    out = str(text).strip()
    out = re.sub(r"```[a-zA-Z]*|```", "", out)
    out = re.sub(r"https?://\S+", "", out)
    out = re.sub(r"@\S+", "", out)
    out = out.replace("\r", " ").replace("\n", " ")
    out = re.sub(r"\s{2,}", " ", out).strip()
    for prefix in ("评论：", "评论:", "回复：", "回复:", "答：", "答:", "答案：", "答案:"):
        if out.startswith(prefix):
            out = out[len(prefix):].strip()
    # 去掉所有引号/书名号残留（评论正文不需要它们）
    out = re.sub(r"[\"'“”‘’《》〈〉「」『』【】]", "", out)
    out = out.strip("[]（）() \t")
    out = out.strip()
    if len(out) > max_len:
        out = out[:max_len].rstrip("，,。.、！!？?~～  ")
    return out.strip()


def detect_llm_client(verbose: bool = True) -> Optional[LLMClient]:
    """按 AI_PROVIDER → OpenAI 通用变量 → 厂商专属变量的顺序自动装配 LLM 客户端。"""
    forced = (env("AI_PROVIDER", "LLM_PROVIDER") or "").strip().lower()
    forced = _PROVIDER_ALIASES.get(forced, forced)

    order: List[str] = []
    if forced:
        order.append(forced)
    for spec in LLM_PROVIDERS:
        if spec["provider"] not in order:
            order.append(spec["provider"])

    extra_headers: Dict[str, str] = {}
    raw_headers = env("AI_EXTRA_HEADERS", "OPENAI_EXTRA_HEADERS")
    if raw_headers:
        try:
            parsed = json.loads(raw_headers)
            if isinstance(parsed, dict):
                extra_headers = {str(k): str(v) for k, v in parsed.items()}
        except Exception as exc:
            Logger.warning(f"AI_EXTRA_HEADERS 不是合法 JSON，已忽略：{exc}")

    for provider in order:
        spec = next((p for p in LLM_PROVIDERS if p["provider"] == provider), None)
        if spec is None:
            if verbose and forced:
                Logger.warning(f"未知 AI_PROVIDER={forced}，按 OpenAI 兼容接口处理")
            spec = LLM_PROVIDERS[0]
        api_key = env(*spec["key_envs"])
        if not api_key:
            continue

        base_url = env(*spec["base_envs"]) or spec["default_base"]
        model = env(*spec["model_envs"]) or ""
        cfg = {
            "provider": spec["provider"],
            "label": spec["label"],
            "api_key": api_key,
            "base_url": base_url,
            "model": model,
            "temperature": env_float("AI_TEMPERATURE", default=0.9),
            "max_tokens": env_int("AI_MAX_TOKENS", default=0),
            "timeout": env_int("AI_TIMEOUT", default=20),
            "comment_max_len": env_int("MIFAN_COMMENT_MAX_LEN", default=30),
            "system_prompt": env("AI_SYSTEM_PROMPT"),
            "user_template": env("AI_USER_TEMPLATE"),
            "extra_headers": extra_headers,
        }
        if verbose:
            Logger.info(
                f"🤖 LLM 已启用：{spec['label']} | base_url={base_url} | "
                f"model={model or '(自动探测)'}"
            )
        return LLMClient(cfg)

    if verbose:
        key_hint = " / ".join(spec["key_envs"][0] for spec in LLM_PROVIDERS)
        Logger.info("🤖 未检测到 LLM Key，评论使用内置文案库（配置 %s 即可启用 AI）", key_hint)
    return None


# =============================================================================
# 7. 米饭 APP 认证
# =============================================================================

class MiFanAuth:
    """登录 / 登出 / Token 维护。"""

    def __init__(self, username: str, password: str, gid: Optional[int] = None):
        self.username = username
        self.password = password
        self.gid = gid or DEFAULT_GID
        self.token: Optional[str] = None

    # ---- 公共请求头 ----

    @staticmethod
    def _base_headers() -> dict:
        return {
            "User-Agent": USER_AGENT,
            "X-Requested-With": "XMLHttpRequest",
            "Referer": f"{API_HOST}/dist/",
            "Origin": API_HOST,
        }

    def _auth_headers(self, token: str, content_type: str = "json") -> dict:
        headers = self._base_headers()
        if token:
            headers["Authorization"] = token
        headers["Content-Type"] = (
            "application/x-www-form-urlencoded" if content_type == "form"
            else "application/json;charset=utf-8"
        )
        return headers

    def get_headers(self, content_type: str = "json") -> dict:
        if not self.token:
            raise RuntimeError("未登录，无法获取请求头")
        return self._auth_headers(self.token, content_type)

    # ---- 登录 ----

    def login(self) -> bool:
        Logger.info(f"🔑 账号 [{self.username}] 正在登录 ...")
        hashed = hashlib.md5(self.password.encode("utf-8")).hexdigest()
        body = {
            "gid": self.gid,
            "uid": self.username,
            "password": hashed,
            "tad": "",
            "encrypt": "true",
        }
        attempts = [
            # 表单提交（历史可用形式）
            lambda: Http.request(
                "POST", URLS["LOGIN"],
                headers=self._auth_headers("", "form"),
                data=f"gid={self.gid}&uid={self.username}&password={hashed}&tad=&encrypt=true",
            ),
            # JSON 提交（与官方前端一致）
            lambda: Http.request(
                "POST", URLS["LOGIN"],
                headers=self._auth_headers("", "json"),
                json_body=body,
            ),
        ]

        last_message = "未知错误"
        for index, call in enumerate(attempts, start=1):
            result = Http.json(call())
            if not result:
                last_message = "无响应"
                continue
            if result.get("code") == 200:
                token = result.get("token") or (result.get("data") or {}).get("token")
                if not token:
                    last_message = "登录成功但未返回 token"
                    continue
                self.token = str(token)
                Logger.success(f"登录成功（token 前缀：{self.token[:16]}...）")
                return True
            last_message = str(result.get("data") or result.get("message") or result)
            if index == 1:
                Logger.warning(f"表单登录未通过（{last_message}），改用 JSON 方式重试 ...")

        Logger.error(f"登录失败：{last_message}")
        return False

    # ---- 登出 ----

    def logout(self) -> bool:
        if not self.token:
            return True
        Logger.info(f"🔒 账号 [{self.username}] 正在登出 ...")
        result = Http.json(
            Http.request("POST", URLS["LOGOUT"], headers=self._auth_headers(self.token))
        )
        self.token = None
        if result and result.get("code") == 200:
            Logger.success("登出成功")
            return True
        Logger.warning("登出未确认（token 可能已失效），本地会话已清理")
        return False


# =============================================================================
# 8. 业务功能
# =============================================================================

class MiFanClient:
    """签到 / 点赞 / 评论 / 补签 / 余额查询。所有方法返回 (是否成功, 说明)。"""

    def __init__(self, auth: MiFanAuth):
        self.auth = auth
        self.llm: Optional[LLMClient] = None

    # ---- 余额 / 用户信息 ----

    def get_profile(self) -> Optional[dict]:
        result = Http.json(Http.request("POST", URLS["PROFILE"], headers=self.auth.get_headers()))
        if result:
            gold = result.get("gold")
            nickname = result.get("nickname") or "未知"
            Logger.info(f"👤 用户：{nickname} | 米粒：{gold}")
            return result
        Logger.warning("获取用户信息失败")
        return None

    def get_gold(self) -> Optional[int]:
        profile = self.get_profile()
        if profile is None:
            return None
        gold = profile.get("gold")
        try:
            return int(gold)
        except (TypeError, ValueError):
            return None

    # ---- 签到 ----

    def daily_sign(self) -> Tuple[bool, str]:
        Logger.info("📝 执行每日签到 ...")

        status = Http.json(
            Http.request("GET", URLS["SIGN_STATUS"], headers=self.auth.get_headers())
        )
        if status and status.get("code") == 200:
            if str(status.get("data")) in {"1", "True", "true", "已签到"}:
                Logger.success("今日已签到")
                if RUNTIME.get("interactive") and _ask_yes("是否强制重新签到"):
                    Logger.info("用户选择重新签到 ...")
                else:
                    return True, "今日已签到"

        result = Http.json(Http.request("POST", URLS["DAILY_SIGN"], headers=self.auth.get_headers()))
        if result is None:
            return False, "签到请求失败"

        data = result.get("data") if isinstance(result, dict) else None
        if str(data) == "已签到":
            Logger.success("今日已签到（服务端返回）")
            return True, "今日已签到"
        if isinstance(result, dict) and result.get("code") == 200:
            Logger.success(f"签到成功：{data}")
            return True, str(data or "签到成功")

        Logger.warning(f"签到失败：{result}")
        return False, f"签到失败：{data or result}"

    # ---- 动态流 ----

    def fetch_feed_page(self, offset: int = -1, count: int = 20) -> Tuple[List[dict], int]:
        """返回 (items, next_offset)。"""
        resp = Http.request(
            "POST", URLS["FEED"],
            headers={"Content-Type": "application/x-www-form-urlencoded",
                     "Authorization": self.auth.token or ""},
            data={
                "type": "latest",
                "offset": offset,
                "count": count,
                "start_time": 0,
                "end_time": 0,
                "tag_id": "undefined",
                "timestamp": int(time.time()),
            },
            timeout=30,
        )
        parsed = Http.json(resp)
        if not parsed:
            return [], offset
        data = parsed.get("data") or {}
        return list(data.get("current_page") or []), int(data.get("next_offset") or -1)

    # ---- 点赞 ----

    def batch_like(self, count: Optional[int] = None) -> Tuple[bool, str]:
        count = count or LIKE_COUNT
        Logger.info("👍 开始自动点赞 ...")

        items, _ = self.fetch_feed_page(offset=-1, count=20)
        article_ids = []
        for item in items:
            if item.get("cmd_id") != 7003:
                continue
            aid = (item.get("data") or {}).get("article_id")
            if aid:
                try:
                    article_ids.append(int(aid))
                except (TypeError, ValueError):
                    continue

        if not article_ids:
            Logger.warning("动态流中未找到可点赞的文章")
            return True, "无可点赞文章"

        article_ids = sorted(set(article_ids), reverse=True)
        checkpoint = int(load_json(STATE_FILE, {}).get("current_id") or 0)
        if checkpoint:
            Logger.info(f"📄 恢复点赞断点：{checkpoint}")
            targets = [aid for aid in article_ids if aid > checkpoint]
            if not targets:
                Logger.info(f"没有比断点 {checkpoint} 更新的文章，跳过点赞")
                return True, "无新增文章"
            Logger.info(f"断点 {checkpoint} → 发现 {len(targets)} 篇新文章")
        else:
            Logger.info("📄 未找到点赞断点，本次从动态流最新文章开始")
            targets = article_ids

        liked, failed = 0, 0
        for aid in targets[:count]:
            url = URLS["LIKE"].format(id=aid)
            resp = Http.request("POST", url, headers=self.auth.get_headers())
            if resp is not None and resp.status_code == 200:
                liked += 1
                Logger.success(f"点赞 ID {aid}")
            else:
                failed += 1
                code = resp.status_code if resp is not None else "无响应"
                Logger.warning(f"点赞 ID {aid} 异常：{code}")
            time.sleep(random.uniform(*SLEEP_LIKE))

        newest = max(article_ids)
        save_json(STATE_FILE, {"current_id": newest})
        Logger.info(f"📂 点赞断点已更新至 {newest}")

        detail = f"点赞 {liked} 篇（失败 {failed}），断点 {newest}"
        if failed and not liked:
            return False, detail
        return True, detail

    # ---- AI 评论 ----

    def auto_comment(self, max_comments: Optional[int] = None) -> Tuple[bool, str]:
        max_comments = max_comments or COMMENT_COUNT
        self.llm = self.llm or detect_llm_client()
        mode = f"{self.llm.label} AI" if self.llm else "内置文案库"
        Logger.info(f"💬 开始自动评论（{mode}）...")

        start_gold = self.get_gold()
        progress = load_json(PROGRESS_FILE, {}) or {}
        commented = {str(x) for x in (progress.get("commented") or [])}
        recent = [str(x) for x in (progress.get("recent_comments") or [])]
        offset = int(progress.get("offset", -1))

        items, next_offset = self.fetch_feed_page(offset=offset, count=15)
        if not items:
            return False, "获取动态流失败"

        actionable = []
        for item in items:
            if item.get("cmd_id") != 7003:
                continue
            data = item.get("data") or {}
            text = (data.get("text") or "").strip()
            aid = str(data.get("article_id") or "")
            if text and aid and aid not in commented:
                actionable.append((aid, text))

        if not actionable:
            Logger.info("暂无可评论帖子")
            return True, "暂无可评论帖子"

        success_count, failed = 0, 0
        for aid, text in actionable:
            if success_count >= max_comments:
                break

            Logger.info(f"📝 处理帖子 {aid}：{text[:40]}...")
            if self.llm:
                comment = self.llm.generate_comment(text, avoid=set(recent))
                if not comment:
                    Logger.warning("LLM 未产出可用评论，使用内置文案兜底")
                    comment = random.choice(FALLBACK_COMMENTS)
            else:
                comment = random.choice(FALLBACK_COMMENTS)

            resp = Http.request(
                "POST", URLS["COMMENT"],
                headers={"Content-Type": "application/x-www-form-urlencoded",
                         "Authorization": self.auth.token or ""},
                data={"comment_article_id": aid, "post_text": comment, "post_atcount": 0},
                timeout=20,
            )
            res = Http.json(resp)
            if res is None:
                failed += 1
                Logger.warning(f"评论 {aid} 无有效响应")
                time.sleep(10)
                continue

            gold = res.get("gold")
            if res.get("code") == 200 and res.get("data"):
                success_count += 1
                commented.add(aid)
                recent.append(comment)
                recent = recent[-30:]
                progress.update({
                    "commented": sorted(commented, key=lambda x: int(x) if str(x).isdigit() else 0),
                    "offset": next_offset,
                    "recent_comments": recent,
                    "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                })
                save_json(PROGRESS_FILE, progress)
                Logger.success(f"评论 {aid}：{comment} | 米粒：{gold}")
                if gold == -1:
                    Logger.info("本次已无法获得米粒，停止评论")
                    break
                time.sleep(random.uniform(*SLEEP_COMMENT))
            else:
                failed += 1
                Logger.warning(f"评论 {aid} 失败：{res}")
                time.sleep(15)

        end_gold = self.get_gold()
        if start_gold is not None and end_gold is not None:
            Logger.info(f"📈 评论模块米粒变化：{start_gold} → {end_gold}（+{end_gold - start_gold}）")

        detail = f"评论 {success_count} 条（失败 {failed}）"
        Logger.info(f"💬 自动评论完成，{detail}")
        if failed and not success_count:
            return False, detail
        return True, detail

    # ---- 补签 ----

    def auto_repair_sign(self) -> Tuple[bool, str]:
        Logger.info("🛠️  开始自动补签 ...")
        result = Http.json(
            Http.request("GET", URLS["COMPLEMENT_RECENT"], headers=self.auth.get_headers())
        )
        days = (result or {}).get("data") or []
        if not days:
            Logger.warning("获取补签数据失败")
            return False, "获取补签数据失败"

        missed = []
        for day in days:
            if not isinstance(day, dict) or not day:
                continue
            date, state = list(day.items())[0]
            if str(state) in {"0", "False", "false"}:
                missed.append(date)

        if not missed:
            Logger.success("无需补签，最近都已签到")
            return True, "无需补签"

        ok, fail = 0, 0
        Logger.info(f"发现 {len(missed)} 天需要补签：{', '.join(missed)}")
        for date in missed:
            resp = Http.request(
                "POST", URLS["COMPLEMENT_DO"],
                headers=self.auth._auth_headers(self.auth.token or "", "form"),
                data=f"complement_date={date}",
            )
            res = Http.json(resp)
            if res and res.get("code") == 200:
                ok += 1
                Logger.success(f"补签 {date} 成功")
            else:
                fail += 1
                Logger.warning(f"补签 {date} 失败：{res}")
            time.sleep(3)

        detail = f"补签成功 {ok} 天（失败 {fail}）"
        return (ok > 0 or fail == 0), detail


# =============================================================================
# 9. 账号编排
# =============================================================================

TASK_ORDER = ["sign", "like", "comment", "repair"]
TASK_LABEL = {
    "sign": "📝 签到",
    "like": "👍 点赞",
    "comment": "💬 评论",
    "repair": "🛠️  补签",
}
TASK_ALIASES = {
    "0": TASK_ORDER, "all": TASK_ORDER, "全部": TASK_ORDER, "allin": TASK_ORDER,
    "1": ["sign"], "sign": ["sign"], "signin": ["sign"], "dailysign": ["sign"], "签到": ["sign"],
    "2": ["like"], "like": ["like"], "likes": ["like"], "点赞": ["like"],
    "3": ["comment"], "comment": ["comment"], "comments": ["comment"],
    "ai": ["comment"], "评论": ["comment"],
    "4": ["repair"], "repair": ["repair"], "complement": ["repair"], "补签": ["repair"],
}


def resolve_tasks(spec: Optional[str]) -> List[str]:
    """把 '0' / 'sign,like' / '签到 点赞' 之类的写法解析成规范任务列表。"""
    raw = spec if spec is not None else env("MIFAN_TASK", "MIFAN_TASKS")
    if raw is None or str(raw).strip() == "":
        raw = "sign,like,comment"

    picked: List[str] = []
    for part in re.split(r"[,\s;；+|/]+", str(raw).strip()):
        if not part:
            continue
        key = part.strip()
        mapped = TASK_ALIASES.get(key.lower()) or TASK_ALIASES.get(key)
        if not mapped:
            Logger.warning(f"未知任务名：{part}（已忽略）")
            continue
        for task in mapped:
            if task not in picked:
                picked.append(task)

    if env_bool("ENABLE_REPAIR") and "repair" not in picked:
        picked.append("repair")
    return [task for task in TASK_ORDER if task in picked]


def split_multi(value: Optional[str]) -> List[str]:
    if not value:
        return []
    return [item.strip() for item in re.split(r"[;；\n]+", value) if item.strip()]


def collect_accounts(
    user_override: Optional[str] = None,
    password_override: Optional[str] = None,
    gid_override: Optional[int] = None,
) -> List[Tuple[str, str, int]]:
    """汇总所有账号来源，返回 [(user, password, gid), ...]。

    支持三种写法：
      1) MIFAN_USER=u1;u2  +  MIFAN_PASSWORD=p1;p2
      2) MIFAN_ACCOUNTS=u1:p1;u2:p2      （推荐，一行搞定）
      3) 命令行 --user / --password
    """
    gid = gid_override or DEFAULT_GID
    accounts: List[Tuple[str, str, int]] = []

    # 1) 组合式账号：user:pass[:gid] 或 user#pass[:gid]
    for raw in split_multi(env("MIFAN_ACCOUNTS", "MIFAN_ACCOUNT")):
        parts = [p.strip() for p in re.split(r"[:\uFF1A#]", raw, maxsplit=2)]
        if len(parts) >= 2 and parts[0] and parts[1]:
            row_gid = gid
            if len(parts) == 3 and parts[2]:
                try:
                    row_gid = int(parts[2])
                except ValueError:
                    row_gid = gid
            accounts.append((parts[0], parts[1], row_gid))
        else:
            Logger.warning("MIFAN_ACCOUNTS 中的条目格式应为 user:password，已忽略")

    # 2) 分离式账号
    users = split_multi(user_override or env("MIFAN_USER", "MIFAN_USERNAME", "MIFAN_USERS"))
    passwords = split_multi(password_override or env("MIFAN_PASSWORD", "MIFAN_PASS", "MIFAN_PASSWORDS"))
    if users or passwords:
        if len(users) != len(passwords):
            Logger.error("MIFAN_USER 与 MIFAN_PASSWORD 数量不一致（多账号请用 ; 分隔且一一对应）")
            return []
        accounts.extend((u, p, gid) for u, p in zip(users, passwords))

    # 去重（按账号名保留首个）
    seen, unique = set(), []
    for user, password, row_gid in accounts:
        if user in seen:
            continue
        seen.add(user)
        unique.append((user, password, row_gid))
    return unique


class MiFanAccount:
    """单账号全流程：登录 → 任务 → 统计 → 登出。"""

    def __init__(self, username: str, password: str, gid: Optional[int] = None):
        self.username = username
        self.password = password
        self.gid = gid or DEFAULT_GID
        self.auth = MiFanAuth(username, password, self.gid)
        self.client = MiFanClient(self.auth)

    def run(self, tasks: Sequence[str]) -> Dict[str, Any]:
        Logger.info("=" * 56)
        Logger.info(f"🚀 开始处理账号：{self.username}")
        record: Dict[str, Any] = {
            "account": self.username,
            "status": False,
            "message": "",
            "gold_before": None,
            "gold_after": None,
            "task_results": [],
        }

        if not self.auth.login():
            record["message"] = "登录失败"
            return record

        try:
            record["gold_before"] = self.client.get_gold()

            for task in tasks:
                Logger.info(f"──── 执行任务：{TASK_LABEL.get(task, task)} ────")
                try:
                    ok, detail = self._run_task(task)
                except Exception as exc:
                    ok, detail = False, f"异常：{exc}"
                    Logger.error(f"任务 {task} 执行异常：{exc}")
                record["task_results"].append({"task": task, "ok": bool(ok), "detail": detail})

            record["gold_after"] = self.client.get_gold()
        finally:
            self.auth.logout()

        record["status"] = True
        record["message"] = "执行完成"
        return record

    def _run_task(self, task: str) -> Tuple[bool, str]:
        if task == "sign":
            return self.client.daily_sign()
        if task == "like":
            return self.client.batch_like()
        if task == "comment":
            return self.client.auto_comment()
        if task == "repair":
            return self.client.auto_repair_sign()
        return False, f"未知任务：{task}"


# =============================================================================
# 10. 交互辅助 & 主入口
# =============================================================================

def stdin_is_tty() -> bool:
    """安全判断 stdin 是否为可交互终端（青龙 / CI / 重定向都不会误判为交互）。"""
    try:
        return bool(sys.stdin and sys.stdin.isatty())
    except Exception:
        return False


def _ask_yes(prompt: str, default: bool = False) -> bool:
    """仅在真实 TTY 下提问，青龙 / CI 环境直接返回默认值，绝不死等输入。"""
    if not stdin_is_tty():
        return default
    try:
        answer = input(f"{prompt} (y/n): ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return default
    return answer in {"y", "yes"}


def _ask(prompt: str, default: str = "") -> str:
    """带默认值与 EOF 保护的 input，永不让脚本因输入流关闭而崩。"""
    try:
        value = input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        Logger.warning("输入流已关闭，改用默认值")
        return default
    return value or default


def interactive_setup() -> Tuple[List[Tuple[str, str, int]], List[str]]:
    print("=" * 56)
    print("      🍚 米饭APP日常脚本 — 交互模式")
    print("=" * 56)

    username = _ask("\n请输入账号（用户名）: ")
    if not username:
        Logger.error("账号不能为空")
        return [], []
    password = _ask("请输入密码: ")
    if not password:
        Logger.error("密码不能为空")
        return [], []

    gid_raw = _ask(f"请输入区服 GID（回车默认 {DEFAULT_GID}）: ")
    try:
        gid = int(gid_raw) if gid_raw else DEFAULT_GID
    except ValueError:
        gid = DEFAULT_GID

    print("\n可选任务:")
    print("  0. 全部执行（签到+点赞+评论+补签）")
    print("  1. 每日签到")
    print("  2. 自动点赞")
    print("  3. 自动评论")
    print("  4. 自动补签")
    print("  5. 签到+点赞+评论（青龙默认组合）")
    choice = _ask("\n请选择 (0-5，回车默认 5): ", default="5")
    tasks = resolve_tasks("sign,like,comment" if choice == "5" else choice)
    if not tasks:
        print("❌ 无效选择")
        return [], []

    if not detect_llm_client(verbose=False):
        key = _ask("\n如需 AI 评论请输入 API Key（回车使用内置文案库）: ")
        if key:
            _ENV["openai_api_key"] = key
            _ENV.setdefault("ai_provider", "openai")
    return [(username, password, gid)], tasks


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog=SCRIPT_NAME,
        description="🍚 米饭APP日常脚本 · 青龙面板适配版",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例：\n"
            "  python3 mifan_optimized.py                       # 按环境变量执行（青龙默认）\n"
            "  python3 mifan_optimized.py --tasks sign,like     # 只做签到+点赞\n"
            "  python3 mifan_optimized.py --check-llm           # 只测试 LLM 连通性\n"
            "  python3 mifan_optimized.py -u 账号 -p 密码        # 临时单账号\n"
        ),
    )
    parser.add_argument("-u", "--user", help="账号（多个用 ; 分隔，优先级高于环境变量）")
    parser.add_argument("-p", "--password", help="密码（与账号一一对应）")
    parser.add_argument("-g", "--gid", type=int, help=f"区服 GID（默认 {DEFAULT_GID}）")
    parser.add_argument("-t", "--tasks", help="任务：sign,like,comment,repair / 0-4 / all")
    parser.add_argument("-i", "--interactive", action="store_true", help="强制进入交互模式")
    parser.add_argument("--no-notify", action="store_true", help="本次运行不推送通知")
    parser.add_argument("--check-llm", action="store_true", help="只测试 LLM 连通性后退出")
    parser.add_argument("--version", action="version", version=f"mifan_optimized {__version__}")
    return parser.parse_args(list(argv))


def build_summary(results: Sequence[Dict[str, Any]], tasks: Sequence[str]) -> Tuple[str, int, int]:
    success_count = sum(1 for r in results if r.get("status"))
    fail_count = len(results) - success_count

    lines = [
        f"运行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"任务: {'/'.join(TASK_LABEL.get(t, t) for t in tasks) or '无'}",
        f"账号总数: {len(results)} | 成功: {success_count} | 失败: {fail_count}",
        "",
    ]
    for record in results:
        account = record.get("account", "?")
        flag = "✅ 成功" if record.get("status") else "❌ 失败"
        lines.append(f"【{account}】{flag} {record.get('message', '')}".rstrip())
        before, after = record.get("gold_before"), record.get("gold_after")
        if before is not None and after is not None:
            lines.append(f"   米粒: {before} → {after}（{after - before:+d}）")
        for item in record.get("task_results") or []:
            mark = "✔" if item.get("ok") else "✘"
            lines.append(f"   {mark} {TASK_LABEL.get(item['task'], item['task'])}: {item.get('detail', '')}")
        lines.append("")
    return "\n".join(lines).strip(), success_count, fail_count


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    Logger.init()

    Logger.info(f"🍚 米饭APP日常脚本 v{__version__}")
    Logger.info(f"   数据目录: {DATA_DIR}")

    # ---- 仅测试 LLM ----
    if args.check_llm:
        client = detect_llm_client()
        if client is None:
            Logger.error("未检测到任何 LLM 配置，请设置 OPENAI_API_KEY / OPENAI_BASE_URL")
            return 2
        reply = client.chat(
            [{"role": "user", "content": "回复两个字：正常"}], temperature=0, max_tokens=16
        )
        if reply:
            Logger.success(f"LLM 连通性正常，返回：{reply}")
            return 0
        Logger.error("LLM 连通性测试失败，请检查 base_url / api_key / model")
        return 1

    # ---- 任务解析 ----
    tasks = resolve_tasks(args.tasks)
    if not tasks:
        Logger.error("没有可执行的任务，请检查 MIFAN_TASK / --tasks 参数")
        return 2

    # ---- 账号收集 ----
    accounts = collect_accounts(args.user, args.password, args.gid)
    if not accounts and (args.interactive or (not args.user and stdin_is_tty())):
        RUNTIME["interactive"] = True
        accounts, interactive_tasks = interactive_setup()
        if interactive_tasks:
            tasks = interactive_tasks
    if not accounts:
        Logger.error("未配置账号，脚本退出（退出码 2）")
        Logger.info("青龙配置：面板 → 环境变量 → 新增")
        Logger.info("  MIFAN_USER=账号1;账号2")
        Logger.info("  MIFAN_PASSWORD=密码1;密码2")
        Logger.info("  （或一行搞定：MIFAN_ACCOUNTS=账号1:密码1;账号2:密码2）")
        return 2

    Logger.info(f"👥 账号数量: {len(accounts)} | 任务: {', '.join(tasks)}")
    if RUNTIME["interactive"]:
        Logger.info("🖥️  交互模式：已签到时会询问是否重签")

    # ---- 执行 ----
    results: List[Dict[str, Any]] = []
    for index, (user, password, gid) in enumerate(accounts):
        account = MiFanAccount(user, password, gid)
        try:
            results.append(account.run(tasks))
        except Exception as exc:
            Logger.error(f"账号 {user} 执行异常：{exc}")
            results.append({
                "account": user, "status": False, "message": f"异常：{exc}",
                "gold_before": None, "gold_after": None, "task_results": [],
            })
        if index < len(accounts) - 1 and ACCOUNT_GAP_S > 0:
            Logger.info(f"⏳ 账号间等待 {ACCOUNT_GAP_S:.0f} 秒 ...")
            time.sleep(ACCOUNT_GAP_S)

    summary, success_count, fail_count = build_summary(results, tasks)
    Logger.info("=" * 56)
    Logger.info("📊 执行汇总")
    for line in summary.splitlines():
        Logger.info(line)
    Logger.info("=" * 56)

    # ---- 通知 ----
    notify_mode = (env("MIFAN_NOTIFY", default="auto") or "auto").lower()
    if args.no_notify:
        notify_mode = "never"
    want_success = env_bool("MIFAN_SUCCESS_NOTIFY", default=False)
    want_fail = env_bool("MIFAN_FAIL_NOTIFY", default=False)
    if notify_mode in ("always", "success"):
        want_success = True
    if notify_mode in ("always", "fail"):
        want_fail = True

    if notify_mode != "never":
        if success_count and want_success:
            Notifier.send(f"米饭APP通知（成功 {success_count}）", summary)
        if fail_count and want_fail:
            Notifier.send(f"米饭APP通知（失败 {fail_count}）", summary)
        if not (want_success or want_fail):
            Logger.info("未开启推送（MIFAN_SUCCESS_NOTIFY / MIFAN_FAIL_NOTIFY），结果仅记录日志")

    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        Logger.warning("已被手动中断")
        sys.exit(130)
    except Exception as exc:  # 兜底：青龙里任何异常都要有可读日志 + 非零退出码
        Logger.error(f"脚本未捕获异常：{exc}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
