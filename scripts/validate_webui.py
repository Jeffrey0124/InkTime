#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""验证本地或 NAS WebUI 路由与设备推送产物。"""

from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from PIL import Image


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from photopainter_renderer import SIX_COLOR_PALETTE  # noqa: E402


ROUTES = (
    "/healthz",
    "/",
    "/gallery",
    "/api/photos?limit=1",
    "/push/latest.png",
    "/push/latest.bmp",
    "/push/manifest.json",
)


def _fetch(base_url: str, path: str, timeout: float) -> tuple[bytes, str]:
    url = urljoin(f"{base_url.rstrip('/')}/", path.lstrip("/"))
    request = Request(url, headers={"User-Agent": "InkTime-WebUI-Validator/1.0"})
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.read(), str(response.headers.get_content_type())
    except (HTTPError, URLError, TimeoutError) as exc:
        raise RuntimeError(f"GET {path} failed: {exc}") from exc


def validate(base_url: str, *, width: int, height: int, timeout: float) -> dict[str, object]:
    responses = {path: _fetch(base_url, path, timeout) for path in ROUTES}

    health = json.loads(responses["/healthz"][0].decode("utf-8"))
    if health.get("ok") is not True:
        raise ValueError("/healthz did not return {\"ok\": true}")

    photo_index = json.loads(responses["/api/photos?limit=1"][0].decode("utf-8"))
    photos = photo_index.get("photos")
    if photo_index.get("ok") is not True or not isinstance(photos, list) or not photos:
        raise ValueError("/api/photos?limit=1 did not return an available photo")
    photo_id = photos[0].get("photo_id")
    if not isinstance(photo_id, int):
        raise ValueError("photo index did not return an integer photo_id")

    concrete_routes = (f"/photos/{photo_id}", f"/push-studio/{photo_id}")
    responses.update(
        {path: _fetch(base_url, path, timeout) for path in concrete_routes}
    )

    manifest = json.loads(responses["/push/manifest.json"][0].decode("utf-8"))
    if manifest.get("image_url") != "/push/latest.bmp":
        raise ValueError("push manifest image_url must be /push/latest.bmp")

    with Image.open(io.BytesIO(responses["/push/latest.bmp"][0])) as image:
        image.load()
        if image.size != (width, height):
            raise ValueError(f"latest.bmp size is {image.size}, expected {(width, height)}")
        if image.mode != "RGB":
            raise ValueError(f"latest.bmp mode is {image.mode}, expected RGB")
        colors = set(image.getdata())

    unexpected = colors - set(SIX_COLOR_PALETTE)
    if unexpected:
        sample = sorted(unexpected)[:6]
        raise ValueError(f"latest.bmp contains non-PhotoPainter colors: {sample}")

    return {
        "base_url": base_url.rstrip("/"),
        "routes": len(responses),
        "photo_id": photo_id,
        "bmp_size": [width, height],
        "bmp_mode": "RGB",
        "bmp_colors": len(colors),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8766")
    parser.add_argument("--width", type=int, default=800)
    parser.add_argument("--height", type=int, default=480)
    parser.add_argument("--timeout", type=float, default=10.0)
    args = parser.parse_args()

    try:
        result = validate(
            args.base_url,
            width=args.width,
            height=args.height,
            timeout=args.timeout,
        )
    except (RuntimeError, ValueError, json.JSONDecodeError, OSError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1

    print("PASS: WebUI routes and push artifact")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
