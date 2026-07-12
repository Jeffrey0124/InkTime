#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""PhotoPainter Spectra 6 六色图像渲染工具。

核心转换逻辑按 Toon-nooT/PhotoPainter-E-Ink-Spectra-6-image-converter
整理为可导入函数：六色调色板、RGB+亮度距离、Atkinson 抖动、
Floyd-Steinberg palette 布局、scale/cut 尺寸处理语义均保持一致。
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

import numpy as np
from PIL import Image, ImageEnhance, ImageFilter, ImageOps
import pillow_heif


pillow_heif.register_heif_opener()

DITHER_NONE = "none"
DITHER_ATKINSON = "atkinson"
DITHER_FLOYD_STEINBERG = "floyd-steinberg"

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


def quantize_atkinson(image: Image.Image) -> Image.Image:
    """PhotoPainter 参考实现的 Atkinson 浮点误差扩散。"""
    img_array = np.array(image.convert("RGB"))
    height, width, _ = img_array.shape
    working_img = img_array.astype(np.float32)

    for y in range(height):
        for x in range(width):
            old_pixel = working_img[y, x].copy()
            idx = closest_palette_index(tuple(np.clip(old_pixel, 0, 255).astype(int)))
            new_pixel = np.array(SIX_COLOR_PALETTE[idx], dtype=np.float32)
            working_img[y, x] = new_pixel

            error = old_pixel - new_pixel

            if x + 1 < width:
                working_img[y, x + 1] += error * (1 / 8)
            if y + 1 < height:
                if x - 1 >= 0:
                    working_img[y + 1, x - 1] += error * (1 / 8)
                working_img[y + 1, x] += error * (1 / 4)
                if x + 1 < width:
                    working_img[y + 1, x + 1] += error * (1 / 8)

    quantized_array = np.clip(working_img, 0, 255).astype(np.uint8)
    return Image.fromarray(quantized_array)


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


def fit_to_photopainter_canvas(
    image: Image.Image,
    *,
    width: int,
    height: int,
    mode: str,
) -> Image.Image:
    """按参考项目的 scale/cut 语义适配画布。

    注意：参考项目中 `scale` 使用 max 比例并居中粘贴，超出部分会被裁掉；
    `cut` 使用 ImageOps.pad，保留完整画面并用白色补边。
    """
    input_image = ImageOps.exif_transpose(image).convert("RGB")
    original_width, original_height = input_image.size
    mode = (mode or "scale").lower()

    if mode == "scale":
        scale_ratio = max(width / original_width, height / original_height)
        resized_width = int(original_width * scale_ratio)
        resized_height = int(original_height * scale_ratio)
        output_image = input_image.resize((resized_width, resized_height))

        resized_image = Image.new("RGB", (width, height), (255, 255, 255))
        left = (width - resized_width) // 2
        top = (height - resized_height) // 2
        resized_image.paste(output_image, (left, top))
        return resized_image

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


def quantize_six_color(image: Image.Image, dither: str) -> Image.Image:
    dither = (dither or DITHER_ATKINSON).lower()
    if dither == DITHER_ATKINSON:
        return quantize_atkinson(image).convert("RGB")
    if dither == DITHER_FLOYD_STEINBERG:
        return image.quantize(
            dither=Image.Dither.FLOYDSTEINBERG,
            palette=_make_photopainter_palette(),
        ).convert("RGB")
    if dither == DITHER_NONE:
        return image.quantize(
            dither=Image.Dither.NONE,
            palette=_make_photopainter_palette(),
        ).convert("RGB")
    raise ValueError("dither 必须是 'atkinson'、'floyd-steinberg' 或 'none'")


def render_photopainter_image(
    source_path: str | Path,
    output_path: str | Path,
    *,
    width: int = 800,
    height: int = 480,
    mode: str = "scale",
    dither: str = DITHER_ATKINSON,
    brightness: float = 1.1,
    contrast: float = 1.2,
    saturation: float = 1.2,
    save_bmp: bool = True,
) -> Image.Image:
    source = Path(source_path).expanduser()
    output = Path(output_path).expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)

    with Image.open(source) as img:
        fitted = fit_to_photopainter_canvas(img, width=width, height=height, mode=mode)
    enhanced = enhance_for_eink(
        fitted,
        brightness=brightness,
        contrast=contrast,
        saturation=saturation,
    )
    rendered = quantize_six_color(enhanced, dither)
    rendered.save(output)

    if save_bmp:
        rendered.save(output.with_suffix(".bmp"))

    return rendered
