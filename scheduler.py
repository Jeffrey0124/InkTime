#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""独立运行的 PhotoPainter 自动推送调度器。"""

from __future__ import annotations

import argparse

from push_manager import publish_scheduled, timezone_from_name

try:
    import config as cfg
except ModuleNotFoundError:
    cfg = object()


def _config_value(name: str, default):
    return getattr(cfg, name, default)


def run_scheduled_push(slot: str) -> None:
    manifest = publish_scheduled(slot=slot)
    print(
        f"[OK] 已自动推送 {manifest['image_url']} "
        f"slot={slot} source={manifest.get('source_path', '')}"
    )


def _parse_slot(slot: str) -> tuple[int, int]:
    hour, minute = slot.split(":", 1)
    return int(hour), int(minute)


def main() -> None:
    parser = argparse.ArgumentParser(description="PhotoPainter 自动推送调度器")
    parser.add_argument("--run-once", action="store_true", help="立即执行一次后退出")
    parser.add_argument("--slot", default="", help="本次运行记录的 slot，例如 07:00")
    args = parser.parse_args()

    schedules = list(_config_value("PUSH_SCHEDULES", ["07:00", "12:00", "18:40"]))
    timezone = str(_config_value("PUSH_TIMEZONE", "Asia/Shanghai"))

    if args.run_once:
        run_scheduled_push(args.slot or (schedules[0] if schedules else "manual"))
        return

    from apscheduler.schedulers.blocking import BlockingScheduler

    scheduler = BlockingScheduler(timezone=timezone_from_name(timezone))
    for slot in schedules:
        hour, minute = _parse_slot(slot)
        scheduler.add_job(run_scheduled_push, "cron", hour=hour, minute=minute, args=[slot])
        print(f"[INFO] 已注册自动推送任务：{slot}")

    print("[INFO] PhotoPainter 自动推送调度器已启动")
    scheduler.start()


if __name__ == "__main__":
    main()
