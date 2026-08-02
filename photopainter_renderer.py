#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""PhotoPainter Spectra 6 六色图像渲染工具。

核心转换逻辑按 Toon-nooT/PhotoPainter-E-Ink-Spectra-6-image-converter
整理为可导入函数：六色调色板、RGB+亮度距离、Atkinson 抖动、
Floyd-Steinberg palette 布局、scale/cut 尺寸处理语义均保持一致。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps
import pillow_heif


pillow_heif.register_heif_opener()

DITHER_NONE = "none"
DITHER_ATKINSON = "atkinson"
DITHER_ATKINSON_STANDARD = "atkinson-standard"
DITHER_FLOYD_STEINBERG = "floyd-steinberg"
DITHER_STUCKI = "stucki"
DITHER_JARVIS = "jarvis-judice-ninke"

SIX_COLOR_PALETTE: tuple[tuple[int, int, int], ...] = (
    (0, 0, 0),
    (255, 255, 255),
    (255, 255, 0),
    (255, 0, 0),
    (0, 0, 255),
    (0, 255, 0),
)

PALETTE_ARRAY = np.array(SIX_COLOR_PALETTE, dtype=np.float32)
PALETTE_LUMA_ARRAY = np.array(
    [r * 250 + g * 350 + b * 400 for (r, g, b) in SIX_COLOR_PALETTE],
    dtype=np.float32,
) / (255.0 * 1000)
GOLDEN_POSITIONS = (0.382, 0.618)
GOLDEN_FACE_Y = 0.382
INFO_BAR_HEIGHT_RATIO = 0.155
INFO_BAR_MIN_HEIGHT = 58
INFO_BAR_MAX_HEIGHT = 82
INFO_BAR_MARGIN_RATIO = 0.035


def closest_palette_index(rgb: Iterable[int | float]) -> int:
    """按 PhotoPainter 的 RGB+亮度距离选择最接近的六色索引。"""
    r1, g1, b1 = rgb
    luma1 = (r1 * 250 + g1 * 350 + b1 * 400) / (255.0 * 1000)

    diff_r = r1 - PALETTE_ARRAY[:, 0]
    diff_g = g1 - PALETTE_ARRAY[:, 1]
    diff_b = b1 - PALETTE_ARRAY[:, 2]

    rgb_dist = (
        diff_r * diff_r * 0.250
        + diff_g * diff_g * 0.350
        + diff_b * diff_b * 0.400
    ) * 0.75 / (255.0 * 255.0)
    luma_diff = luma1 - PALETTE_LUMA_ARRAY
    luma_dist = luma_diff * luma_diff
    total_dist = 1.5 * rgb_dist + 0.60 * luma_dist
    return int(np.argmin(total_dist))


ERROR_DIFFUSION_KERNELS: dict[str, tuple[float, tuple[tuple[int, int, float], ...]]] = {
    DITHER_FLOYD_STEINBERG: (
        16.0,
        ((1, 0, 7), (-1, 1, 3), (0, 1, 5), (1, 1, 1)),
    ),
    DITHER_ATKINSON: (
        8.0,
        ((1, 0, 1), (-1, 1, 1), (0, 1, 2), (1, 1, 1)),
    ),
    DITHER_ATKINSON_STANDARD: (
        8.0,
        ((1, 0, 1), (2, 0, 1), (-1, 1, 1), (0, 1, 1), (1, 1, 1), (0, 2, 1)),
    ),
    DITHER_STUCKI: (
        42.0,
        (
            (1, 0, 8), (2, 0, 4),
            (-2, 1, 2), (-1, 1, 4), (0, 1, 8), (1, 1, 4), (2, 1, 2),
            (-2, 2, 1), (-1, 2, 2), (0, 2, 4), (1, 2, 2), (2, 2, 1),
        ),
    ),
    DITHER_JARVIS: (
        48.0,
        (
            (1, 0, 7), (2, 0, 5),
            (-2, 1, 3), (-1, 1, 5), (0, 1, 7), (1, 1, 5), (2, 1, 3),
            (-2, 2, 1), (-1, 2, 3), (0, 2, 5), (1, 2, 3), (2, 2, 1),
        ),
    ),
}


