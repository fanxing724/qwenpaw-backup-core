# -*- coding: utf-8 -*-
"""转化层：每晚把当天 raw 积累总结提炼为 xiaoxin-memory 式精炼仓库。

产物布局（= xiaoxin-memory 结构）：
  vault/repo/
  ├── AGENTS.md               写作规范
  ├── config/persona.txt      人设概览
  ├── config/diary-template.md
  ├── memory/memory-lite.md / .json   编号共同记忆
  └── memory/memory-daily/YYYY-MM/YYYY-MM-DD.md  按日叙事日记

质量红线（取自 xiaoxin-memory 治理实践）：
- 事实/日期/引语逐字保留，改写只动叙事不动事实
- 治理类条目不被改写；编号连续；缺失不猜
- 产物过密钥扫描闸门后才可入仓
"""
import json
import re
import subprocess
import time
from pathlib import Path
from urllib.request import Request as URLRequest, build_opener, ProxyHandler

import importlib.util as _ilu
_spec = _ilu.spec_from_file_location("bc_memoryflow", Path(__file__).parent / "memoryflow.py")
_mf = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_mf)
SECRET_PATTERNS = _mf.SECRET_PATTERNS
NARRATIVE_SECRET = _mf.NARRATIVE_SECRET

EXTRACT_PROMPT = """你是记忆提取器。输入是一段对话记忆流片段。
提取其中值得长期记住的事实/经历/用户原话，输出 JSON 数组（字符串列表）。
规则：一条一事；用户引语逐字保留（用引号）；不编造；无值得记的内容输出 []。
仅输出 JSON 数组，不要其他文字。"""

NARRATE_PROMPT = """你是记忆整理器。输入是某 AI 智能体当日提炼出的记忆条目列表。
据此输出 JSON 对象：{"persona": "一段话人设概览", "daily": "当日第一人称叙事日记"}。
规则：事实与引语逐字保留不改写；不编造条目之外的经历；日记温和不卖惨。
仅输出 JSON 对象。"""


def _parse_loose(text):
    """容错 JSON 解析：直接解析失败时截取最外层括号重试。"""
    text = (text or "").strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    for opener, closer in (("{", "}"), ("[", "]")):
        i, j = text.find(opener), text.rfind(closer)
        if i != -1 and j > i:
            try:
                return json.loads(text[i:j + 1])
            except Exception:
                continue
    # 截断容错：残缺数组/对象截取到最后一个完整元素补全
    if text.startswith("["):
        cut = text.rfind('",')
        if cut > 0:
            try:
                return json.loads(text[:cut + 1] + "]")
            except Exception:
                pass
        cut2 = text.rfind('"')
        if cut2 > 0:
            try:
                return json.loads(text[:cut2 + 1] + "]")
            except Exception:
                pass
    raise ValueError(f"模型输出无法解析为 JSON：{text[:120]!r}")


FORMAT_SPEC = """提交格式（JSON 对象）：
{"persona": "一段话人设概览", "memory_lite": ["条目1", "条目2"], "daily": "当日第一人称叙事日记"}
红线：事实/日期/引语逐字保留；不编造；memory_lite 一条一事。"""

DIARY_PROMPT = """你是日记记录器。输入是某 AI 智能体近 2 小时积累对话的增量片段。
写一段当日日记增量（第一人称、温和、不卖惨），只记片段里真实发生/提及的事，
用户引语逐字保留。无值得记的内容则输出空字符串。
仅输出日记正文，不要 JSON、不要标题。"""

def _scan(text):
    hits = []
    for line in (text or "").splitlines():
        if SECRET_PATTERNS.search(line) or NARRATIVE_SECRET.search(line):
            hits.append(line[:80])
    return hits


