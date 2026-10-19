"""通过本机 Codex App Server 读取账号额度，不读取或回传登录凭据。"""

import json
import math
import os
from pathlib import Path
import queue
import shutil
import subprocess
import threading
import time


# CLI 接受的配置值，不代表每个模型都支持全部级别。
REASONING_EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra")


def normalize_models(models, config):
    """只暴露模型选择所需元数据，不回传完整本机配置。"""
    choices = []
    seen = set()
    for item in models:
        model = item.get("model")
        if not isinstance(model, str) or not model or item.get("hidden") or model in seen:
            continue
        seen.add(model)
        efforts = list(dict.fromkeys(
            entry["reasoningEffort"] for entry in item.get("supportedReasoningEfforts", [])
            if isinstance(entry.get("reasoningEffort"), str)
        ))
        choices.append({"value": model, "label": item.get("displayName") or model,
                        "efforts": efforts, "defaultEffort": item.get("defaultReasoningEffort") or ""})
    recommended = next((item.get("model") for item in models if item.get("isDefault")), "")
    default_model = os.environ.get("KDT_AI_MODEL") or config.get("model") or recommended
    default_effort = config.get("model_reasoning_effort") or ""
    if default_model and default_model not in seen:
        # 本地配置可以比缓存目录更新，也可以来自自定义提供商，不能借用其他模型的能力。
        choices.append({"value": default_model, "label": default_model + "（本地配置）",
                        "efforts": list(REASONING_EFFORTS), "defaultEffort": default_effort,
                        "effortsVerified": False})
    return {"models": choices,
            "defaultModel": default_model,
            "defaultEffort": default_effort, "error": None}


def normalize_quota(response):
    buckets = response.get("rateLimitsByLimitId") or {}
    snapshot = buckets.get("codex") or response.get("rateLimits") or {}
    windows = []
    for key in ("primary", "secondary"):
        window = snapshot.get(key)
        if not isinstance(window, dict):
            continue
        used = window.get("usedPercent")
        if isinstance(used, bool) or not isinstance(used, (int, float)) or not math.isfinite(used):
            continue
        windows.append({
            "id": key,
            "remainingPercent": max(0, min(100, 100 - used)),
            "durationMins": window.get("windowDurationMins"),
            "resetsAt": window.get("resetsAt"),
        })
    return {
        "remainingPercent": min((window["remainingPercent"] for window in windows), default=None),
        "windows": windows,
        "updatedAt": int(time.time()),
        "error": None if windows else "本地 Codex 未提供账号额度",
    }


class CodexQuotaClient:
    def __init__(self, stop):
        executable = shutil.which(os.environ.get("KDT_AI_CODEX", "codex"))
        if not executable:
            raise RuntimeError("未找到 Codex CLI")
        self.stop = stop
        self.sequence = 0
        self.messages = queue.Queue()
        self.process = subprocess.Popen(
            [executable, "app-server"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            cwd=str(Path(__file__).resolve().parents[2]),
            stderr=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
            start_new_session=os.name != "nt",
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
        )
        self.reader = threading.Thread(target=self.read, daemon=True)
        self.reader.start()
        try:
            self.request("initialize", {"clientInfo": {"name": "kdt_studio", "version": "1.1.0"}})
            self.send({"method": "initialized"})
        except BaseException:
            self.close()
            raise

    def read(self):
        for line in self.process.stdout:
            try:
                self.messages.put(json.loads(line))
            except ValueError:
                pass
        self.messages.put(None)

    def send(self, message):
        self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()

    def request(self, method, params):
        self.sequence += 1
        self.send({"id": self.sequence, "method": method, "params": params})
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if self.stop.exists():
                raise InterruptedError("额度读取已停止")
            try:
                message = self.messages.get(timeout=.1)
            except queue.Empty:
                continue
            if message is None:
                raise RuntimeError("Codex App Server 已退出")
            if message.get("id") == self.sequence:
                if "error" in message:
                    raise RuntimeError("本地 Codex 暂时无法提供账号额度，请检查登录方式与网络")
                return message.get("result", {})
        raise TimeoutError("读取本地 Codex 额度超时")

    def catalog(self):
        models = []
        cursor = None
        seen = set()
        while True:
            params = {"limit": 100, "includeHidden": False}
            if cursor:
                params["cursor"] = cursor
            page = self.request("model/list", params)
            models.extend(page.get("data", []))
            cursor = page.get("nextCursor")
            if not cursor:
                break
            if cursor in seen:
                raise RuntimeError("Codex 模型列表分页异常")
            seen.add(cursor)
        config = self.request("config/read", {"includeLayers": False, "cwd": str(Path(__file__).resolve().parents[2])})
        return normalize_models(models, config.get("config", {}))

    def close(self):
        if self.process.stdin and not self.process.stdin.closed:
            self.process.stdin.close()
        try:
            self.process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        self.reader.join(timeout=2)
        self.process.stdout.close()


def watch_quota(stop, emit):
    client = None
    next_read = 0
    next_catalog = 0
    try:
        while not stop.exists():
            if time.monotonic() >= next_read:
                try:
                    if client is None:
                        client = CodexQuotaClient(stop)
                        next_catalog = 0
                    quota = normalize_quota(client.request("account/rateLimits/read", {}))
                    emit("quota", quota=quota)
                except InterruptedError:
                    break
                except Exception as error:
                    emit("quota", quota={"remainingPercent": None, "windows": [], "error": str(error), "updatedAt": int(time.time())})
                    # 某些登录方式没有额度接口，但仍能读取模型目录。
                    if client and client.process.poll() is not None:
                        client.close()
                        client = None
                next_read = time.monotonic() + 30
            if client is not None and time.monotonic() >= next_catalog:
                try:
                    emit("catalog", catalog=client.catalog())
                except InterruptedError:
                    break
                except Exception:
                    emit("catalog", catalog={"models": [], "defaultModel": os.environ.get("KDT_AI_MODEL", ""),
                                             "defaultEffort": "", "error": "无法读取本地 Codex 模型列表，请检查 CLI 版本和网络"})
                next_catalog = time.monotonic() + 300
            if client is None:
                time.sleep(.1)
                continue
            try:
                event = client.messages.get(timeout=.1)
            except queue.Empty:
                continue
            if event is None:
                client.close()
                client = None
            elif event.get("method") == "account/rateLimits/updated":
                emit("quota", quota=normalize_quota(event.get("params", {})))
    finally:
        if client:
            client.close()
