# -*- coding: utf-8 -*-
"""备份核心：把重要文件备份到 Gitee / GitHub 私有仓库。

流程（agent 参与转换）：
  1. 插件采集并脱敏 → 暂存区（companion 快照 json + 工作区重要文件）
  2. Agent 调 backup_core_get_draft 取草稿，转换成可读格式（markdown 等）
  3. Agent 调 backup_core_submit_markdown 提交转换结果
  4. backup_core_push 将暂存区 commit + push 到所有已配置远端（git over SSH）

安全边界：
  - 密钥永不进备份：organizer api_key、care token、credentials、ssh 私钥一律剔除
  - 只推送到设置里显式配置的远端；分支可配（默认 main）
  - 暂存与缓存都在插件数据目录内；单次备份有大小/文件数上限
"""
import json
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

PLUGIN_VERSION = "0.1.0"
MAX_FILES = 400
MAX_BYTES = 24 * 1024 * 1024
SECRET_PATTERNS = re.compile(
    r"(api[_-]?key|token|secret|password|passwd|authorization|bearer|private[_-]?key)",
    re.IGNORECASE)

import importlib.util as _ilu
from pathlib import Path as _P


def _load_sibling(name):
    spec = _ilu.spec_from_file_location(name, _P(__file__).parent / f"{name}.py")
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


router = APIRouter()

_DATA_DIR = Path(os.environ.get(
    "QWENPAW_BACKUP_DATA",
    Path.home() / ".qwenpaw" / "data" / "backup-core"))
_lock = threading.RLock()