def quantize_error_diffusion(
    image: Image.Image,
    *,
    algorithm: str,
    strength: float = 1.0,
) -> Image.Image:
    """使用 FrameFilm 同组误差扩散矩阵转换为 PhotoPainter 六色图。"""
    if algorithm not in ERROR_DIFFUSION_KERNELS:
        raise ValueError(f"不支持的误差扩散算法: {algorithm}")
    divisor, kernel = ERROR_DIFFUSION_KERNELS[algorithm]
    working = np.array(image.convert("RGB"), dtype=np.float32)
    height, width, _ = working.shape
    strength = _clamp(float(strength), 0.0, 5.0)

    for y in range(height):
        for x in range(width):
            old_pixel = np.clip(working[y, x], 0, 255)
            index = closest_palette_index(old_pixel)
            new_pixel = PALETTE_ARRAY[index]
            working[y, x] = new_pixel
            error = (old_pixel - new_pixel) * strength
            for offset_x, offset_y, weight in kernel:
                target_x = x + offset_x
                target_y = y + offset_y
                if 0 <= target_x < width and target_y < height:
                    working[target_y, target_x] += error * (weight / divisor)

    return Image.fromarray(np.clip(working, 0, 255).astype(np.uint8), mode="RGB")


def quantize_atkinson(image: Image.Image, strength: float = 1.0) -> Image.Image:
    """PhotoPainter 原版的前向四邻点 Atkinson 误差扩散。"""
    return quantize_error_diffusion(
        image,
        algorithm=DITHER_ATKINSON,
        strength=strength,
    )


