#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""照片内容指纹。"""

from __future__ import annotations

import hashlib
from pathlib import Path


def content_fingerprint(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
