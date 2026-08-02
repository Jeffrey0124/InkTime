(() => {
  const clamp = (value, min, max) => Math.min(max, Math.max(min, value));
  const palette = [
    [0, 0, 0],
    [255, 255, 255],
    [255, 255, 0],
    [255, 0, 0],
    [0, 0, 255],
    [0, 255, 0],
  ];
  const kernels = {
    "floyd-steinberg": [16, [[1, 0, 7], [-1, 1, 3], [0, 1, 5], [1, 1, 1]]],
    atkinson: [8, [[1, 0, 1], [-1, 1, 1], [0, 1, 2], [1, 1, 1]]],
    "atkinson-standard": [8, [[1, 0, 1], [2, 0, 1], [-1, 1, 1], [0, 1, 1], [1, 1, 1], [0, 2, 1]]],
    stucki: [42, [[1, 0, 8], [2, 0, 4], [-2, 1, 2], [-1, 1, 4], [0, 1, 8], [1, 1, 4], [2, 1, 2], [-2, 2, 1], [-1, 2, 2], [0, 2, 4], [1, 2, 2], [2, 2, 1]]],
    "jarvis-judice-ninke": [48, [[1, 0, 7], [2, 0, 5], [-2, 1, 3], [-1, 1, 5], [0, 1, 7], [1, 1, 5], [2, 1, 3], [-2, 2, 1], [-1, 2, 3], [0, 2, 5], [1, 2, 3], [2, 2, 1]]],
  };

  const parseJson = (value, fallback = {}) => {
    try {
      const parsed = JSON.parse(value || "");
      return parsed && typeof parsed === "object" ? parsed : fallback;
    } catch {
      return fallback;
    }
  };

  const closestColor = (red, green, blue) => {
    const luma = (red * 250 + green * 350 + blue * 400) / (255 * 1000);
    let best = palette[0];
    let bestDistance = Number.POSITIVE_INFINITY;
    palette.forEach((color) => {
      const dr = red - color[0];
      const dg = green - color[1];
      const db = blue - color[2];
      const colorLuma = (color[0] * 250 + color[1] * 350 + color[2] * 400) / (255 * 1000);
      const rgbDistance = (dr * dr * 0.25 + dg * dg * 0.35 + db * db * 0.4) * 0.75 / (255 * 255);
      const lumaDistance = (luma - colorLuma) ** 2;
      const distance = 1.5 * rgbDistance + 0.6 * lumaDistance;
      if (distance < bestDistance) {
        bestDistance = distance;
        best = color;
      }
    });
    return best;
  };

  const ditherImage = (imageData, algorithm, strength) => {
    const definition = kernels[algorithm] || kernels.atkinson;
    const [divisor, kernel] = definition;
    const { width, height } = imageData;
    const source = new Float32Array(imageData.data.length);
    source.set(imageData.data);
    const output = imageData.data;
    const activeStrength = clamp(Number(strength), 0, 5);

    for (let y = 0; y < height; y += 1) {
      for (let x = 0; x < width; x += 1) {
        const index = (y * width + x) * 4;
        const red = clamp(source[index], 0, 255);
        const green = clamp(source[index + 1], 0, 255);
        const blue = clamp(source[index + 2], 0, 255);
        const color = closestColor(red, green, blue);
        output[index] = color[0];
        output[index + 1] = color[1];
        output[index + 2] = color[2];
        output[index + 3] = 255;
        const error = [
          (red - color[0]) * activeStrength,
          (green - color[1]) * activeStrength,
          (blue - color[2]) * activeStrength,
        ];
        kernel.forEach(([offsetX, offsetY, weight]) => {
          const targetX = x + offsetX;
          const targetY = y + offsetY;
          if (targetX < 0 || targetX >= width || targetY >= height) return;
          const target = (targetY * width + targetX) * 4;
          source[target] += error[0] * weight / divisor;
          source[target + 1] += error[1] * weight / divisor;
          source[target + 2] += error[2] * weight / divisor;
        });
      }
    }
    return imageData;
  };

  const initPushStudio = (root) => {
    const stage = root.querySelector("[data-editor-stage]");
    const canvas = root.querySelector("[data-editor-canvas]");
    const loading = root.querySelector("[data-editor-loading]");
    const captionInput = root.querySelector("[data-caption-input]");
    const captionPreview = root.querySelector("[data-caption-preview]");
    const captionBar = root.querySelector("[data-caption-bar]");
    const metaPreview = root.querySelector("[data-meta-preview]");
    const previewMode = root.querySelector("[data-preview-mode]");
    const zoomInput = root.querySelector("[data-zoom-input]");
    const zoomValue = root.querySelector("[data-zoom-value]");
    const panX = root.querySelector("[data-pan-x]");
    const panY = root.querySelector("[data-pan-y]");
    const fitMode = root.querySelector("[data-fit-mode]");
    const ditherEnabled = root.querySelector("[data-dither-enabled]");
    const ditherType = root.querySelector("[data-dither-type]");
    const ditherStrength = root.querySelector("[data-dither-strength]");
    const brightness = root.querySelector("[data-brightness]");
    const contrast = root.querySelector("[data-contrast]");
    const saturation = root.querySelector("[data-saturation]");
    const saveButton = root.querySelector("[data-save-button]");
    const pushButton = root.querySelector("[data-push-button]");
    const saveState = root.querySelector("[data-save-state]");
    const resetButton = root.querySelector("[data-reset-button]");
    const fitButton = root.querySelector("[data-fit-button]");
    const rotateButton = root.querySelector("[data-rotate-button]");
    const frameButtons = [...root.querySelectorAll("[data-frame-orientation]")];
    const autoButton = root.querySelector("[data-auto-button]");
    const captionToggle = root.querySelector("[data-toggle-caption]");
    const dateToggle = root.querySelector("[data-toggle-date]");
    const locationToggle = root.querySelector("[data-toggle-location]");
    if (!stage || !canvas || !captionInput || !zoomInput) return;

    const context = canvas.getContext("2d", { willReadFrequently: true });
    const original = new Image();
    const initialCrop = parseJson(root.dataset.crop, {});
    const initialRender = parseJson(root.dataset.render, {});
    const state = {
      offsetX: Number(initialCrop.offset_x ?? initialCrop.x ?? 0),
      offsetY: Number(initialCrop.offset_y ?? initialCrop.y ?? 0),
      scale: clamp(Number(initialCrop.scale || 1), 0.1, 3),
      rotation: Number(initialCrop.rotation || 0) % 360,
      frameOrientation: initialRender.frame_orientation === "portrait" ? "portrait" : "landscape",
      fitMode: initialCrop.fit_mode === "contain" ? "contain" : "fill",
      ditherEnabled: initialRender.dither_enabled !== false,
      ditherType: initialRender.dither_type || "atkinson",
      ditherStrength: clamp(Number(initialRender.dither_strength ?? 1), 0, 5),
      brightness: clamp(Number(initialRender.brightness ?? 1.1), 0.5, 1.8),
      contrast: clamp(Number(initialRender.contrast ?? 1.2), 0.5, 2),
      saturation: clamp(Number(initialRender.saturation ?? 1.2), 0, 2),
      dragging: false,
      dragStartX: 0,
      dragStartY: 0,
      originX: 0,
      originY: 0,
      renderTimer: 0,
    };

    const syncCanvasLayout = () => {
      const portrait = state.frameOrientation === "portrait";
      const width = portrait ? 480 : 800;
      const height = portrait ? 800 : 480;
      if (canvas.width !== width) canvas.width = width;
      if (canvas.height !== height) canvas.height = height;
      stage.classList.toggle("is-portrait", portrait);
      stage.style.setProperty("--editor-aspect", portrait ? "480 / 800" : "800 / 480");
      stage.style.setProperty("--caption-ratio", portrait ? "12%" : "10%");
    };

    const imageHeight = () => state.frameOrientation === "portrait" ? 704 : 432;

    const drawCaption = () => {
      const top = imageHeight();
      const portrait = state.frameOrientation === "portrait";
      const margin = portrait ? 24 : 28;
      context.fillStyle = "#fff";
      context.fillRect(0, top, canvas.width, canvas.height - top);
      context.fillStyle = "#111611";
      context.textBaseline = "alphabetic";

      if (captionToggle?.checked) {
        let size = portrait ? 20 : 17;
        const caption = captionInput.value.trim() || "未命名照片";
        const reserved = portrait ? 0 : 250;
        while (size > 15) {
          context.font = `600 ${size}px "Noto Sans SC", "Microsoft YaHei", sans-serif`;
          if (context.measureText(caption).width <= canvas.width - margin * 2 - reserved) break;
          size -= 1;
        }
        context.fillText(caption, margin, portrait ? top + 38 : top + 31);
      }

      const meta = [];
      if (locationToggle?.checked && root.dataset.location) meta.push(root.dataset.location);
      if (dateToggle?.checked && root.dataset.date) meta.push(root.dataset.date);
      if (meta.length) {
        context.font = `400 17px "Noto Sans SC", "Microsoft YaHei", sans-serif`;
        const text = meta.join(" · ");
        const x = portrait ? margin : canvas.width - margin - context.measureText(text).width;
        context.fillText(text, x, portrait ? top + 78 : top + 31);
      }
    };

    const syncControls = () => {
      zoomInput.value = state.scale.toFixed(2);
      if (zoomValue) zoomValue.textContent = state.scale.toFixed(2);
      if (panX) panX.textContent = String(Math.round(state.offsetX));
      if (panY) panY.textContent = String(Math.round(state.offsetY));
      if (fitMode) fitMode.value = state.fitMode;
      if (ditherEnabled) ditherEnabled.checked = state.ditherEnabled;
      if (ditherType) ditherType.value = state.ditherType;
      if (ditherStrength) ditherStrength.value = state.ditherStrength.toFixed(1);
      if (brightness) brightness.value = state.brightness.toFixed(2);
      if (contrast) contrast.value = state.contrast.toFixed(2);
      if (saturation) saturation.value = state.saturation.toFixed(2);
      root.querySelector("[data-dither-strength-value]").textContent = state.ditherStrength.toFixed(1);
      root.querySelector("[data-brightness-value]").textContent = state.brightness.toFixed(2);
      root.querySelector("[data-contrast-value]").textContent = state.contrast.toFixed(2);
      root.querySelector("[data-saturation-value]").textContent = state.saturation.toFixed(2);
      if (previewMode) previewMode.textContent = state.ditherEnabled ? "六色预览" : "原图构图";
      frameButtons.forEach((button) => {
        const active = button.dataset.frameOrientation === state.frameOrientation;
        button.classList.toggle("active", active);
        button.setAttribute("aria-pressed", String(active));
      });
      if (captionPreview) captionPreview.textContent = captionInput.value.trim() || "未命名照片";
      if (captionBar && captionToggle) captionBar.hidden = !captionToggle.checked;
      if (metaPreview) {
        const parts = [];
        if (locationToggle?.checked && root.dataset.location) parts.push(root.dataset.location);
        if (dateToggle?.checked && root.dataset.date) parts.push(root.dataset.date);
        metaPreview.textContent = parts.join(" · ");
      }
    };

    const drawOriginal = () => {
      if (!original.complete || !original.naturalWidth) return;
      syncCanvasLayout();
      const quarterTurn = state.rotation % 180 !== 0;
      const rotatedWidth = quarterTurn ? original.naturalHeight : original.naturalWidth;
      const rotatedHeight = quarterTurn ? original.naturalWidth : original.naturalHeight;
      const baseScale = state.fitMode === "contain"
        ? Math.min(canvas.width / rotatedWidth, imageHeight() / rotatedHeight)
        : Math.max(canvas.width / rotatedWidth, imageHeight() / rotatedHeight);
      const totalScale = baseScale * state.scale;
      context.save();
      context.fillStyle = "#fff";
      context.fillRect(0, 0, canvas.width, canvas.height);
      context.beginPath();
      context.rect(0, 0, canvas.width, imageHeight());
      context.clip();
      context.translate(canvas.width / 2 + state.offsetX, imageHeight() / 2 + state.offsetY);
      context.rotate(state.rotation * Math.PI / 180);
      context.scale(totalScale, totalScale);
      context.filter = `brightness(${state.brightness}) contrast(${state.contrast}) saturate(${state.saturation})`;
      context.drawImage(original, -original.naturalWidth / 2, -original.naturalHeight / 2);
      context.restore();
      drawCaption();
    };

    const render = (withDither = state.ditherEnabled) => {
      drawOriginal();
      if (withDither && state.ditherEnabled) {
        const imageData = context.getImageData(0, 0, canvas.width, canvas.height);
        context.putImageData(ditherImage(imageData, state.ditherType, state.ditherStrength), 0, 0);
      }
      syncControls();
    };

    const scheduleRender = (delay = 90) => {
      window.clearTimeout(state.renderTimer);
      drawOriginal();
      syncControls();
      state.renderTimer = window.setTimeout(() => render(), delay);
    };

    const updateScaleAt = (nextScale, canvasX, canvasY) => {
      const scaleRatio = nextScale / state.scale;
      const centerX = canvas.width / 2;
      const centerY = canvas.height / 2;
      state.offsetX = canvasX - centerX - (canvasX - centerX - state.offsetX) * scaleRatio;
      state.offsetY = canvasY - centerY - (canvasY - centerY - state.offsetY) * scaleRatio;
      state.scale = nextScale;
    };

    fitMode.value = state.fitMode;
    ditherEnabled.checked = state.ditherEnabled;
    ditherType.value = state.ditherType;

    original.addEventListener("load", () => {
      if (loading) loading.hidden = true;
      render();
    });
    original.addEventListener("error", () => {
      if (loading) loading.textContent = "原图载入失败，请返回画廊确认文件仍然存在。";
    });
    original.src = root.dataset.sourceUrl;

    stage.addEventListener("pointerdown", (event) => {
      const rect = canvas.getBoundingClientRect();
      const canvasY = (event.clientY - rect.top) * canvas.height / rect.height;
      if (event.button !== 0 || canvasY >= imageHeight()) return;
      state.dragging = true;
      state.dragStartX = event.clientX;
      state.dragStartY = event.clientY;
      state.originX = state.offsetX;
      state.originY = state.offsetY;
      stage.setPointerCapture(event.pointerId);
      stage.classList.add("dragging");
    });
    stage.addEventListener("pointermove", (event) => {
      if (!state.dragging) return;
      const rect = canvas.getBoundingClientRect();
      state.offsetX = state.originX + (event.clientX - state.dragStartX) * canvas.width / rect.width;
      state.offsetY = state.originY + (event.clientY - state.dragStartY) * canvas.height / rect.height;
      drawOriginal();
      syncControls();
    });
    const endDrag = (event) => {
      if (!state.dragging) return;
      state.dragging = false;
      stage.classList.remove("dragging");
      if (stage.hasPointerCapture(event.pointerId)) stage.releasePointerCapture(event.pointerId);
      scheduleRender(30);
    };
    stage.addEventListener("pointerup", endDrag);
    stage.addEventListener("pointercancel", endDrag);

    stage.addEventListener("wheel", (event) => {
      event.preventDefault();
      const rect = canvas.getBoundingClientRect();
      const canvasX = clamp((event.clientX - rect.left) * canvas.width / rect.width, 0, canvas.width);
      const canvasY = clamp((event.clientY - rect.top) * canvas.height / rect.height, 0, canvas.height);
      const nextScale = clamp(state.scale * (event.deltaY > 0 ? 0.9 : 1.1), 0.1, 3);
      updateScaleAt(nextScale, canvasX, canvasY);
      scheduleRender(120);
    }, { passive: false });

    stage.addEventListener("keydown", (event) => {
      const step = event.shiftKey ? 10 : 2;
      if (["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) event.preventDefault();
      if (event.key === "ArrowLeft") state.offsetX -= step;
      if (event.key === "ArrowRight") state.offsetX += step;
      if (event.key === "ArrowUp") state.offsetY -= step;
      if (event.key === "ArrowDown") state.offsetY += step;
      if (["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) scheduleRender(60);
    });

    zoomInput.addEventListener("input", () => {
      state.scale = clamp(Number(zoomInput.value || 1), 0.1, 3);
      scheduleRender();
    });
    fitMode.addEventListener("change", () => {
      state.fitMode = fitMode.value === "contain" ? "contain" : "fill";
      state.offsetX = 0;
      state.offsetY = 0;
      state.scale = 1;
      scheduleRender(20);
    });
    ditherEnabled.addEventListener("change", () => {
      state.ditherEnabled = ditherEnabled.checked;
      render();
    });
    ditherType.addEventListener("change", () => {
      state.ditherType = ditherType.value;
      render();
    });
    [
      [ditherStrength, "ditherStrength"],
      [brightness, "brightness"],
      [contrast, "contrast"],
      [saturation, "saturation"],
    ].forEach(([input, key]) => input?.addEventListener("input", () => {
      state[key] = Number(input.value);
      scheduleRender();
    }));

    captionInput.addEventListener("input", () => scheduleRender(60));
    captionToggle?.addEventListener("change", () => scheduleRender(20));
    dateToggle?.addEventListener("change", () => scheduleRender(20));
    locationToggle?.addEventListener("change", () => scheduleRender(20));
    fitButton?.addEventListener("click", () => {
      state.offsetX = 0;
      state.offsetY = 0;
      state.scale = 1;
      scheduleRender(20);
    });
    rotateButton?.addEventListener("click", () => {
      state.rotation = (state.rotation + 90) % 360;
      state.offsetX = 0;
      state.offsetY = 0;
      state.scale = 1;
      scheduleRender(20);
    });
    frameButtons.forEach((button) => button.addEventListener("click", () => {
      state.frameOrientation = button.dataset.frameOrientation === "portrait" ? "portrait" : "landscape";
      state.offsetX = 0;
      state.offsetY = 0;
      state.scale = 1;
      scheduleRender(20);
    }));
    resetButton?.addEventListener("click", () => {
      Object.assign(state, {
        offsetX: 0, offsetY: 0, scale: 1, rotation: 0, frameOrientation: "landscape", fitMode: "fill",
        ditherEnabled: true, ditherType: "atkinson", ditherStrength: 1,
        brightness: 1.1, contrast: 1.2, saturation: 1.2,
      });
      scheduleRender(20);
    });

    autoButton?.addEventListener("click", () => {
      drawOriginal();
      const data = context.getImageData(0, 0, canvas.width, canvas.height).data;
      let brightnessSum = 0;
      let redSum = 0;
      let greenSum = 0;
      let blueSum = 0;
      const pixelCount = data.length / 4;
      for (let index = 0; index < data.length; index += 4) {
        brightnessSum += (data[index] + data[index + 1] + data[index + 2]) / 3;
        redSum += data[index]; greenSum += data[index + 1]; blueSum += data[index + 2];
      }
      const averageBrightness = brightnessSum / pixelCount;
      const averageRed = redSum / pixelCount;
      const averageGreen = greenSum / pixelCount;
      const averageBlue = blueSum / pixelCount;
      let contrastSum = 0;
      let variance = 0;
      for (let index = 0; index < data.length; index += 4) {
        const pixelBrightness = (data[index] + data[index + 1] + data[index + 2]) / 3;
        contrastSum += Math.abs(pixelBrightness - averageBrightness);
        variance += (data[index] - averageRed) ** 2 + (data[index + 1] - averageGreen) ** 2 + (data[index + 2] - averageBlue) ** 2;
      }
      const normalizedBrightness = averageBrightness / 255;
      const normalizedContrast = contrastSum / pixelCount / 127;
      const colorSaturation = Math.min(1, Math.sqrt(variance / (3 * pixelCount)) / 255);
      state.ditherType = colorSaturation > 0.6 ? "stucki"
        : normalizedContrast < 0.3 ? "atkinson-standard"
          : normalizedBrightness < 0.3 || normalizedBrightness > 0.7 ? "jarvis-judice-ninke"
            : "floyd-steinberg";
      state.ditherStrength = normalizedContrast < 0.4
        ? 1.5 + (0.4 - normalizedContrast) * 2
        : normalizedContrast > 0.7 ? 0.8 - (normalizedContrast - 0.7) * 2 : 1;
      if (colorSaturation > 0.5) state.ditherStrength *= 1.1;
      state.ditherStrength = clamp(Number(state.ditherStrength.toFixed(1)), 0.5, 3);
      state.contrast = normalizedBrightness < 0.4
        ? 1.4 + (0.4 - normalizedBrightness) * 0.8
        : normalizedBrightness > 0.7 ? 1 - (normalizedBrightness - 0.7) * 0.5 : 1.2;
      state.contrast = clamp(Number(state.contrast.toFixed(1)), 0.8, 2);
      state.ditherEnabled = true;
      if (saveState) saveState.textContent = "已按当前画面自动配置抖动参数。";
      render();
    });

    const payload = () => ({
      custom_side_caption: captionInput.value.trim(),
      manual_crop_json: {
        scale: Number(state.scale.toFixed(4)),
        offset_x: Math.round(state.offsetX),
        offset_y: Math.round(state.offsetY),
        rotation: state.rotation,
        fit_mode: state.fitMode,
      },
      render_overrides_json: {
        display_defaults_version: 2,
        frame_orientation: state.frameOrientation,
        show_caption: Boolean(captionToggle?.checked),
        show_date: Boolean(dateToggle?.checked),
        show_location: Boolean(locationToggle?.checked),
        dither_enabled: state.ditherEnabled,
        dither_type: state.ditherType,
        dither_strength: Number(state.ditherStrength.toFixed(1)),
        brightness: Number(state.brightness.toFixed(2)),
        contrast: Number(state.contrast.toFixed(2)),
        saturation: Number(state.saturation.toFixed(2)),
      },
    });

    const saveOverrides = async () => {
      const response = await fetch(root.dataset.saveUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload()),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.ok) throw new Error(data.error || response.statusText);
      return data;
    };

    saveButton?.addEventListener("click", async () => {
      saveButton.disabled = true;
      if (saveState) saveState.textContent = "保存中...";
      try {
        await saveOverrides();
        if (saveState) saveState.textContent = "参数已保存。";
      } catch (error) {
        if (saveState) saveState.textContent = `保存失败：${error.message}`;
      } finally {
        saveButton.disabled = false;
      }
    });

    pushButton?.addEventListener("click", async () => {
      pushButton.disabled = true;
      if (saveState) saveState.textContent = "正在保存并生成设备成品...";
      try {
        await saveOverrides();
        let token = window.sessionStorage.getItem("inktimePushToken") || "";
        let response = await fetch(root.dataset.pushUrl, {
          method: "POST",
          headers: token ? { "X-Push-Token": token } : {},
        });
        if (response.status === 401) {
          token = window.prompt("请输入推送 token") || "";
          if (!token) throw new Error("已取消推送");
          window.sessionStorage.setItem("inktimePushToken", token);
          response = await fetch(root.dataset.pushUrl, {
            method: "POST",
            headers: { "X-Push-Token": token },
          });
        }
        const data = await response.json().catch(() => ({}));
        if (!response.ok || !data.ok) throw new Error(data.error || response.statusText);
        if (saveState) saveState.textContent = "已更新 latest.bmp，设备可读取新成品。";
      } catch (error) {
        if (saveState) saveState.textContent = `推送失败：${error.message}`;
      } finally {
        pushButton.disabled = false;
      }
    });

    syncControls();
  };

  const initDetailCaptionEditor = (root) => {
    const input = root.querySelector("[data-detail-caption-input]");
    const button = root.querySelector("[data-detail-caption-save]");
    const state = root.querySelector("[data-detail-caption-state]");
    if (!input || !button) return;

    button.addEventListener("click", async () => {
      button.disabled = true;
      if (state) state.textContent = "保存中...";
      try {
        const response = await fetch(root.dataset.saveUrl, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ custom_side_caption: input.value.trim() }),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok || !data.ok) throw new Error(data.error || response.statusText);
        if (state) state.textContent = input.value.trim() ? "已应用人工覆盖" : "已恢复使用 AI 文案";
      } catch (error) {
        if (state) state.textContent = `保存失败：${error.message}`;
      } finally {
        button.disabled = false;
      }
    });
  };

  document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll("[data-push-studio]").forEach(initPushStudio);
    document.querySelectorAll("[data-detail-caption-editor]").forEach(initDetailCaptionEditor);
  });
})();