def _make_photopainter_palette() -> Image.Image:
    """按参考脚本构造 palette：索引 4 留黑色，蓝/绿位于 5/6。"""
    pal_image = Image.new("P", (1, 1))
    pal_image.putpalette(
        (
            0,
            0,
            0,
            255,
            255,
            255,
            255,
            255,
            0,
            255,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            255,
            0,
            255,
            0,
        )
        + (0, 0, 0) * 249
    )
    return pal_image


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _detect_face_boxes(image: Image.Image) -> list[tuple[int, int, int, int]]:
    """用 OpenCV Haar cascade 检测人脸；未安装 OpenCV 时静默退回居中裁切。"""
    try:
        import cv2  # type: ignore
    except Exception:
        return []

    try:
        rgb = np.array(image.convert("RGB"))
        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        cascade_path = str(Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml")
        detector = cv2.CascadeClassifier(cascade_path)
        if detector.empty():
            return []
        min_side = max(24, min(image.size) // 24)
        faces = detector.detectMultiScale(
            gray,
            scaleFactor=1.08,
            minNeighbors=4,
            minSize=(min_side, min_side),
        )
        return [(int(x), int(y), int(w), int(h)) for (x, y, w, h) in faces]
    except Exception:
        return []


def _union_focus_boxes(
    boxes: list[tuple[int, int, int, int]],
    image_size: tuple[int, int],
    margin_ratio: float = 0.08,
) -> tuple[float, float, float, float] | None:
    if not boxes:
        return None
    width, height = image_size
    x1 = min(x for x, _, _, _ in boxes)
    y1 = min(y for _, y, _, _ in boxes)
    x2 = max(x + w for x, _, w, _ in boxes)
    y2 = max(y + h for _, y, _, h in boxes)
    margin = max(width, height) * margin_ratio
    return (
        _clamp(x1 - margin, 0, width),
        _clamp(y1 - margin, 0, height),
        _clamp(x2 + margin, 0, width),
        _clamp(y2 + margin, 0, height),
    )


def _axis_crop_start(
    *,
    axis_length: int,
    crop_length: int,
    focus_min: float,
    focus_max: float,
    focus_center: float,
    golden_positions: tuple[float, ...],
) -> int:
    max_start = axis_length - crop_length
    if max_start <= 0:
        return 0

    contain_low = _clamp(focus_max - crop_length, 0, max_start)
    contain_high = _clamp(focus_min, 0, max_start)
    if contain_low <= contain_high:
        candidates: list[tuple[float, float]] = []
        for golden in golden_positions:
            desired = focus_center - crop_length * golden
            start = _clamp(desired, contain_low, contain_high)
            relative = (focus_center - start) / crop_length
            score = abs(relative - golden)
            candidates.append((score, start))
        return int(round(min(candidates, key=lambda item: item[0])[1]))

    # 极端情况下人脸区域比裁切窗口还大，只能尽量居中保住主体。
    return int(round(_clamp(focus_center - crop_length * 0.5, 0, max_start)))


def _compute_cover_crop_box(
    image_size: tuple[int, int],
    *,
    width: int,
    height: int,
    focus_boxes: list[tuple[int, int, int, int]] | None = None,
) -> tuple[int, int, int, int]:
    """计算 scale 模式裁切框，优先保住人脸并靠近黄金分割位置。"""
    original_width, original_height = image_size
    target_aspect = width / height
    source_aspect = original_width / original_height

    if source_aspect > target_aspect:
        crop_height = original_height
        crop_width = int(round(original_height * target_aspect))
    else:
        crop_width = original_width
        crop_height = int(round(original_width / target_aspect))

    crop_width = min(original_width, max(1, crop_width))
    crop_height = min(original_height, max(1, crop_height))
    focus = _union_focus_boxes(focus_boxes or [], image_size)

    if focus is None:
        left = (original_width - crop_width) // 2
        top = (original_height - crop_height) // 2
    else:
        focus_x1, focus_y1, focus_x2, focus_y2 = focus
        focus_cx = (focus_x1 + focus_x2) / 2
        focus_cy = (focus_y1 + focus_y2) / 2
        left = _axis_crop_start(
            axis_length=original_width,
            crop_length=crop_width,
            focus_min=focus_x1,
            focus_max=focus_x2,
            focus_center=focus_cx,
            golden_positions=GOLDEN_POSITIONS,
        )
        top = _axis_crop_start(
            axis_length=original_height,
            crop_length=crop_height,
            focus_min=focus_y1,
            focus_max=focus_y2,
            focus_center=focus_cy,
            golden_positions=(GOLDEN_FACE_Y,),
        )

    left = int(round(_clamp(left, 0, original_width - crop_width)))
    top = int(round(_clamp(top, 0, original_height - crop_height)))
    return (left, top, left + crop_width, top + crop_height)


def _focus_box_from_crop_focus(
    crop_focus: dict[str, Any] | None,
    image_size: tuple[int, int],
) -> tuple[int, int, int, int] | None:
    """把 VLM 输出的 0~1 相对坐标裁切关注区转换成像素 box。"""
    if not isinstance(crop_focus, dict):
        return None
    try:
        x = float(crop_focus["x"])
        y = float(crop_focus["y"])
        w = float(crop_focus["w"])
        h = float(crop_focus["h"])
    except (KeyError, TypeError, ValueError):
        return None

    if w <= 0 or h <= 0:
        return None
    x = _clamp(x, 0.0, 1.0)
    y = _clamp(y, 0.0, 1.0)
    w = _clamp(w, 0.01, 1.0 - x)
    h = _clamp(h, 0.01, 1.0 - y)
    image_width, image_height = image_size
    left = int(round(x * image_width))
    top = int(round(y * image_height))
    right = int(round((x + w) * image_width))
    bottom = int(round((y + h) * image_height))
    if right <= left or bottom <= top:
        return None
    return (left, top, right - left, bottom - top)


def fit_to_photopainter_canvas(
    image: Image.Image,
    *,
    width: int,
    height: int,
    mode: str,
    focus_boxes: list[tuple[int, int, int, int]] | None = None,
    crop_focus: dict[str, Any] | None = None,
) -> Image.Image:
    """按参考项目的 scale/cut 语义适配画布。

    注意：参考项目中 `scale` 使用 max 比例并居中粘贴，超出部分会被裁掉；
    `cut` 使用 ImageOps.pad，保留完整画面并用白色补边。
    """
    input_image = ImageOps.exif_transpose(image).convert("RGB")
    original_width, original_height = input_image.size
    mode = (mode or "scale").lower()

    if mode == "scale":
        vlm_focus = _focus_box_from_crop_focus(crop_focus, input_image.size)
        if focus_boxes is not None:
            boxes = focus_boxes
        elif vlm_focus is not None:
            boxes = [vlm_focus]
        else:
            boxes = _detect_face_boxes(input_image)
        crop_box = _compute_cover_crop_box(
            (original_width, original_height),
            width=width,
            height=height,
            focus_boxes=boxes,
        )
        return input_image.crop(crop_box).resize((width, height), Image.Resampling.LANCZOS)

    if mode == "cut":
        if original_width / original_height >= width / height:
            box = (0, 0, original_width, original_height)
        else:
            box = (0, 0, original_width, original_height)
        return ImageOps.pad(
            input_image.crop(box),
            size=(width, height),
            color=(255, 255, 255),
            centering=(0.5, 0.5),
        )

    raise ValueError("mode 必须是 'scale' 或 'cut'")


def fit_manual_transform_to_canvas(
    image: Image.Image,
    *,
    width: int,
    height: int,
    scale: float = 1.0,
    offset_x: float = 0.0,
    offset_y: float = 0.0,
    rotation: int = 0,
    fit_mode: str = "fill",
) -> Image.Image:
    """把完整原图绘制进固定画布，再由画布边界完成裁切。

    `scale=1` 表示自动填充或完整适应后的基准大小；偏移量使用最终画布像素，
    因而 WebUI 的 800x432 坐标可以直接复用于最终推送渲染。
    """
    source = ImageOps.exif_transpose(image).convert("RGB")
    normalized_rotation = int(rotation or 0) % 360
    if normalized_rotation not in {0, 90, 180, 270}:
        raise ValueError("rotation 必须是 0、90、180 或 270")
    if normalized_rotation:
        source = source.rotate(-normalized_rotation, expand=True, resample=Image.Resampling.BICUBIC)

    source_width, source_height = source.size
    fit_mode = str(fit_mode or "fill").lower()
    if fit_mode == "fill":
        base_scale = max(width / source_width, height / source_height)
    elif fit_mode == "contain":
        base_scale = min(width / source_width, height / source_height)
    else:
        raise ValueError("fit_mode 必须是 'fill' 或 'contain'")

    total_scale = base_scale * _clamp(float(scale), 0.1, 5.0)
    resized_width = max(1, int(round(source_width * total_scale)))
    resized_height = max(1, int(round(source_height * total_scale)))
    resized = source.resize((resized_width, resized_height), Image.Resampling.LANCZOS)
    left = int(round((width - resized_width) / 2 + float(offset_x)))
    top = int(round((height - resized_height) / 2 + float(offset_y)))
    canvas = Image.new("RGB", (width, height), (255, 255, 255))
    canvas.paste(resized, (left, top))
    return canvas


def enhance_for_eink(
    image: Image.Image,
    *,
    brightness: float,
    contrast: float,
    saturation: float,
) -> Image.Image:
    """按参考项目顺序做亮度、对比度、饱和度和细节增强。"""
    enhanced_image = ImageEnhance.Brightness(image).enhance(brightness)
    enhanced_image = ImageEnhance.Contrast(enhanced_image).enhance(contrast)
    enhanced_image = ImageEnhance.Color(enhanced_image).enhance(saturation)
    enhanced_image = enhanced_image.filter(ImageFilter.EDGE_ENHANCE)
    enhanced_image = enhanced_image.filter(ImageFilter.SMOOTH)
    return enhanced_image.filter(ImageFilter.SHARPEN)


def quantize_six_color(
    image: Image.Image,
    dither: str,
    *,
    strength: float = 1.0,
) -> Image.Image:
    dither = (dither or DITHER_ATKINSON).lower()
    if dither in ERROR_DIFFUSION_KERNELS:
        return quantize_error_diffusion(
            image,
            algorithm=dither,
            strength=strength,
        )
    if dither == DITHER_NONE:
        return image.quantize(
            dither=Image.Dither.NONE,
            palette=_make_photopainter_palette(),
        ).convert("RGB")
    raise ValueError(
        "dither 必须是 'atkinson'、'atkinson-standard'、'floyd-steinberg'、"
        "'stucki'、'jarvis-judice-ninke' 或 'none'"
    )


def _load_text_font(font_path: str | Path | None, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates: list[Path] = []
    if font_path:
        candidates.append(Path(font_path).expanduser())
    candidates.extend(
        [
            Path("C:/Windows/Fonts/msyh.ttc"),
            Path("C:/Windows/Fonts/simkai.ttf"),
            Path("C:/Windows/Fonts/simsun.ttc"),
            Path("/System/Library/Fonts/PingFang.ttc"),
            Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"),
        ]
    )
    for candidate in candidates:
        if candidate.exists():
            try:
                return ImageFont.truetype(str(candidate), size=size)
            except Exception:
                continue
    return ImageFont.load_default()


def _text_size(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> tuple[int, int]:
    box = draw.textbbox((0, 0), text, font=font)
    return int(box[2] - box[0]), int(box[3] - box[1])


def _draw_location_pin(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    *,
    size: int = 16,
    fill: tuple[int, int, int] = (0, 0, 0),
) -> None:
    center_x = x + size // 2
    circle_top = y + 1
    circle_bottom = y + size - 6
    circle_left = x + 3
    circle_right = x + size - 3
    point_y = y + size - 1

    draw.ellipse((circle_left, circle_top, circle_right, circle_bottom), outline=fill, width=1)
    draw.ellipse((center_x - 2, circle_top + 4, center_x + 2, circle_top + 8), outline=fill, width=1)
    draw.line((circle_left + 1, circle_bottom - 2, center_x, point_y), fill=fill, width=1)
    draw.line((circle_right - 1, circle_bottom - 2, center_x, point_y), fill=fill, width=1)


def _fit_single_line(
    draw: ImageDraw.ImageDraw,
    text: str,
    *,
    font_path: str | Path | None,
    preferred_size: int,
    min_size: int,
    max_width: int,
) -> tuple[str, ImageFont.ImageFont]:
    clean = str(text or "").strip()
    for size in range(preferred_size, min_size - 1, -1):
        font = _load_text_font(font_path, size)
        if _text_size(draw, clean, font)[0] <= max_width:
            return clean, font

    font = _load_text_font(font_path, min_size)
    ellipsis = "…"
    while clean and _text_size(draw, clean + ellipsis, font)[0] > max_width:
        clean = clean[:-1]
    return (clean + ellipsis if clean else ellipsis), font


def _draw_info_bar(
    image: Image.Image,
    *,
    side_caption: str,
    location: str = "",
    font_path: str | Path | None = None,
) -> Image.Image:
    if not side_caption and not location:
        return image

    width, height = image.size
    bar_height = int(round(height * INFO_BAR_HEIGHT_RATIO))
    bar_height = max(INFO_BAR_MIN_HEIGHT, min(INFO_BAR_MAX_HEIGHT, bar_height))
    margin = max(22, int(round(width * INFO_BAR_MARGIN_RATIO)))
    bar_top = height - bar_height

    output = image.copy()
    draw = ImageDraw.Draw(output)
    draw.rectangle((0, bar_top, width, height), fill=(248, 245, 237))

    location_text = str(location or "").strip()
    caption_text = str(side_caption or "").strip()
    location_font = _load_text_font(font_path, max(18, int(bar_height * 0.34)))
    location_width = 0
    if location_text:
        location_width = 16 + 6 + _text_size(draw, location_text, location_font)[0]

    gap = 20 if location_text else 0
    caption_max_width = max(80, width - margin * 2 - location_width - gap)
    caption, caption_font = _fit_single_line(
        draw,
        caption_text,
        font_path=font_path,
        preferred_size=max(22, int(bar_height * 0.42)),
        min_size=16,
        max_width=caption_max_width,
    )

    caption_height = _text_size(draw, caption, caption_font)[1]
    caption_y = bar_top + (bar_height - caption_height) // 2 - 2
    draw.text((margin, caption_y), caption, fill=(0, 0, 0), font=caption_font)

    if location_text:
        loc_width, loc_height = _text_size(draw, location_text, location_font)
        total_width = 16 + 6 + loc_width
        icon_x = width - margin - total_width
        loc_y = bar_top + (bar_height - loc_height) // 2 - 2
        icon_y = bar_top + (bar_height - 16) // 2 - 1
        _draw_location_pin(draw, icon_x, icon_y, size=16, fill=(120, 120, 120))
        draw.text((icon_x + 22, loc_y), location_text, fill=(120, 120, 120), font=location_font)

    return output


def render_photopainter_image(
    source_path: str | Path,
    output_path: str | Path,
    *,
    width: int = 800,
    height: int = 432,
    mode: str = "scale",
    dither: str = DITHER_ATKINSON,
    dither_strength: float = 1.0,
    brightness: float = 1.1,
    contrast: float = 1.2,
    saturation: float = 1.2,
    save_bmp: bool = True,
    crop_focus: dict[str, Any] | None = None,
    side_caption: str = "",
    location: str = "",
    font_path: str | Path | None = None,
) -> Image.Image:
    source = Path(source_path).expanduser()
    output = Path(output_path).expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)

    bar_height = 0
    if side_caption or location:
        bar_height = int(round(height * INFO_BAR_HEIGHT_RATIO))
        bar_height = max(INFO_BAR_MIN_HEIGHT, min(INFO_BAR_MAX_HEIGHT, bar_height))
    image_height = max(1, height - bar_height)

    with Image.open(source) as img:
        fitted = fit_to_photopainter_canvas(
            img,
            width=width,
            height=image_height,
            mode=mode,
            crop_focus=crop_focus,
        )
    if bar_height:
        canvas = Image.new("RGB", (width, height), (248, 245, 237))
        canvas.paste(fitted, (0, 0))
        fitted = _draw_info_bar(
            canvas,
            side_caption=side_caption,
            location=location,
            font_path=font_path,
        )
    enhanced = enhance_for_eink(
        fitted,
        brightness=brightness,
        contrast=contrast,
        saturation=saturation,
    )
    rendered = quantize_six_color(enhanced, dither, strength=dither_strength)
    rendered.save(output)

    if save_bmp:
        rendered.save(output.with_suffix(".bmp"))

    return rendered
