# -*- coding: utf-8 -*-
"""积累层：从绑定智能体的近期对话中提取记忆流，脱敏后 append 落盘。

设计（DESIGN.md v0.3）：
- 每 2h 轮询 + 每次对话结束（runtime hook）触发，防上下文压缩/重置丢失珍贵记忆
- 只保留 user/assistant 文本（不含工具/系统提示/图片）
- 脱敏 + 敏感闸门扫描；命中密钥特征的内容整块丢弃并记入 gate-log
- 以 fingerprint 去重：同一段对话不重复积累
- 落盘为 raw/YYYY-MM-DD/conversations.md 的 append 块（带时间戳）
"""
import hashlib
import json
import re
import time
from datetime import datetime
from pathlib import Path

SECRET_PATTERNS = re.compile(
    r"(api[_-]?key|token|secret|password|passwd|authorization|bearer|private[_-]?key"
    r"|sk-[A-Za-z0-9]{8,}|ms-[0-9a-f]{8}-|ENC:[A-Za-z0-9+/=]{8,})",
    re.IGNORECASE)
# 叙事型敏感值（中文口令、IP:端口、root@ 主机等）
NARRATIVE_SECRET = re.compile(
    r"(密码|口令|验证码)\s*[:：是]\s*\S{4,}"
    r"|\b\d{1,3}(?:\.\d{1,3}){3}:\d{2,5}\b"
    r"|root@[a-z0-9.-]+", re.IGNORECASE)


def fingerprint(texts) -> str:
    blob = json.dumps(texts, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def text_messages(state, limit, char_limit):
    """Keep user/assistant text only; never ship tools, system prompts or images."""
    agent = state.get("agent") or {}
    raw = (agent.get("state") or {}).get("context")
    if raw is None:
        raw = (agent.get("memory") or {}).get("content", [])
    flat = []
    for item in raw or []:
        flat.extend(item if isinstance(item, list) else [item])
    result, remaining = [], char_limit
    for msg in reversed(flat):
        if not isinstance(msg, dict) or msg.get("role") not in {"user", "assistant"}:
            continue
        content = msg.get("content", "")
        if isinstance(content, list):
            content = "\n".join(block.get("text", "") for block in content
                                if isinstance(block, dict) and block.get("type") == "text")
        if not isinstance(content, str) or not content.strip():
            continue
        content = content[-remaining:]
        result.append({"role": msg["role"], "content": content})
        remaining -= len(content)
        if len(result) >= limit or remaining <= 0:
            break
    return list(reversed(result))


def scan_block(text):
    """返回 (clean_text, hits)。命中密钥特征的行整行剔除并计数。"""
    hits = []
    out = []
    for line in text.splitlines():
        if SECRET_PATTERNS.search(line) or NARRATIVE_SECRET.search(line):
            hits.append(line[:80])
            continue
        out.append(line)
    return "\n".join(out), hits


class MemoryFlow:
    def __init__(self, core):
        self.c = core
        self.state_path = core["_DATA_DIR"] / "memoryflow-state.json"

    # ── state ───────────────────────────────────────────
    def _load(self):
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception:
            return {"seen": {}, "gate_log": []}

    def _save(self, data):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.state_path)

    # ── raw paths ────────────────────────────────────────
    def raw_dir(self, vault: Path, day=None):
        day = day or time.strftime("%Y-%m-%d")
        d = vault / "raw" / day
        d.mkdir(parents=True, exist_ok=True)
        return d

    def prune_raw(self, vault: Path, keep_days: int):
        root = vault / "raw"
        if not root.is_dir():
            return 0
        removed = 0
        for d in sorted(root.iterdir()):
            if d.is_dir() and d.name < time.strftime("%Y-%m-%d", time.localtime(time.time() - keep_days * 86400)):
                import shutil
                shutil.rmtree(d, ignore_errors=True)
                removed += 1
        return removed

    # ── accumulate ───────────────────────────────────────
    async def accumulate(self, workspace, session_id, user_id, channel,
                         vault: Path, limit=40, chars=24000):
        """读近期对话 → 脱敏 → 去重 → append 到 raw/当日/conversations.md。"""
        state = await workspace.session.get_session_state_dict(session_id, user_id, channel)
        texts = text_messages(state, limit, chars)
        if not texts:
            return {"added": 0, "reason": "no-text"}
        fp = fingerprint(texts)
        st = self._load()
        key = f"{session_id}"
        if st["seen"].get(key) == fp:
            return {"added": 0, "reason": "no-change"}
        body = "\n".join(f"[{m['role']}] {m['content']}" for m in texts)
        clean, hits = scan_block(body)
        if hits:
            st.setdefault("gate_log", []).append(
                {"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "session": session_id,
                 "dropped_lines": len(hits), "samples": hits[:3]})
            st["gate_log"] = st["gate_log"][-200:]
        if not clean.strip():
            st["seen"][key] = fp
            self._save(st)
            return {"added": 0, "reason": "all-filtered"}
        day_dir = self.raw_dir(vault)
        target = day_dir / "conversations.md"
        stamp = datetime.now().astimezone().isoformat(timespec="seconds")
        block = f"\n\n<!-- accumulated {stamp} fp={fp} session={session_id[:8]} -->\n{clean}\n"
        with open(target, "a", encoding="utf-8") as f:
            f.write(block)
        st["seen"][key] = fp
        st["last"] = {"at": stamp, "session": session_id, "chars": len(clean)}
        self._save(st)
        return {"added": len(clean), "fp": fp, "dropped_lines": len(hits)}

    def gate_log(self):
        return self._load().get("gate_log", [])
