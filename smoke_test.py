# -*- coding: utf-8 -*-
"""离线冒烟测试：用 stub 替换 requests，验证 mifan_optimized 的纯逻辑与自适应分支。"""
import json
import os
import sys
import types

# ---------- stub requests / urllib3 ----------
CALLS = []
MODE = {"all_fail": False}


class FakeResp:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text or json.dumps(self._payload, ensure_ascii=False)
        self.content = self.text.encode("utf-8")
        self.headers = {}
        self.ok = 200 <= status_code < 300

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class FakeAdapter:
    def __init__(self, **kwargs):
        pass


class FakeSession:
    def __init__(self):
        self.headers = {}

    def mount(self, *a, **k):
        pass

    def request(self, **kw):
        url = kw.get("url") or ""
        method = (kw.get("method") or "GET").upper()
        CALLS.append(("session.request", method, url))
        if MODE["all_fail"]:
            return FakeResp(404, {"error": "no route"})
        # 演示 endpoint 自适应：/v1/ 路径 404，裸路径 200
        if "/v1/" in url:
            return FakeResp(404, {"error": "not found"})
        if url.endswith("/models"):
            return FakeResp(200, {"data": [{"id": "text-embedding-3-small"}, {"id": "gpt-4o-mini"}]})
        return FakeResp(200, {"choices": [{"message": {"content": '  “学到了！”\n'}}]})


requests = types.ModuleType("requests")
requests.Session = FakeSession
requests.post = lambda url, **kw: FakeSession().request(method="POST", url=url)
requests.get = lambda url, **kw: FakeSession().request(method="GET", url=url)
requests.request = lambda **kw: FakeSession().request(**kw)
requests.adapters = types.SimpleNamespace(HTTPAdapter=FakeAdapter)
requests.exceptions = types.SimpleNamespace(InsecureRequestWarning=Exception)

urllib3 = types.ModuleType("urllib3")
urllib3.exceptions = types.SimpleNamespace(InsecureRequestWarning=Exception)
urllib3.disable_warnings = lambda *a, **k: None

sys.modules["requests"] = requests
sys.modules["urllib3"] = urllib3

# ---------- 环境变量 ----------
os.environ.update({
    "MIFAN_ACCOUNTS": "u1:p1;u2:p2:688",
    "MIFAN_USER": "u3;u4",
    "MIFAN_PASSWORD": "p3;p4",
    "OPENAI_API_KEY": "sk-test",
    "OPENAI_BASE_URL": "https://gateway.example.com",   # 故意不带 /v1，验证自适应
    "AI_PROVIDER": "custom",
})

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mifan_optimized as m

failures = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  <- {extra}"))
    if not cond:
        failures.append(name)


# 1. 环境变量读取
check("env 大小写不敏感", m.env("mifan_usEr") == "u3;u4", m.env("mifan_usEr"))
check("env_* 多名称按序回退", m.env("NOT_EXIST", "MIFAN_GID", default="x") == "x")
check("env_bool 默认值", m.env_bool("X_UNSET", default=True) is True)

# 2. 任务解析
check("tasks 0 = 全部", m.resolve_tasks("0") == ["sign", "like", "comment", "repair"], m.resolve_tasks("0"))
check("tasks 别名+大小写", m.resolve_tasks("签到, LIKE ,3") == ["sign", "like", "comment"], m.resolve_tasks("签到, LIKE ,3"))
check("tasks 未知忽略", m.resolve_tasks("sign,bogus") == ["sign"])
check("tasks 默认", m.resolve_tasks(None) == ["sign", "like", "comment"], m.resolve_tasks(None))

# 3. 账号解析
accs = m.collect_accounts()
check("账号合并 4 个", len(accs) == 4, accs)
check("账号 gid 默认继承", accs[0][2] == 689)
check("账号 gid 行内覆盖", accs[1][2] == 688, accs[1])
check("账号去重", len(m.collect_accounts("u1;u2", "p1;p2")) == 2)
check("分割符解析", m.split_multi("a; b；c\nd") == ["a", "b", "c", "d"])

# 4. 评论清洗
clean = m.sanitize_comment('评论："学到了！"\n谢谢', 30)
check("清洗前缀/引号/换行", clean == "学到了！ 谢谢", repr(clean))
check("清洗超长截断", len(m.sanitize_comment("啊" * 50, 10)) == 10)
check("清洗 markdown 与链接", "http" not in m.sanitize_comment("```\n不错 https://a.b/c\n```", 30))
check("空输入安全", m.sanitize_comment("", 10) == "")

# 5. URL 自适应
check("base 无 v1 展开", m.LLMClient.build_urls("https://x.com") ==
      ["https://x.com/v1/chat/completions", "https://x.com/chat/completions"],
      m.LLMClient.build_urls("https://x.com"))
check("base 带 v1", m.LLMClient.build_urls("https://x.com/v1/") == ["https://x.com/v1/chat/completions"])
check("完整地址直用", m.LLMClient.build_urls("https://x.com/v1/chat/completions") ==
      ["https://x.com/v1/chat/completions"])
check("无协议补 https", m.LLMClient.build_urls("api.x.com/v1")[0].startswith("https://"))

# 6. LLM 客户端装配与调用
client = m.detect_llm_client()
check("LLM 客户端已装配", client is not None)
check("AI_PROVIDER 别名归一化", client and client.provider == "openai", client and client.provider)
reply = client.chat([{"role": "user", "content": "hi"}])
check("404 后自适应切换 endpoint", reply == "学到了！", repr(reply))
check("记住可用 endpoint", client._url_idx == 1, client._url_idx)
check("模型自动探测过滤 embedding", client.model == "gpt-4o-mini", client.model)
check("评论生成（含清洗）", client.generate_comment("今天天气不错") == "学到了！")
check("额外请求头为 dict", isinstance(client.extra_headers, dict))

MODE["all_fail"] = True
dead = m.LLMClient({"provider": "openai", "label": "dead", "api_key": "k",
                    "base_url": "https://dead.example.com", "timeout": 2})
check("全部 endpoint 失败时返回 None", dead.chat([{"role": "user", "content": "hi"}]) is None)
MODE["all_fail"] = False

# 7. 未配置 Key 时不装配
saved = m._ENV.pop("openai_api_key")
check("无 Key 返回 None", m.detect_llm_client(verbose=False) is None)
m._ENV["openai_api_key"] = saved

# 8. CLI / 汇总
args = m.parse_args([])
check("CLI 默认值", args.user is None and args.tasks is None and args.check_llm is False)
check("CLI --tasks", m.parse_args(["--tasks", "like"]).tasks == "like")
check("CLI --check-llm", m.parse_args(["--check-llm"]).check_llm is True)
summary, ok, bad = m.build_summary(
    [{"account": "u1", "status": True, "message": "ok", "gold_before": 1, "gold_after": 3,
      "task_results": [{"task": "sign", "ok": True, "detail": "签到成功"}]},
     {"account": "u2", "status": False, "message": "登录失败", "gold_before": None,
      "gold_after": None, "task_results": []}], ["sign"])
check("汇总成功/失败计数", (ok, bad) == (1, 1), (ok, bad))
check("汇总包含米粒变化", "米粒: 1 → 3（+2）" in summary, summary)

print()
print("HTTP 调用轨迹:", CALLS)
print("失败项:", failures if failures else "无")
sys.exit(1 if failures else 0)