class Distiller:
    def __init__(self, core):
        self.c = core

    def _complete(self, cfg, messages, tokens=4096):
        url = cfg["base_url"].rstrip("/")
        endpoint = url if url.endswith("/chat/completions") else url + "/chat/completions"
        headers = {"Content-Type": "application/json"}
        if cfg.get("api_key"):
            headers["Authorization"] = "Bearer " + cfg["api_key"]
        payload = {"model": cfg["model"], "messages": messages,
                   "max_tokens": tokens, "stream": False}
        req = URLRequest(endpoint, data=json.dumps(payload).encode(),
                         headers=headers, method="POST")
        # 直连：透明代理环境下 urllib 默认走代理会被网关拦截
        last = None
        for _attempt in range(2):
            try:
                with build_opener(ProxyHandler({})).open(req, timeout=300) as resp:
                    result = json.loads(resp.read())
                break
            except TimeoutError as exc:
                last = exc
        else:
            raise last
        ch = result["choices"][0]
        content = ch["message"].get("content") or ""
        if content.startswith("```"):
            content = "\n".join(content.splitlines()[1:-1])
        return _parse_loose(content), result.get("usage", {})

    def run(self, cfg, vault: Path, day=None):
        """读当天 raw → 模型提炼 → 写精炼仓库文件 → git commit。返回摘要。"""
        day = day or time.strftime("%Y-%m-%d")
        raw_file = vault / "raw" / day / "conversations.md"
        if not raw_file.is_file():
            # fallback：取最近一个有 raw 的日子（跨天场景）
            root = vault / "raw"
            if root.is_dir():
                cands = sorted((d for d in root.iterdir()
                                if d.is_dir() and (d / "conversations.md").is_file()),
                               key=lambda x: x.name, reverse=True)
                if cands:
                    day = cands[0].name
                    raw_file = cands[0] / "conversations.md"
        if not raw_file.is_file() or not raw_file.read_text(encoding="utf-8").strip():
            return {"state": "skipped", "reason": "no-raw"}
        repo = vault / "repo"
        repo.mkdir(parents=True, exist_ok=True)
        if not (repo / ".git").is_dir():
            subprocess.run(["git", "init", "-b", "main", str(repo)],
                           check=True, capture_output=True)
        # 起始编号：读现有 memory-lite
        lite_path = repo / "memory" / "memory-lite.md"
        start_no = 1
        existing = []
        if lite_path.is_file():
            for line in lite_path.read_text(encoding="utf-8").splitlines():
                m = re.match(r"^(\d+)\.\s", line)
                if m:
                    start_no = max(start_no, int(m.group(1)) + 1)
                    existing.append(line)
        raw_text = raw_file.read_text(encoding="utf-8")
        # 分块提炼：每块 <=6000 字符，逐块提取记忆条目（输入输出小、格式稳）
        blocks, cur = [], ""
        for chunk in re.split(r"(?=<!-- accumulated )", raw_text):
            if not chunk.strip():
                continue
            if len(cur) + len(chunk) > 6000 and cur:
                blocks.append(cur)
                cur = chunk
            else:
                cur += chunk
        if cur.strip():
            blocks.append(cur)
        entries, usage_total = [], {"prompt_tokens": 0, "completion_tokens": 0}
        for blk in blocks:
            out_b, u = self._complete(cfg, [
                {"role": "system", "content": EXTRACT_PROMPT},
                {"role": "user", "content": blk[-6000:]}], tokens=4096)
            if isinstance(out_b, list):
                entries.extend(str(e) for e in out_b)
            elif isinstance(out_b, dict) and isinstance(out_b.get("memory_lite"), list):
                entries.extend(str(e) for e in out_b["memory_lite"])
            for k in usage_total:
                usage_total[k] += (u or {}).get(k, 0)
        # 去重保序
        seen, dedup = set(), []
        for e in entries:
            key = e.strip().casefold()
            if key and key not in seen:
                seen.add(key)
                dedup.append(e)
        entries = dedup
        # persona + daily 基于条目二次生成（输入短）
        out = {"persona": "", "daily": ""}
        if entries:
            out2, u2 = self._complete(cfg, [
                {"role": "system", "content": NARRATE_PROMPT},
                {"role": "user", "content": json.dumps(
                    {"day": day, "entries": entries[:80]}, ensure_ascii=False)}], tokens=2048)
            if isinstance(out2, dict):
                out["persona"] = out2.get("persona") or ""
                out["daily"] = out2.get("daily") or ""
            for k in usage_total:
                usage_total[k] += (u2 or {}).get(k, 0)
        usage = usage_total
        new_entries = [re.sub(r"^\d+\.\s*", "", str(e)).strip() for e in entries]
        new_entries = [e for e in new_entries if e]
        # 闸门：产物含密钥特征则拒写
        for key in ("persona", "daily"):
            hits = _scan(out.get(key) or "")
            if hits:
                raise ValueError(f"产物 {key} 含密钥特征，已拒写：{hits[:2]}")
        for e in new_entries:
            if _scan(e):
                raise ValueError("memory_lite 条目含密钥特征，已拒写")
        # 写 persona
        if out.get("persona"):
            (repo / "config").mkdir(parents=True, exist_ok=True)
            (repo / "config" / "persona.txt").write_text(out["persona"] + "\n", encoding="utf-8")
        # 写 memory-lite（append 新条目）
        if new_entries:
            lite_path.parent.mkdir(parents=True, exist_ok=True)
            lines = list(existing)
            no = start_no
            for e in new_entries:
                lines.append(f"{no}. {e}")
                no += 1
            lite_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            (repo / "memory" / "memory-lite.json").write_text(
                json.dumps([re.sub(r"^\d+\.\s", "", l) for l in lines if re.match(r"^\d+\.\s", l)],
                           ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        # 写 daily
        if out.get("daily"):
            ddir = repo / "memory" / "memory-daily" / day[:7]
            ddir.mkdir(parents=True, exist_ok=True)
            (ddir / f"{day}.md").write_text(out["daily"] + "\n", encoding="utf-8")
        # 写作规范与模板（首次补齐）
        if not (repo / "AGENTS.md").is_file():
            (repo / "AGENTS.md").write_text(
                "# 写作规范\n\n1. 日记以 AI 第一人称写，事实与引语逐字保留，不编造。\n"
                "2. memory-lite 一条一事、编号连续、引语保留原话。\n"
                "3. 治理类条目（不编造/不记成已做成）不被改写。\n", encoding="utf-8")
        # commit
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
        st = subprocess.run(["git", "status", "--porcelain"], cwd=repo,
                            capture_output=True, text=True).stdout
        if not st.strip():
            return {"state": "no-change", "usage": usage}
        subprocess.run(["git", "-c", f"user.name={cfg.get('git_name', '番星')}",
                        "-c", f"user.email={cfg.get('git_email', '3509412293@qq.com')}",
                        "commit", "-m", f"distill: {day}"], cwd=repo,
                       check=True, capture_output=True)
        return {"state": "distilled", "entries": len(new_entries),
                "persona": bool(out.get("persona")), "daily": bool(out.get("daily")),
                "usage": usage}


    def record_diary(self, cfg, vault: Path, day=None):
        """2h 上下文日记：小模型把增量对话写成日记片段，append 到 raw/diary/。"""
        day = day or time.strftime("%Y-%m-%d")
        state_file = vault / "raw" / ".diary-offset.json"
        offsets = {}
        if state_file.is_file():
            try:
                offsets = json.loads(state_file.read_text(encoding="utf-8"))
            except Exception:
                offsets = {}
        conv = vault / "raw" / day / "conversations.md"
        if not conv.is_file():
            return {"state": "skipped", "reason": "no-conversations"}
        text = conv.read_text(encoding="utf-8")
        last = offsets.get(day, 0)
        delta = text[last:]
        if not delta.strip():
            return {"state": "no-change"}
        try:
            diary, _u = self._complete(cfg, [
                {"role": "system", "content": DIARY_PROMPT},
                {"role": "user", "content": delta[-6000:]}], tokens=1024)
        except Exception as exc:
            return {"state": "error", "error": str(exc)[:150]}
        offsets[day] = len(text)
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps(offsets), encoding="utf-8")
        if not isinstance(diary, str) or not diary.strip():
            return {"state": "empty"}
        hits = _scan(diary)
        if hits:
            return {"state": "rejected", "reason": "secret-pattern", "hits": hits[:2]}
        ddir = vault / "raw" / "diary"
        ddir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%H:%M")
        with open(ddir / f"{day}.md", "a", encoding="utf-8") as f:
            f.write(f"\n\n### {stamp}\n{diary.strip()}\n")
        return {"state": "recorded", "chars": len(diary)}

    def prepare_draft(self, cfg, vault: Path, day=None):
        """汇总当日 raw 对话流 + 日记，脱敏后作为草稿供主模型(agent)总结。"""
        day = day or time.strftime("%Y-%m-%d")
        root = vault / "raw"
        if not (root / day / "conversations.md").is_file():
            cands = sorted((d for d in root.iterdir() if d.is_dir() and (d / "conversations.md").is_file()),
                           key=lambda x: x.name, reverse=True) if root.is_dir() else []
            if not cands:
                return {"state": "skipped", "reason": "no-raw"}
            day = cands[0].name
        conv = (root / day / "conversations.md").read_text(encoding="utf-8")[-40000:]
        diary_f = root / "diary" / f"{day}.md"
        diary = diary_f.read_text(encoding="utf-8")[-8000:] if diary_f.is_file() else ""
        clean, hits = _mf.scan_block(conv)
        return {"state": "draft", "day": day, "conversations": clean,
                "diary": diary, "dropped_lines": len(hits),
                "format_spec": FORMAT_SPEC}

    def submit_summary(self, cfg, vault: Path, payload: dict):
        """接收主模型(agent)提交的总结，过闸门后写入精炼仓库并 commit。"""
        day = payload.get("day") or time.strftime("%Y-%m-%d")
        repo = vault / "repo"
        repo.mkdir(parents=True, exist_ok=True)
        if not (repo / ".git").is_dir():
            subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
        lite_path = repo / "memory" / "memory-lite.md"
        start_no, existing = 1, []
        if lite_path.is_file():
            for line in lite_path.read_text(encoding="utf-8").splitlines():
                m = re.match(r"^(\d+)\.\s", line)
                if m:
                    start_no = max(start_no, int(m.group(1)) + 1)
                    existing.append(line)
        entries = [re.sub(r"^\d+\.\s*", "", str(e)).strip()
                   for e in (payload.get("memory_lite") or [])]
        entries = [e for e in entries if e]
        for key in ("persona", "daily"):
            hits = _scan(payload.get(key) or "")
            if hits:
                raise ValueError(f"产物 {key} 含密钥特征，已拒写：{hits[:2]}")
        for e in entries:
            if _scan(e):
                raise ValueError("memory_lite 条目含密钥特征，已拒写")
        if payload.get("persona"):
            (repo / "config").mkdir(parents=True, exist_ok=True)
            (repo / "config" / "persona.txt").write_text(payload["persona"] + "\n", encoding="utf-8")
        if entries:
            lite_path.parent.mkdir(parents=True, exist_ok=True)
            lines, no = list(existing), start_no
            for e in entries:
                lines.append(f"{no}. {e}")
                no += 1
            lite_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            (repo / "memory" / "memory-lite.json").write_text(
                json.dumps([re.sub(r"^\d+\.\s", "", l) for l in lines if re.match(r"^\d+\.\s", l)],
                           ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if payload.get("daily"):
            ddir = repo / "memory" / "memory-daily" / day[:7]
            ddir.mkdir(parents=True, exist_ok=True)
            (ddir / f"{day}.md").write_text(payload["daily"] + "\n", encoding="utf-8")
        if not (repo / "AGENTS.md").is_file():
            (repo / "AGENTS.md").write_text(
                "# 写作规范\n\n1. 日记以 AI 第一人称写，事实与引语逐字保留，不编造。\n"
                "2. memory-lite 一条一事、编号连续、引语保留原话。\n"
                "3. 治理类条目（不编造/不记成已做成）不被改写。\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
        st = subprocess.run(["git", "status", "--porcelain"], cwd=repo,
                            capture_output=True, text=True).stdout
        if not st.strip():
            return {"state": "no-change"}
        subprocess.run(["git", "-c", f"user.name={cfg.get('git_name', '番星')}",
                        "-c", f"user.email={cfg.get('git_email', '3509412293@qq.com')}",
                        "commit", "-m", f"summary: {day}"], cwd=repo, check=True, capture_output=True)
        return {"state": "committed", "entries": len(entries)}
