# -*- coding: utf-8 -*-
"""离线全链路测试：stub 掉 requests，模拟米饭 API 全流程，验证 main() 的行为与退出码。

运行：python flow_test.py
不联网、不写入真实状态文件（数据目录指向临时目录）。
"""
import json
import os
import shutil
import sys
import tempfile
import types

CALLS = []
MODE = {"login_ok": True, "signed": False}

DATA_DIR = os.path.join(tempfile.gettempdir(), "mifan_flow_test")
shutil.rmtree(DATA_DIR, ignore_errors=True)
os.makedirs(DATA_DIR, exist_ok=True)


class FakeResp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = json.dumps(self._payload, ensure_ascii=False)
        self.content = self.text.encode("utf-8")
        self.headers = {}
        self.ok = 200 <= status_code < 300

    def json(self):
        return self._payload


PROFILE_CALLS = {"n": 0}


class FakeSession:
    def __init__(self):
        self.headers = {}

    def mount(self, *a, **k):
        pass

    def request(self, **kw):
        url = kw.get("url") or ""
        method = (kw.get("method") or "GET").upper()
        CALLS.append((method, url))

        # ---- 米饭 API ----
        if url.endswith("/api/v1/login"):
            if MODE["login_ok"]:
                return FakeResp(200, {"code": 200, "token": "tok-abcdefghijklmnop"})
            return FakeResp(200, {"code": 501, "data": "账号或密码错误"})
        if url.endswith("/api/v1/logout"):
            return FakeResp(200, {"code": 200})
        if url.endswith("/api/v1/profile"):
            PROFILE_CALLS["n"] += 1
            gold = 100 if PROFILE_CALLS["n"] == 1 else 120
            return FakeResp(200, {"gold": gold, "nickname": "测试号"})
        if url.endswith("/api/v1/event/dailysign/status/"):
            return FakeResp(200, {"code": 200, "data": 1 if MODE["signed"] else 0})
        if url.endswith("/api/v1/event/dailysign/"):
            return FakeResp(200, {"code": 200, "data": "已签到" if MODE["signed"] else "签到成功"})
        if url.endswith("/api/v1/event/dailysign/recent"):
            return FakeResp(200, {"code": 200, "data": [{"2026-11-05": 0}, {"2026-11-04": 1}]})
        if url.endswith("/api/v1/event/dailysign/complement"):
            return FakeResp(200, {"code": 200, "data": "补签成功"})
        if url.endswith("/api/v1/feed"):
            return FakeResp(200, {"data": {
                "current_page": [{"cmd_id": 7003, "data": {"article_id": 9001, "text": "今天天气不错"}}],
                "next_offset": 0,
            }})
        if "/api/v1/article/likes/" in url:
            return FakeResp(200, {"code": 200})
        if url.endswith("/api/v1/article/comment"):
            return FakeResp(200, {"code": 200, "data": "ok", "gold": 3})

        # ---- LLM ----
        if url.endswith("/models"):
            return FakeResp(200, {"data": [{"id": "gpt-4o-mini"}]})
        if url.endswith("/chat/completions"):
            return FakeResp(200, {"choices": [{"message": {"content": "这条动态写得挺有意思！"}}]})

        return FakeResp(404, {"error": "unmocked", "url": url})


requests = types.ModuleType("requests")
requests.Session = FakeSession
requests.post = lambda url, **kw: FakeSession().request(method="POST", url=url)
requests.get = lambda url, **kw: FakeSession().request(method="GET", url=url)
requests.request = lambda **kw: FakeSession().request(**kw)
requests.adapters = types.SimpleNamespace(HTTPAdapter=lambda **kw: None)
requests.exceptions = types.SimpleNamespace(InsecureRequestWarning=Exception)

urllib3 = types.ModuleType("urllib3")
urllib3.exceptions = types.SimpleNamespace(InsecureRequestWarning=Exception)
urllib3.disable_warnings = lambda *a, **k: None

sys.modules["requests"] = requests
sys.modules["urllib3"] = urllib3

os.environ.update({
    "MIFAN_ACCOUNTS": "tester:pwd",
    "MIFAN_DATA_DIR": DATA_DIR,
    "MIFAN_LIKE_SLEEP_MIN": "0",
    "MIFAN_LIKE_SLEEP_MAX": "0",
    "MIFAN_COMMENT_SLEEP_MIN": "0",
    "MIFAN_COMMENT_SLEEP_MAX": "0",
    "MIFAN_ACCOUNT_GAP": "0",
    "MIFAN_RETRY_DELAY": "0",
    "OPENAI_API_KEY": "sk-stub",
    "OPENAI_BASE_URL": "https://gw.example.com/v1",
    "OPENAI_MODEL": "gpt-4o-mini",
})

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mifan_optimized as m

failures = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  <- {extra}"))
    if not cond:
        failures.append(name)


# ============ 用例 1：全任务成功 ============
print("\n===== 用例1：签到 + 点赞 + 评论 + 补签 =====")
code = m.main(["--tasks", "0", "--no-notify"])
check("退出码 0（全部成功）", code == 0, code)

state = json.load(open(os.path.join(DATA_DIR, "request_state.json"), encoding="utf-8"))
check("点赞断点落盘", state.get("current_id") == 9001, state)

progress = json.load(open(os.path.join(DATA_DIR, "comment_progress.json"), encoding="utf-8"))
check("评论进度落盘", "9001" in progress.get("commented", []), progress)
check("评论内容去重池记录", progress.get("recent_comments"), progress)

urls = [u for _, u in CALLS]
for required in ("/api/v1/login", "/api/v1/profile", "/api/v1/event/dailysign/",
                 "/api/v1/feed", "/api/v1/article/likes/9001/", "/api/v1/article/comment",
                 "/api/v1/event/dailysign/recent", "/api/v1/event/dailysign/complement",
                 "/api/v1/logout"):
    check(f"调用到 {required}", any(u.endswith(required) for u in urls), urls[:6] + urls[-6:])

# ============ 用例 2：已签到不重复签到 ============
print("\n===== 用例2：已签到（幂等） =====")
MODE["signed"] = True
before = len([u for _, u in CALLS if u.endswith("/api/v1/event/dailysign/")])
code = m.main(["--tasks", "sign", "--no-notify"])
after = len([u for _, u in CALLS if u.endswith("/api/v1/event/dailysign/")])
check("已签到时不再 POST 签到", after == before, (before, after))
check("已签到仍算成功", code == 0, code)
MODE["signed"] = False

# ============ 用例 3：登录失败 → 退出码 1 ============
print("\n===== 用例3：登录失败 =====")
MODE["login_ok"] = False
code = m.main(["--tasks", "sign", "--no-notify"])
check("退出码 1（有账号失败）", code == 1, code)
MODE["login_ok"] = True

# ============ 用例 4：账号数量不一致 → 退出码 2 ============
print("\n===== 用例4：账号配置错误 =====")
m._ENV["mifan_accounts"] = ""
m._ENV["mifan_user"] = "a;b"
m._ENV["mifan_password"] = "only-one"
code = m.main(["--tasks", "sign", "--no-notify", "-i"])
check("退出码 2（配置缺失）", code == 2, code)

shutil.rmtree(DATA_DIR, ignore_errors=True)
print()
print("HTTP 调用总数:", len(CALLS))
print("失败项:", failures if failures else "无")
sys.exit(1 if failures else 0)