class Target(BaseModel):
    name: str = Field(min_length=1, max_length=40, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    kind: str = Field(default="remote", pattern=r"^(local|remote)$")
    url: str = Field(default="", max_length=300)
    path: str = Field(default="", max_length=300)
    branch: str = Field(default="main", max_length=60)


class Settings(BaseModel):
    targets: list[Target] = Field(default_factory=list)
    agent_id: str = Field(default="", max_length=100)
    companion: bool = True
    workspace_globs: list[str] = Field(default_factory=lambda: [
        "MEMORY.md", "PROFILE.md", "AGENTS.md", "SOUL.md",
        "jobs.json", "skills/companion_core/SKILL.md"])
    workspace_root: str = ""
    git_name: str = "番星"
    git_email: str = "3509412293@qq.com"
    key_path: str = ""
    repo_subdir: str = Field(default="backups", max_length=60)
    auto: bool = False
    time: str = "03:30"
    vault_path: str = Field(default="", max_length=300)
    accumulate_interval_minutes: int = Field(default=120, ge=10, le=1440)
    distill_time: str = Field(default="23:00", max_length=5)
    push_time: str = Field(default="23:30", max_length=5)
    keep_days: int = Field(default=14, ge=1, le=365)
    enabled: bool = True
    base_url: str = Field(default="", max_length=2000)
    model: str = Field(default="", max_length=200)
    api_key: str = Field(default="", max_length=4000, repr=False)


def _read_json(path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8")) or default
    except (OSError, ValueError):
        return default


def _write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _settings_path():
    return _DATA_DIR / "backup-settings.json"


def _read_settings():
    data = _read_json(_settings_path(), {})
    base = Settings().model_dump()
    if isinstance(data, dict):
        for key, value in data.items():
            if key in base:
                base[key] = value
    return Settings.model_validate(base).model_dump()


def _companion_data_dir():
    return Path(os.environ.get(
        "QWENPAW_COMPANION_DATA",
        Path.home() / ".qwenpaw" / "data" / "companion-core"))


def _sanitize_obj(obj):
    """Deep-strip secret-looking values from dicts/lists."""
    if isinstance(obj, dict):
        out = {}
        for key, value in obj.items():
            if SECRET_PATTERNS.search(str(key)):
                out[key] = "***"
            else:
                out[key] = _sanitize_obj(value)
        return out
    if isinstance(obj, list):
        return [_sanitize_obj(x) for x in obj]
    return obj


def _stage_dir():
    return _DATA_DIR / "stage"


def _collect(cfg):
    stage = _stage_dir()
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True, exist_ok=True)
    manifest = {"created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "version": PLUGIN_VERSION, "files": []}

    def add(src: Path, rel: str):
        if not src.is_file() or src.is_symlink():
            return
        if src.stat().st_size > 2 * 1024 * 1024:
            return
        dest = stage / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        manifest["files"].append({"path": rel, "bytes": src.stat().st_size})

    if cfg["companion"]:
        cdir = _companion_data_dir()
        for name in ("state.yaml", "schedule.yaml", "routine.yaml", "relationship.yaml",
                     "important_dates.yaml", "notes.json", "interactions.json",
                     "usage.json", "profiles.json", "care-state.json",
                     "organizer-settings.json", "diary-settings.json"):
            src = cdir / name
            if not src.is_file():
                continue
            if name == "organizer-settings.json":
                data = _sanitize_obj(_read_json(src, {}))
                dest = stage / "companion" / name
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                manifest["files"].append({"path": f"companion/{name}",
                                          "bytes": dest.stat().st_size, "sanitized": True})
            elif name == "care-state.json":
                data = _read_json(src, {})
                for agent_cfg in (data.get("settings") or {}).values():
                    if isinstance(agent_cfg, dict):
                        agent_cfg.pop("token", None)
                        agent_cfg.pop("model_signature", None)
                dest = stage / "companion" / name
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                manifest["files"].append({"path": f"companion/{name}",
                                          "bytes": dest.stat().st_size, "sanitized": True})
            else:
                add(src, f"companion/{name}")
        for folder, pattern in (("diary", "*.md"), ("weekly", "*.md"), ("dreams", "*.md"),
                                ("history", "*.yaml")):
            src_dir = cdir / folder
            if src_dir.is_dir() and not src_dir.is_symlink():
                for item in sorted(src_dir.glob(pattern))[:300]:
                    add(item, f"companion/{folder}/{item.name}")

    root = Path(cfg["workspace_root"]) if cfg["workspace_root"] else None
    if root is None and cfg.get("agent_id"):
        # 绑定智能体：只备份该 agent 的工作区，避免备错
        base = Path(os.environ.get("QWENPAW_WORKING_DIR", "") or (Path.home() / ".qwenpaw"))
        cand = base / "workspaces" / cfg["agent_id"]
        root = cand if cand.is_dir() else None
    if root is None:
        candidate = Path(os.environ.get("QWENPAW_WORKING_DIR", ""))
        root = candidate if candidate.is_dir() else Path.cwd()
    for pattern in cfg["workspace_globs"]:
        for hit in sorted(root.glob(pattern))[:50]:
            rel = hit.relative_to(root).as_posix()
            if any(part.startswith(".") for part in Path(rel).parts):
                continue
            add(hit, f"workspace/{rel}")

    total = sum(f["bytes"] for f in manifest["files"])
    if len(manifest["files"]) > MAX_FILES or total > MAX_BYTES:
        raise HTTPException(400, f"备份超过上限（{len(manifest['files'])} 文件 / {total} 字节）")
    _write_json(stage / "manifest.json", manifest)
    return manifest


def _render_readable(stage: Path):
    """Deterministic human-readable render (agent markdown may replace/extend)."""
    lines = ["# 陪伴备份可读版", "", f"生成时间：{time.strftime('%Y-%m-%d %H:%M %Z')}"]
    state_file = stage / "companion" / "state.yaml"
    if state_file.is_file():
        try:
            import yaml
            state = yaml.safe_load(state_file.read_text(encoding="utf-8")) or {}
            lines += ["", "## 当前状态",
                      f"- 心情：{state.get('mood') or '未记录'}",
                      f"- 精力：{state.get('energy') or '未记录'}",
                      f"- 睡眠：{state.get('sleep') or '未记录'}"]
        except Exception:  # noqa: BLE001
            lines += ["", "## 当前状态", "（解析失败，见 companion/state.yaml）"]
    notes_file = stage / "companion" / "notes.json"
    if notes_file.is_file():
        notes = _read_json(notes_file, [])
        lines += ["", f"## 便签（{len(notes)}）"]
        lines += [f"- {n.get('content', '')}" for n in notes[:30] if isinstance(n, dict)]
    (stage / "READABLE.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _git_env(cfg):
    env = dict(os.environ)
    key = cfg.get("key_path") or str(Path.home() / ".ssh" / "id_ed25519")
    if Path(key).is_file():
        env["GIT_SSH_COMMAND"] = f"ssh -i {key} -o BatchMode=yes -o StrictHostKeyChecking=accept-new"
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def _run_git(args, cwd, cfg):
    proc = subprocess.run(["git", *args], cwd=str(cwd), env=_git_env(cfg),
                          capture_output=True, text=True, timeout=300)
    if proc.returncode != 0:
        raise HTTPException(502, f"git {' '.join(args)} 失败：{proc.stderr.strip()[:300]}")
    return proc.stdout.strip()


def _resolve_repo_path(target, cfg):
    repo = Path(target.get("path") or "")
    if not repo.is_absolute():
        base = Path(os.environ.get("QWENPAW_WORKING_DIR", "") or (Path.home() / ".qwenpaw"))
        repo = base / repo
    return repo


def _apply_stage_to_repo(repo: Path, sub: Path):
    """stage -> 仓库布局（latest + history/日期，保留 30 天）。"""
    stage = _stage_dir()
    if not str(sub.resolve()).startswith(str(repo.resolve())):
        raise HTTPException(400, "repo_subdir 越界")
    if sub.resolve() == repo.resolve():
        for item in repo.iterdir():
            if item.name == ".git":
                continue
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()
    else:
        if sub.exists():
            shutil.rmtree(sub)
        sub.mkdir(parents=True, exist_ok=True)

    def copy_stage(dest_root: Path):
        dest_root.mkdir(parents=True, exist_ok=True)
        for item in stage.iterdir():
            dest = dest_root / item.name
            if item.is_dir():
                shutil.copytree(item, dest)
            else:
                shutil.copy2(item, dest)

    copy_stage(sub / "latest")
    day = time.strftime("%Y-%m-%d")
    history_root = sub / "history"
    hist = history_root / day
    if hist.exists():
        shutil.rmtree(hist)
    copy_stage(hist)
    if history_root.is_dir():
        for old_dir in sorted(history_root.iterdir(), key=lambda x: x.name, reverse=True)[30:]:
            if old_dir.is_dir():
                shutil.rmtree(old_dir)


def _commit_local(cfg, message):
    """第一级备份：stage -> 本地仓库 commit（不 push，高频）。"""
    stage = _stage_dir()
    if not stage.is_dir():
        raise HTTPException(400, "没有暂存备份，请先采集")
    locals_ = [t for t in cfg["targets"] if t.get("kind") == "local"]
    if not locals_:
        raise HTTPException(400, "尚未配置本地备份仓库（kind=local 的 target）")
    results = []
    for target in locals_:
        repo = _resolve_repo_path(target, cfg)
        try:
            repo.mkdir(parents=True, exist_ok=True)
            if not (repo / ".git").is_dir():
                _run_git(["init", "-b", target.get("branch") or "main"], repo, cfg)
            sub = repo / (cfg.get("repo_subdir") or "")
            _apply_stage_to_repo(repo, sub)
            _run_git(["add", "-A"], repo, cfg)
            if not _run_git(["status", "--porcelain"], repo, cfg):
                results.append({"target": target["name"], "state": "no-change"})
                continue
            _run_git(["-c", f"user.name={cfg['git_name']}", "-c", f"user.email={cfg['git_email']}",
                      "commit", "-m", message], repo, cfg)
            results.append({"target": target["name"], "state": "committed",
                            "commit": _run_git(["rev-parse", "--short", "HEAD"], repo, cfg).strip()})
        except HTTPException as exc:
            results.append({"target": target["name"], "state": "error", "error": exc.detail})
        except (OSError, subprocess.SubprocessError) as exc:
            results.append({"target": target["name"], "state": "error", "error": str(exc)[:300]})
    return results


def _push_remote(cfg, message):
    """第二级备份：本地仓库 -> 所有 remote targets 镜像推送（低频）。"""
    locals_ = [t for t in cfg["targets"] if t.get("kind") == "local"]
    if not locals_:
        raise HTTPException(400, "缺少本地仓库（kind=local），无法推送远端")
    repo = _resolve_repo_path(locals_[0], cfg)
    if not (repo / ".git").is_dir():
        raise HTTPException(400, "本地备份仓库不存在，请先执行本地提交")
    branch = locals_[0].get("branch") or "main"
    results = []
    for target in cfg["targets"]:
        if target.get("kind") != "remote":
            continue
        try:
            names = _run_git(["remote"], repo, cfg).split()
            if target["name"] in names:
                _run_git(["remote", "set-url", target["name"], target["url"]], repo, cfg)
            else:
                _run_git(["remote", "add", target["name"], target["url"]], repo, cfg)
            remote_branch = target.get("branch") or branch
            refspec = f"{branch}:{remote_branch}"
            try:
                _run_git(["push", target["name"], refspec], repo, cfg)
            except HTTPException:
                _run_git(["push", "-u", target["name"], refspec], repo, cfg)
            results.append({"target": target["name"], "state": "pushed"})
        except HTTPException as exc:
            results.append({"target": target["name"], "state": "error", "error": exc.detail})
        except (OSError, subprocess.SubprocessError) as exc:
            results.append({"target": target["name"], "state": "error", "error": str(exc)[:300]})
    if not results:
        raise HTTPException(400, "尚未配置远端（kind=remote 的 target）")
    return results


def _record_status(info):
    history = _read_json(_DATA_DIR / "backup-status.json", [])
    if not isinstance(history, list):
        history = []
    history.append(info)
    _write_json(_DATA_DIR / "backup-status.json", history[-30:])


@router.get("/settings")
def get_settings():
    return _read_settings()


def _save_settings(data: dict):
    with _lock:
        _write_json(_settings_path(), Settings.model_validate(data).model_dump())
    return _read_settings()


@router.put("/settings")
def put_settings(payload: Settings):
    return _save_settings(payload.model_dump())


@router.post("/collect")
def collect():
    with _lock:
        cfg = _read_settings()
        manifest = _collect(cfg)
        _render_readable(_stage_dir())
    return {"files": len(manifest["files"]),
            "bytes": sum(f["bytes"] for f in manifest["files"]),
            "paths": [f["path"] for f in manifest["files"]]}


@router.get("/draft")
def draft():
    """脱敏草稿：供 Agent 读取并转换为合适格式。"""
    stage = _stage_dir()
    if not stage.is_dir():
        raise HTTPException(400, "请先 POST /collect")
    out = {}
    for item in sorted(stage.rglob("*")):
        if item.is_file() and item.suffix in {".json", ".md"} and item.name != "manifest.json":
            text = item.read_text(encoding="utf-8", errors="replace")
            out[item.relative_to(stage).as_posix()] = text[:20000]
    return out


@router.post("/markdown")
def submit_markdown(payload: dict):
    text = payload.get("text") if isinstance(payload, dict) else None
    if not isinstance(text, str) or not text.strip() or len(text) > 200000:
        raise HTTPException(400, "markdown 内容无效")
    if SECRET_PATTERNS.search(text) and "===KEY===" not in text:
        raise HTTPException(400, "转换结果疑似包含密钥，已拒绝；请脱敏后重交")
    with _lock:
        (_stage_dir() / "AGENT-NOTES.md").write_text(text, encoding="utf-8")
    return {"saved": "AGENT-NOTES.md", "bytes": len(text.encode("utf-8"))}


@router.post("/commit-local")
def commit_local(payload: dict | None = None):
    """第一级备份：采集 + 脱敏 + 本地仓库提交（高频，无网络依赖）。"""
    message = (payload or {}).get("message") or f"local backup: {time.strftime('%Y-%m-%d %H:%M')}"
    with _lock:
        cfg = _read_settings()
        manifest = _collect(cfg)
        _render_readable(_stage_dir())
        results = _commit_local(cfg, message)
    _record_status({"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    "message": message, "results": results})
    return {"files": len(manifest["files"]),
            "bytes": sum(f["bytes"] for f in manifest["files"]),
            "results": results}


@router.post("/push")
def push(payload: dict | None = None):
    message = (payload or {}).get("message") or f"backup: {time.strftime('%Y-%m-%d %H:%M')}"
    with _lock:
        cfg = _read_settings()
        if not cfg["targets"]:
            raise HTTPException(400, "尚未配置备份目标")
        results = _push_remote(cfg, message)
    _record_status({"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    "message": message, "results": results})
    return {"results": results}


@router.get("/status")
def status():
    return _read_json(_DATA_DIR / "backup-status.json", [])



# ── v0.3 上下文：积累/转化/调度 ────────────────────────────
_memoryflow = _load_sibling("memoryflow")
_distillmod = _load_sibling("distill")
_schedulermod = _load_sibling("scheduler")

_CORE = {"state": {}, "fail_streak": 0}


def _vault_path(cfg):
    p = cfg.get("vault_path") or ""
    if p:
        return Path(p)
    base = Path(os.environ.get("QWENPAW_WORKING_DIR", "") or (Path.home() / ".qwenpaw"))
    agent = cfg.get("agent_id") or ""
    return base / "workspaces" / agent / "backups" / "vault" if agent else base / "backups" / "vault"


def _flow():
    if "flow" not in _CORE:
        _CORE["flow"] = _memoryflow.MemoryFlow(_CORE_PROXY)
    return _CORE["flow"]


def _distiller():
    if "distill" not in _CORE:
        _CORE["distill"] = _distillmod.Distiller(_CORE_PROXY)
    return _CORE["distill"]


_CORE_PROXY = {"_DATA_DIR": _DATA_DIR}


async def _poll_accumulate(cfg, vault):
    """兜底轮询：遍历绑定 agent 近期会话积累记忆流。"""
    from qwenpaw.plugins.registry import PluginRegistry
    mgr = PluginRegistry().get_workspace_manager()
    if mgr is None:
        return {"accumulated": 0, "reason": "no-workspace-manager"}
    ws = mgr.agents.get(cfg.get("agent_id") or "")
    if ws is None:
        return {"accumulated": 0, "reason": "agent-workspace-not-found"}
    chats = await ws.chat_manager.list_chats()
    chats = sorted(chats, key=lambda c: c.updated_at, reverse=True)[:3]
    done = 0
    details = []
    for chat in chats:
        sid = getattr(chat, "session_id", None) or chat.id
        r = await _flow().accumulate(ws, sid, getattr(chat, "user_id", "") or "",
                                     getattr(chat, "channel", "") or "", vault)
        details.append({"chat": chat.id[:8], **r})
        if r.get("added"):
            done += 1
    return {"accumulated": done, "sessions": len(chats), "details": details}


_CORE.update({"settings": _read_settings, "vault_path": _vault_path,
              "poll_accumulate": _poll_accumulate,
              "push_remote": lambda cfg, msg: _push_remote(cfg, msg)})
_CORE_PROXY.update(_CORE)

_SCHED = _schedulermod.Scheduler(_CORE_PROXY)


@router.post("/accumulate")
async def accumulate_now():
    cfg = _read_settings()
    vault = _vault_path(cfg)
    return await _poll_accumulate(cfg, vault)


@router.post("/distill")
def distill_now(payload: dict | None = None):
    cfg = _read_settings()
    if not cfg.get("base_url") or not cfg.get("model"):
        raise HTTPException(400, "尚未配置转化模型（base_url/model）")
    vault = _vault_path(cfg)
    day = (payload or {}).get("day")
    return _distiller().run(cfg, vault, day)


@router.get("/summary-draft")
def get_summary_draft(day: str | None = None):
    cfg = _read_settings()
    vault = _vault_path(cfg)
    return _distiller().prepare_draft(cfg, vault, day)


@router.post("/submit-summary")
def submit_summary(payload: dict):
    cfg = _read_settings()
    vault = _vault_path(cfg)
    return _distiller().submit_summary(cfg, vault, payload)


@router.get("/overview")
def overview():
    cfg = _read_settings()
    vault = _vault_path(cfg)
    raw_root = vault / "raw"
    snaps = sorted(raw_root.iterdir()) if raw_root.is_dir() else []
    repo = vault / "repo"
    commits = 0
    if (repo / ".git").is_dir():
        out = subprocess.run(["git", "rev-list", "--count", "HEAD"], cwd=repo,
                             capture_output=True, text=True).stdout.strip()
        commits = int(out) if out.isdigit() else 0
    return {"vault": str(vault), "agent_id": cfg.get("agent_id"),
            "raw_snapshots": len(snaps),
            "raw_latest": snaps[-1].name if snaps else None,
            "repo_commits": commits,
            "scheduler_running": _SCHED.running,
            "last": {k: _CORE["state"].get(k) for k in ("last", "last_distill", "last_push")},
            "fail_streak": _CORE.get("fail_streak", 0)}


@router.get("/gate-log")
def gate_log():
    return _flow().gate_log()



def asyncio_run(coro):
    import asyncio as _a
    try:
        loop = _a.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is not None and loop.is_running():
        import concurrent.futures as _cf
        with _cf.ThreadPoolExecutor(1) as ex:
            return ex.submit(lambda: _a.run(coro)).result()
    return _a.run(coro)



SUMMARY_PROMPT = (
    "现在是备份核心的【晚间总结】时刻（后台例行任务，不要给用户发消息）。请依次："
    "1) 调用 backup_core_get_draft 读取当日备份草稿；"
    "2) 严格按草稿中 format_spec 的格式与质量红线，把对话记忆总结为 "
    "persona / memory_lite / daily 三部分（事实、日期、用户引语逐字保留，不编造）；"
    "3) 调用 backup_core_submit_summary 提交总结；"
    "4) 调用 backup_core_push 推送精炼仓库到远端。"
    "完成后用一句话记录结果即可，不要输出总结全文。")


def _ensure_summary_job():
    """幂等创建每晚总结 cron 任务（主模型三段式）。"""
    from qwenpaw.plugins.registry import PluginRegistry
    from qwenpaw.app.crons.models import CronJobSpec
    cfg = _read_settings()
    agent = cfg.get("agent_id") or ""
    if not agent:
        return
    mgr = PluginRegistry().get_workspace_manager()
    if mgr is None:
        return
    ws = mgr.agents.get(agent)
    cm = getattr(ws, "cron_manager", None)
    if cm is None or not callable(getattr(cm, "create_or_replace_job", None)):
        return
    spec = CronJobSpec.model_validate({
        "id": "backup-core-nightly-summary",
        "name": "备份核心：晚间总结（主模型三段式）",
        "task_type": "agent",
        "enabled": True,
        "schedule": {"type": "cron", "cron": "0 23 * * *", "timezone": "Asia/Shanghai"},
        "dispatch": {"type": "channel", "channel": "console", "mode": "final",
                     "silent": True,
                     "target": {"session_id": f"backup-summary-{agent}", "user_id": agent}},
        "request": {"input": [{"content": [{"text": SUMMARY_PROMPT, "type": "text"}]}],
                    "session_id": f"backup-summary-{agent}", "user_id": agent},
        "runtime": {"max_concurrency": 1, "timeout_seconds": 900,
                    "misfire_grace_seconds": 1800, "share_session": False},
        "save_result_to_inbox": False,
    })
    import asyncio as _a
    try:
        _a.get_running_loop()
        _a.create_task(cm.create_or_replace_job(spec))
    except RuntimeError:
        _a.run(cm.create_or_replace_job(spec))


def install(app=None):
    return router


# ── Agent 工具（默认禁用，需在智能体工具开关中启用）───────────────────

def tool_get_draft() -> str:
    """读取脱敏后的备份草稿，供转换为可读格式。"""
    stage = _stage_dir()
    if not stage.is_dir():
        return "还没有暂存备份：请先调用备份采集（POST /api/backup-core/collect）。"
    return json.dumps(draft(), ensure_ascii=False)[:60000]


def tool_submit_markdown(text: str) -> str:
    """提交 Agent 转换后的可读备份文档（AGENT-NOTES.md）。"""
    try:
        result = submit_markdown({"text": text})
    except HTTPException as exc:
        return f"提交失败：{exc.detail}"
    return f"已保存 {result['saved']}（{result['bytes']} 字节），下一步调用 backup_core_push。"


def tool_submit_summary(persona: str = "", memory_lite: list = None, daily: str = "", day: str = "") -> str:
    """提交主模型总结到精炼仓库。"""
    try:
        r = submit_summary({"persona": persona, "memory_lite": memory_lite or [],
                            "daily": daily, "day": day})
    except HTTPException as exc:
        return f"提交失败：{exc.detail}"
    except ValueError as exc:
        return f"提交被拒：{exc}"
    return json.dumps(r, ensure_ascii=False)


def tool_commit_local(message: str = "") -> str:
    """第一级备份：采集+脱敏后提交到本地仓库（高频，无网络）。"""
    try:
        result = commit_local({"message": message} if message else None)
    except HTTPException as exc:
        return f"本地提交失败：{exc.detail}"
    return json.dumps(result, ensure_ascii=False)


def tool_push(message: str = "") -> str:
    """把本地备份仓库推送到已配置的 GitHub/Gitee 远端（低频，每晚）。"""
    try:
        result = push({"message": message} if message else None)
    except HTTPException as exc:
        return f"推送失败：{exc.detail}"
    return json.dumps(result, ensure_ascii=False)


def tool_status() -> str:
    """最近备份运行状态。"""
    return json.dumps(status(), ensure_ascii=False)[:20000]


class BackupCorePlugin:
    """Entry point for the QwenPaw plugin loader."""

    def register(self, api):
        api.register_http_router(router, prefix="/backup-core", tags=["backup-core"])

        def _startup():
            _CORE["api"] = api
            _SCHED.start()
            try:
                _ensure_summary_job()
            except Exception:
                pass

        async def _shutdown():
            await _SCHED.stop()

        api.register_startup_hook("backup_core_scheduler", _startup)
        api.register_shutdown_hook("backup_core_scheduler", _shutdown)

        # v0.4.1：不再注册 runtime hook（热装重复注册会触发宿主 hook 拓扑排序
        # cycle 报错）。积累改由 scheduler 轮询 + backup_core_accumulate 工具驱动。
        api.register_tool("backup_core_get_draft", tool_get_draft,
                          description="读取备份核心脱敏草稿（companion 快照与工作区重要文件）",
                          icon="📦", tool_type="internal")
        api.register_tool("backup_core_submit_markdown", tool_submit_markdown,
                          description="提交转换后的可读备份文档（markdown）",
                          icon="✍️", tool_type="internal")
        api.register_tool("backup_core_accumulate", lambda: json.dumps(asyncio_run(_poll_accumulate(_read_settings(), _vault_path(_read_settings()))), ensure_ascii=False),
                          description="立即积累绑定智能体的对话记忆流到本地 raw",
                          icon="🧠", tool_type="internal")
        api.register_tool("backup_core_distill", lambda: json.dumps(distill_now(), ensure_ascii=False),
                          description="立即执行每日转化：raw 积累总结为 xiaoxin-memory 式精炼仓库",
                          icon="⚗️", tool_type="internal")
        api.register_tool("backup_core_overview", lambda: json.dumps(overview(), ensure_ascii=False),
                          description="备份系统总览：raw 快照数/精炼 commit 数/调度状态/最近运行",
                          icon="📊", tool_type="internal")
        api.register_tool("backup_core_get_draft", lambda: json.dumps(_distiller().prepare_draft(_read_settings(), _vault_path(_read_settings())), ensure_ascii=False)[:60000],
                          description="读取当日备份草稿（对话流+日记），供主模型总结",
                          icon="📄", tool_type="internal")
        api.register_tool("backup_core_submit_summary", tool_submit_summary,
                          description="提交主模型总结（persona/memory_lite/daily），写入精炼仓库并 commit",
                          icon="✍️", tool_type="internal")
        api.register_tool("backup_core_commit_local", tool_commit_local,
                          description="第一级备份：采集+脱敏后提交到本地仓库（高频，无网络）",
                          icon="💾", tool_type="internal")
        api.register_tool("backup_core_push", tool_push,
                          description="第二级备份：把本地备份仓库推送到 GitHub/Gitee 远端（低频）",
                          icon="☁️", tool_type="network")
        api.register_tool("backup_core_status", tool_status,
                          description="查看最近备份运行状态",
                          icon="🩺", tool_type="internal")


plugin = BackupCorePlugin()
