# -*- coding: utf-8 -*-
"""Offline end-to-end tests: file:// bare repos stand in for Gitee/GitHub."""
import importlib.util
import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def backup(tmp_path, monkeypatch):
    monkeypatch.setenv("QWENPAW_BACKUP_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("QWENPAW_COMPANION_DATA", str(tmp_path / "companion"))
    companion = tmp_path / "companion"
    companion.mkdir(parents=True)
    (companion / "state.yaml").write_text("date: '2026-10-19'\nmood: 测试\nenergy: 70\n", encoding="utf-8")
    (companion / "notes.json").write_text(json.dumps([{"id": "n1", "content": "备份测试便签"}]), encoding="utf-8")
    (companion / "organizer-settings.json").write_text(json.dumps(
        {"base_url": "https://x/v1", "model": "m", "api_key": "SUPER-SECRET-KEY", "modules": {}}),
        encoding="utf-8")
    spec = importlib.util.spec_from_file_location("backup_core_under_test", ROOT / "plugin.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, tmp_path


def make_remote(tmp_path, name="remote.git", seed_readme=False):
    remote = tmp_path / name
    subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)],
                   check=True, capture_output=True)
    if seed_readme:
        seed = tmp_path / "seed"
        seed.mkdir()
        (seed / "README.md").write_text("# project code\n", encoding="utf-8")
        for cmd in (["git", "init", "-b", "main"], ["git", "add", "-A"],
                    ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "init"],
                    ["git", "push", str(remote), "main"]):
            subprocess.run(cmd, cwd=str(seed), check=True, capture_output=True)
    return remote


def tree(remote):
    return subprocess.run(["git", "--git-dir", str(remote), "ls-tree", "-r", "--name-only", "main"],
                          capture_output=True, text=True).stdout.split()


def configure(module, remote, **extra):
    module._save_settings({"targets": [{"name": "local", "url": f"file://{remote}", "branch": "main"}],
                           "companion": True, "workspace_globs": [],
                           "workspace_root": str(remote.parent), **extra})


def test_collect_sanitizes_and_push_creates_branch(backup):
    module, tmp_path = backup
    remote = make_remote(tmp_path)
    configure(module, remote, repo_subdir="")
    collected = module.collect()
    assert collected["files"] >= 3
    organizer = json.loads((module._stage_dir() / "companion" / "organizer-settings.json").read_text())
    assert organizer["api_key"] == "***"
    with pytest.raises(Exception):
        module.submit_markdown({"text": "leak api_key=abc123"})
    module.submit_markdown({"text": "# Agent 转换版\n\n心情测试，精力 70。"})
    result = module.push({"message": "first"})
    assert result["results"][0]["state"] == "pushed"
    files = tree(remote)
    for expected in ("latest/READABLE.md", "latest/AGENT-NOTES.md", "latest/manifest.json",
                     "latest/companion/state.yaml"):
        assert expected in files, files
    assert any(f.startswith("history/") and f.endswith("manifest.json") for f in files), files
    blob = subprocess.run(["git", "--git-dir", str(remote), "show",
                           "main:latest/companion/organizer-settings.json"],
                          capture_output=True, text=True).stdout
    assert "SUPER-SECRET" not in blob
    second = module.push({"message": "second"})
    assert second["results"][0]["state"] == "no-change"


def test_repo_subdir_keeps_unrelated_files(backup):
    module, tmp_path = backup
    remote = make_remote(tmp_path, seed_readme=True)
    configure(module, remote, repo_subdir="backups")
    module.collect()
    assert module.push({"message": "t"})["results"][0]["state"] == "pushed"
    files = tree(remote)
    assert "README.md" in files
    assert any(f.startswith("backups/latest/") for f in files)
    assert not any(f.startswith("latest/") for f in files)


def test_status_records_runs(backup):
    module, tmp_path = backup
    remote = make_remote(tmp_path)
    configure(module, remote, repo_subdir="")
    module.collect()
    module.push({"message": "t"})
    history = module.status()
    assert history and history[-1]["results"][0]["target"] == "local"
