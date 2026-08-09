#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Resolve settings once when a new analysis process starts."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def load_analysis_runtime_settings(
    db_path: str | Path,
    *,
    config_channels: list[dict[str, Any]],
    config_defaults: dict[str, Any],
    master_key: str = "",
) -> dict[str, Any]:
    """Load one immutable settings snapshot for a new process/task.

    Existing processes deliberately keep their current globals. Any database
    error or ineffective fallback chain preserves config.py compatibility.
    """

    channels = [dict(item) for item in config_channels]
    defaults = dict(config_defaults)
    source = "config"
    raw_path = str(db_path)
    if raw_path == ":memory:" or not Path(raw_path).exists():
        return {"channels": channels, "defaults": defaults, "source": source}

    try:
        from settings_store import SettingsStore

        store = SettingsStore(raw_path, master_key=master_key)
        database_defaults = store.get_section("analysis_defaults")
        database_channels = store.runtime_channels()
        if database_channels:
            channels = database_channels
            source = "database"
        if database_defaults["version"] > 0:
            defaults.update(database_defaults["value"])
            source = "database"
    except Exception:
        pass
    return {"channels": channels, "defaults": defaults, "source": source}
