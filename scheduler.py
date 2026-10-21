# -*- coding: utf-8 -*-
"""内置定时器：积累兜底轮询 + 每晚转化 + 每晚推送 + raw 修剪。

积累主路径是 runtime hook（对话后即时）；本调度器做兜底轮询与夜间流水线。
"""
import asyncio
import time


def _hhmm_passed(now_struct, hhmm):
    try:
        h, m = (int(x) for x in str(hhmm).split(":"))
    except Exception:
        return False
    return (now_struct.tm_hour, now_struct.tm_min) >= (h, m)


class Scheduler:
    def __init__(self, core):
        self.c = core
        self.tasks = set()
        self.running = False
        self._last_distill_day = ""
        self._last_push_day = ""

    async def _loop(self):
        c = self.c
        while self.running:
            await asyncio.sleep(60)
            cfg = c["settings"]()
            if not cfg.get("enabled", True):
                continue
            now = time.localtime()
            vault = c["vault_path"](cfg)
            # 兜底积累轮询
            interval = cfg.get("accumulate_interval_minutes", 120)
            last = c["state"].get("last_poll", 0)
            if time.time() - last >= interval * 60:
                c["state"]["last_poll"] = time.time()
                try:
                    await c["poll_accumulate"](cfg, vault)
                except Exception:
                    pass
            # 每晚转化
            if _hhmm_passed(now, cfg.get("distill_time", "23:00")):
                day = time.strftime("%Y-%m-%d")
                if self._last_distill_day != day:
                    self._last_distill_day = day
                    try:
                        c["state"]["last_distill"] = c["distill"].run(cfg, vault)
                    except Exception as exc:
                        c["state"]["last_distill"] = {"state": "error", "error": str(exc)[:200]}
            # 每晚推送（转化之后）
            if _hhmm_passed(now, cfg.get("push_time", "23:30")):
                day = time.strftime("%Y-%m-%d")
                if self._last_push_day != day:
                    self._last_push_day = day
                    try:
                        c["state"]["last_push"] = {"results": c["push_remote"](cfg, f"nightly: {day}")}
                        c["fail_streak"] = 0
                    except Exception as exc:
                        c["fail_streak"] = c.get("fail_streak", 0) + 1
                        c["state"]["last_push"] = {"state": "error", "error": str(exc)[:200],
                                                   "fail_streak": c["fail_streak"]}
            # raw 修剪（每天一次，跟 push 同日）
            if self._last_push_day == time.strftime("%Y-%m-%d"):
                try:
                    c["flow"].prune_raw(vault, cfg.get("keep_days", 14))
                except Exception:
                    pass

    def start(self):
        if self.running:
            return
        self.running = True
        task = asyncio.create_task(self._loop())
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def stop(self):
        self.running = False
        for t in list(self.tasks):
            t.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)
