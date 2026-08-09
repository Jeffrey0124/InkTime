(() => {
  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";
  const withCsrf = (headers = {}) => ({ ...headers, "X-CSRF-Token": csrfToken });
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
        headers: withCsrf({ "Content-Type": "application/json" }),
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
          headers: withCsrf(token ? { "X-Push-Token": token } : {}),
        });
        if (response.status === 401) {
          token = window.prompt("请输入推送 token") || "";
          if (!token) throw new Error("已取消推送");
          window.sessionStorage.setItem("inktimePushToken", token);
          response = await fetch(root.dataset.pushUrl, {
            method: "POST",
            headers: withCsrf({ "X-Push-Token": token }),
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
          headers: withCsrf({ "Content-Type": "application/json" }),
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

  const initDashboardLog = (button) => {
    const listId = button.getAttribute("aria-controls");
    const list = listId ? document.getElementById(listId) : null;
    if (!list) return;
    button.addEventListener("click", () => {
      const expanded = button.getAttribute("aria-expanded") === "true";
      button.setAttribute("aria-expanded", String(!expanded));
      button.textContent = expanded ? "展开" : "折叠";
      list.hidden = expanded;
    });
  };

  const initLibrarySelection = (root) => {
    const storageKey = "inktime:library-selection:v1";
    const context = parseJson(root.dataset.selectionContext, {});
    const dialog = root.querySelector("[data-analysis-task-dialog]");
    const form = root.querySelector("[data-analysis-task-form]");
    const summary = root.querySelector("[data-task-summary]");
    const taskState = root.querySelector("[data-task-state]");
    const highCost = root.querySelector("[data-high-cost]");
    const count = root.querySelector("[data-selection-count]");
    const selectionState = root.querySelector("[data-selection-state]");
    let state = parseJson(sessionStorage.getItem(storageKey), { photoIds: [], selection: null });
    let preview = null;
    let previewRequest = 0;

    const save = () => sessionStorage.setItem(storageKey, JSON.stringify(state));
    const render = () => {
      const selected = new Set((state.photoIds || []).map(Number));
      root.querySelectorAll("[data-library-photo]").forEach((input) => {
        input.checked = selected.has(Number(input.value));
      });
      count.textContent = String(selected.size);
    };
    const api = async (url, options = {}) => {
      const response = await fetch(url, {
        ...options,
        headers: withCsrf({ "Content-Type": "application/json", ...(options.headers || {}) }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || data.ok === false) throw new Error(data.error || response.statusText);
      return data;
    };
    const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    })[char]);
    const taskType = () => form.elements.namedItem("task_type").value;
    const currentSelection = () => state.selection || {
      kind: "manual",
      photo_ids: state.photoIds || [],
      filters: {},
      sort: "filename",
      order: "asc",
    };
    const describeReasons = (reasons) => Object.entries(reasons || {})
      .map(([reason, value]) => `${({ already_analyzed: "已分析", not_analyzed: "尚未分析", file_unavailable: "文件不可用", not_active: "已归档", occupied: "已被任务占用" })[reason] || reason} ${value}`)
      .join("，");
    const loadPreview = async ({ updateSelection = true } = {}) => {
      const requestId = ++previewRequest;
      summary.textContent = "正在核对素材资格…";
      taskState.textContent = "";
      const data = await api("/api/library/selection-preview", {
        method: "POST",
        body: JSON.stringify({ task_type: taskType(), selection: currentSelection() }),
      });
      if (requestId !== previewRequest) return null;
      preview = data.selection;
      if (updateSelection) {
        if (state.selection?.kind !== "manual") state.photoIds = preview.photo_ids;
        if (state.selection) {
          if (state.selection.sort === "random") state.selection.seed = preview.seed;
          if (state.selection.kind !== "manual") {
            state.selection.frozen_photo_ids = preview.frozen_photo_ids;
          }
        }
        save(); render();
      }
      const levels = preview.execution_levels.map((item) => `${item.channel_name} / ${item.model_id}`).join(" → ");
      const excluded = describeReasons(preview.excluded_reasons);
      summary.innerHTML = `<strong>最终 ${escapeHtml(preview.selected_count)} 张</strong><span>匹配 ${escapeHtml(preview.matched_count)} · 合格 ${escapeHtml(preview.eligible_count)} · 排除 ${escapeHtml(preview.excluded_count)}${excluded ? `（${escapeHtml(excluded)}）` : ""}</span><span>模型：${escapeHtml(levels)}</span><span>最多 ${escapeHtml(preview.max_request_rounds)} 轮</span>`;
      form.elements.namedItem("concurrency").value = String(preview.concurrency || 1);
      highCost.hidden = !preview.requires_high_cost_confirmation;
      highCost.querySelector("input").checked = false;
      return preview;
    };
    const selectBy = async (selection) => {
      state.selection = selection;
      selectionState.textContent = "正在核对…";
      try {
        await loadPreview();
        selectionState.textContent = "选择已更新";
      } catch (error) { selectionState.textContent = `选择失败：${error.message}`; }
    };

    root.querySelectorAll("[data-library-photo]").forEach((input) => {
      input.addEventListener("change", () => {
        const selected = new Set((state.photoIds || []).map(Number));
        if (input.checked) selected.add(Number(input.value)); else selected.delete(Number(input.value));
        state.photoIds = [...selected];
        state.selection = { kind: "manual", photo_ids: state.photoIds, filters: {}, sort: "filename", order: "asc" };
        save(); render();
      });
    });
    root.querySelector("[data-select-filtered]").addEventListener("click", () => selectBy({ kind: "all", ...context }));
    root.querySelector("[data-select-top]").addEventListener("click", () => {
      const limit = Math.max(1, Number(root.querySelector("[data-select-limit]").value || 1));
      const selection = { kind: "top_n", limit, ...context };
      if (selection.sort === "random") selection.seed = window.crypto.randomUUID();
      selectBy(selection);
    });
    root.querySelector("[data-selection-clear]").addEventListener("click", () => {
      state = { photoIds: [], selection: null }; preview = null; selectionState.textContent = ""; save(); render();
    });
    root.querySelector("[data-task-open]").addEventListener("click", async () => {
      if (!(state.photoIds || []).length && !state.selection) { selectionState.textContent = "请先选择素材"; return; }
      dialog.showModal();
      try { await loadPreview(); } catch (error) { summary.textContent = `无法核对：${error.message}`; }
    });
    form.querySelectorAll('input[name="task_type"]').forEach((input) => input.addEventListener("change", () => {
      loadPreview().catch((error) => { summary.textContent = `无法核对：${error.message}`; });
    }));
    form.addEventListener("submit", async (event) => {
      if (event.submitter?.value === "cancel") { dialog.close(); return; }
      event.preventDefault();
      if (!preview?.photo_ids?.length) { taskState.textContent = "没有可创建任务的素材"; return; }
      if (!highCost.hidden && !form.elements.namedItem("confirmed_high_cost").checked) {
        taskState.textContent = "请先确认数量和费用"; return;
      }
      taskState.textContent = "正在创建任务…";
      try {
        const data = await api("/api/analysis-tasks", {
          method: "POST",
          body: JSON.stringify({
            task_type: taskType(),
            name: form.elements.namedItem("name").value.trim(),
            concurrency: Number(form.elements.namedItem("concurrency").value || 1),
            confirmed_high_cost: form.elements.namedItem("confirmed_high_cost").checked,
            photo_ids: preview.photo_ids,
            strategy_snapshot: {
              execution_levels: preview.execution_levels,
              max_request_rounds: preview.max_request_rounds,
            },
          }),
        });
        state = { photoIds: [], selection: null }; save(); render();
        window.location.assign(`/analysis-tasks/${data.task.task_id}`);
      } catch (error) { taskState.textContent = `创建失败：${error.message}`; }
    });
    render();
  };

  const initTaskCenter = (root) => {
    const list = root.querySelector("[data-task-list]");
    const state = root.querySelector("[data-task-sync-state]");
    const panel = root.querySelector("[data-notification-panel]");
    const toggle = root.querySelector("[data-notification-toggle]");
    const count = root.querySelector("[data-notification-count]");
    let timer = null;
    let slowPolling = false;
    const escapeHtml = (value) => String(value ?? "").replace(/[&<>'"]/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[character]);
    const taskMarkup = (task) => `
      <a class="task-list-row" href="/analysis-tasks/${task.task_id}">
        <span class="state-label">${escapeHtml(task.queue_position ? `队列 ${task.queue_position}` : "历史")}</span><strong>${escapeHtml(task.name)}</strong>
        <span>${task.processed_count}/${task.total_count} 张</span><span>${escapeHtml(task.created_at || "-")}</span>
      </a>`;
    const renderNotifications = (notifications) => {
      const unread = notifications.filter((item) => !item.is_read);
      count.textContent = String(unread.length);
      panel.innerHTML = notifications.length ? notifications.map((item) => `
        <a class="notification-row ${item.is_read ? "" : "unread"}" data-notification-id="${item.id}" href="${item.target_url || "/analysis-tasks"}">
          <strong>${escapeHtml(item.title)}</strong><span>${escapeHtml(item.message)}</span>
        </a>`).join("") : "<p>暂无应用内通知。</p>";
      panel.querySelectorAll("[data-notification-id]").forEach((item) => {
        item.addEventListener("click", () => {
          fetch(`/api/notifications/${item.dataset.notificationId}/read`, { method: "POST", headers: withCsrf() });
        });
      });
    };
    const refresh = async () => {
      try {
        const [tasks, notices] = await Promise.all([api("/api/analysis-tasks"), api("/api/notifications")]);
        list.innerHTML = tasks.tasks.length ? tasks.tasks.map(taskMarkup).join("") : "<p class=\"settings-empty\">暂无分析任务。</p>";
        renderNotifications(notices.notifications);
        slowPolling = false;
        state.textContent = "数据每 2 秒更新";
      } catch (_) {
        slowPolling = true;
        state.textContent = "连接中断，正在以低频轮询重试";
      } finally {
        window.clearTimeout(timer);
        timer = window.setTimeout(refresh, slowPolling ? 10000 : 2000);
      }
    };
    toggle?.addEventListener("click", () => { panel.hidden = !panel.hidden; });
    window.addEventListener("online", () => { slowPolling = false; refresh(); });
    refresh();
  };

  const initTaskDetail = (root) => {
    const taskId = root.dataset.taskId;
    const sync = root.querySelector("[data-task-sync-state]");
    let timer = null;
    let slowPolling = false;
    const list = root.querySelector("[data-task-item-list]");
    const filter = root.querySelector("[data-task-item-filter]");
    let snapshotItems = [];
    const escapeHtml = (value) => String(value ?? "").replace(/[&<>'"]/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[character]);
    const renderItems = () => {
      const selected = filter?.value || "all";
      const visible = snapshotItems.filter((item) => selected === "all" || (selected === "completed" ? item.status === "completed" : selected === "failed" ? item.status === "failed" : item.status === "queued"));
      list.innerHTML = visible.length ? visible.map((item) => `<tr data-task-item-status="${escapeHtml(item.status)}"><td>${escapeHtml(item.filename)}</td><td>${escapeHtml(item.status)}</td><td>${escapeHtml(item.current_execution_level || "-")}</td><td>${Number(item.attempt_count || 0)}</td><td>${escapeHtml(item.duration_seconds ?? "-")} 秒</td><td>${escapeHtml(item.error_message || "-")}</td></tr>`).join("") : "<tr><td colspan=\"6\">当前筛选没有素材。</td></tr>";
    };
    const refresh = async () => {
      try {
        const data = await api(`/api/analysis-tasks/${taskId}/snapshot`);
        const task = data.task;
        root.querySelector("[data-task-status]").textContent = task.status;
        root.querySelector("[data-task-summary]").textContent = `${task.task_type === "reanalysis" ? "重新分析" : "增量分析"} · ${task.total_count} 张 · 并发 ${task.concurrency} · 当前 ${task.current_filename || "等待领取"}`;
        root.querySelectorAll("[data-task-count]").forEach((node) => { node.textContent = task[node.dataset.taskCount]; });
        snapshotItems = data.items;
        renderItems();
        slowPolling = false;
        sync.textContent = "数据每 2 秒更新";
      } catch (_) {
        slowPolling = true;
        sync.textContent = "连接中断，正在以低频轮询重试";
      } finally {
        window.clearTimeout(timer);
        timer = window.setTimeout(refresh, slowPolling ? 10000 : 2000);
      }
    };
    window.addEventListener("online", () => { slowPolling = false; refresh(); });
    filter?.addEventListener("change", renderItems);
    refresh();
  };

  const initSettings = async (root) => {
    const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    })[char]);
    const api = async (url, options = {}) => {
      const response = await fetch(url, {
        ...options,
        headers: withCsrf({ ...(options.body ? { "Content-Type": "application/json" } : {}), ...(options.headers || {}) }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || data.ok === false) throw new Error(data.error || response.statusText);
      return data;
    };
    const state = { channels: [], presets: [], fallback: [] };
    const dirtySections = new Set();
    const editableSections = ["analysis_defaults", "scan_settings", "security_settings"];
    const channelList = root.querySelector("[data-channel-list]");
    const fallbackList = root.querySelector("[data-fallback-list]");
    const warning = root.querySelector("[data-settings-warning]");

    const markDirty = (section) => { dirtySections.add(section); };
    const clearDirty = (section) => { dirtySections.delete(section); };
    window.addEventListener("beforeunload", (event) => {
      if (!dirtySections.size) return;
      event.preventDefault();
      event.returnValue = "";
    });

    root.querySelectorAll("[data-settings-tab]").forEach((button) => {
      button.addEventListener("click", () => {
        root.querySelectorAll("[data-settings-tab]").forEach((item) => item.classList.toggle("active", item === button));
        root.querySelectorAll("[data-settings-panel]").forEach((panel) => panel.classList.toggle("active", panel.dataset.settingsPanel === button.dataset.settingsTab));
      });
    });

    const modelRow = (model = {}) => `
      <div class="model-row" data-model-row>
        <input data-model-id aria-label="模型 ID" value="${escapeHtml(model.model_id || "")}" placeholder="模型 ID">
        <input data-model-name aria-label="显示名称" value="${escapeHtml(model.name || model.model_id || "")}" placeholder="显示名称">
        <label class="model-default"><input type="radio" name="default-${escapeHtml(model.channel_id || "new")}" data-model-default ${model.is_default ? "checked" : ""}> 默认</label>
        <button class="icon-button" type="button" title="移除模型" data-model-remove aria-label="移除模型">×</button>
      </div>`;

    const channelMarkup = (channel) => `
        <article class="channel-editor" data-channel-id="${escapeHtml(channel.id)}">
          <div class="channel-editor-head"><div><strong>${escapeHtml(channel.name)}</strong><span data-channel-version>${channel._draft ? "未保存" : `v${channel.version || 0}`}</span></div><label class="toggle-field"><input type="checkbox" data-channel-enabled ${channel.enabled ? "checked" : ""}> 启用</label></div>
          <div class="settings-field-grid channel-fields">
            <label>通道名称<input data-channel-name value="${escapeHtml(channel.name)}"></label>
            <label>供应商<select data-channel-provider>${state.presets.map((preset) => `<option value="${escapeHtml(preset.id)}" ${preset.id === channel.provider ? "selected" : ""}>${escapeHtml(preset.label)}</option>`).join("")}</select></label>
            <label class="wide-field">API 地址<input data-channel-url value="${escapeHtml(channel.base_url)}"></label>
            <label>超时（秒）<input data-channel-timeout type="number" min="1" value="${escapeHtml(channel.timeout)}"></label>
            <label>凭据来源<select data-credential-source><option value="none" ${channel.credential.source === "none" ? "selected" : ""}>无需凭据</option><option value="environment" ${channel.credential.source === "environment" ? "selected" : ""}>环境变量</option><option value="database" ${channel.credential.source === "database" ? "selected" : ""}>数据库加密</option></select></label>
            <label>环境变量<input data-credential-env value="${escapeHtml(channel.credential.env_name || "")}" placeholder="QWEN_API_KEY"></label>
            <label>新密钥<input data-credential-value type="password" autocomplete="new-password" placeholder="${channel.credential.configured ? "已配置，留空保持不变" : "输入密钥"}"></label>
          </div>
          <div class="model-editor"><div class="model-editor-head"><strong>模型</strong><div><button class="button" type="button" data-model-add>手动添加</button><button class="button" type="button" data-model-discover>自动发现</button></div></div><div data-model-list>${channel.models.map((model) => modelRow({ ...model, channel_id: channel.id })).join("")}</div></div>
          <div class="channel-actions"><button class="button" type="button" data-test-connection ${channel._draft ? "disabled" : ""}>基础连接测试</button><button class="button" type="button" data-test-vision ${channel._draft ? "disabled" : ""}>视觉能力测试</button><span class="save-state" data-channel-state></span><button class="text-button danger" type="button" data-channel-delete>${channel._draft ? "放弃" : "删除"}</button><button class="primary-button" type="button" data-channel-save>保存通道</button></div>
        </article>`;

    const renderChannels = () => {
      channelList.innerHTML = state.channels.length ? state.channels.map(channelMarkup).join("") : '<div class="settings-empty">尚未配置模型通道</div>';
      channelList.querySelectorAll("[data-channel-id]").forEach(bindChannel);
      renderFallback();
    };

    const collectChannel = (card) => ({
      id: card.dataset.channelId,
      name: card.querySelector("[data-channel-name]").value.trim(),
      provider: card.querySelector("[data-channel-provider]").value,
      base_url: card.querySelector("[data-channel-url]").value.trim(),
      timeout: Number(card.querySelector("[data-channel-timeout]").value || 100),
      enabled: card.querySelector("[data-channel-enabled]").checked,
      credential: {
        source: card.querySelector("[data-credential-source]").value,
        env_name: card.querySelector("[data-credential-env]").value.trim(),
        value: card.querySelector("[data-credential-value]").value,
      },
      models: [...card.querySelectorAll("[data-model-row]")].map((row) => ({
        model_id: row.querySelector("[data-model-id]").value.trim(),
        name: row.querySelector("[data-model-name]").value.trim(),
        is_default: row.querySelector("[data-model-default]").checked,
        enabled: true,
      })).filter((model) => model.model_id),
    });

    function bindChannel(card) {
      const status = card.querySelector("[data-channel-state]");
      const dirtyKey = () => `channel:${card.dataset.channelId}`;
      card.addEventListener("input", () => markDirty(dirtyKey()));
      card.querySelector("[data-model-list]").addEventListener("click", (event) => {
        const button = event.target.closest("[data-model-remove]");
        if (!button) return;
        button.closest("[data-model-row]").remove(); markDirty(dirtyKey());
      });
      card.querySelector("[data-model-add]").addEventListener("click", () => {
        card.querySelector("[data-model-list]").insertAdjacentHTML("beforeend", modelRow({ channel_id: card.dataset.channelId }));
        markDirty(dirtyKey());
      });
      card.querySelector("[data-channel-provider]").addEventListener("change", (event) => {
        const preset = state.presets.find((item) => item.id === event.target.value);
        if (preset) card.querySelector("[data-channel-url]").value = preset.base_url || "";
        markDirty(dirtyKey());
      });
      card.querySelector("[data-channel-save]").addEventListener("click", async () => {
        status.textContent = "保存中...";
        try {
          const oldId = card.dataset.channelId;
          const channel = state.channels.find((item) => item.id === oldId);
          const method = channel._draft ? "POST" : "PUT";
          const url = channel._draft
            ? "/api/settings/model-channels"
            : `/api/settings/model-channels/${oldId}`;
          const data = await api(url, { method, body: JSON.stringify(collectChannel(card)) });
          state.channels = state.channels.map((item) => item.id === oldId ? data.channel : item);
          clearDirty(`channel:${oldId}`);
          card.dataset.channelId = data.channel.id;
          card.querySelector(".channel-editor-head strong").textContent = data.channel.name;
          card.querySelector("[data-channel-version]").textContent = `v${data.channel.version}`;
          card.querySelectorAll("[data-test-connection], [data-test-vision]").forEach((button) => { button.disabled = false; });
          card.querySelector("[data-channel-delete]").textContent = "删除";
          status.textContent = "通道已保存";
          renderFallback();
        } catch (error) { status.textContent = `保存失败：${error.message}`; }
      });
      card.querySelector("[data-model-discover]").addEventListener("click", async () => {
        status.textContent = "发现模型中...";
        try {
          const data = await api(`/api/settings/model-channels/${card.dataset.channelId}/discover`, { method: "POST" });
          const existing = new Set([...card.querySelectorAll("[data-model-id]")].map((input) => input.value));
          data.models.filter((model) => !existing.has(model.model_id)).forEach((model) => card.querySelector("[data-model-list]").insertAdjacentHTML("beforeend", modelRow({ ...model, channel_id: card.dataset.channelId })));
          status.textContent = `发现 ${data.models.length} 个模型，保存后生效`; markDirty(dirtyKey());
        } catch (error) { status.textContent = `发现失败：${error.message}`; }
      });
      card.querySelector("[data-test-connection]").addEventListener("click", async () => {
        status.textContent = "测试连接中...";
        try { await api(`/api/settings/model-channels/${card.dataset.channelId}/test-connection`, { method: "POST" }); status.textContent = "基础连接正常"; } catch (error) { status.textContent = `连接失败：${error.message}`; }
      });
      card.querySelector("[data-test-vision]").addEventListener("click", async () => {
        const model = card.querySelector("[data-model-default]:checked")?.closest("[data-model-row]")?.querySelector("[data-model-id]")?.value || card.querySelector("[data-model-id]")?.value;
        if (!model) { status.textContent = "请先添加模型"; return; }
        status.textContent = "测试视觉能力中...";
        try { await api(`/api/settings/model-channels/${card.dataset.channelId}/test-vision`, { method: "POST", body: JSON.stringify({ model_id: model }) }); status.textContent = "视觉能力正常"; } catch (error) { status.textContent = `视觉测试失败：${error.message}`; }
      });
      card.querySelector("[data-channel-delete]").addEventListener("click", async () => {
        const channel = state.channels.find((item) => item.id === card.dataset.channelId);
        if (channel?._draft) {
          state.channels = state.channels.filter((item) => item.id !== channel.id);
          clearDirty(`channel:${channel.id}`);
          card.remove();
          if (!state.channels.length) channelList.innerHTML = '<div class="settings-empty">尚未配置模型通道</div>';
          renderFallback();
          return;
        }
        if (!window.confirm("删除未被引用的通道；有历史引用时将改为停用。继续吗？")) return;
        try {
          await api(`/api/settings/model-channels/${card.dataset.channelId}`, { method: "DELETE" });
          state.channels = state.channels.filter((item) => item.id !== card.dataset.channelId);
          clearDirty(dirtyKey());
          card.remove();
          renderFallback();
        } catch (error) { status.textContent = `操作失败：${error.message}`; }
      });
    }

    const fallbackOptions = () => state.channels.flatMap((channel) => channel.models.filter((model) => model.enabled).map((model) => ({ value: `${channel.id}\u0000${model.model_id}`, label: `${channel.name} / ${model.name}` })));
    const renderFallback = () => {
      if (!fallbackList) return;
      const options = fallbackOptions();
      fallbackList.innerHTML = state.fallback.map((item, index) => `<div class="fallback-row" data-fallback-row><span>${index + 1}</span><select>${options.map((option) => `<option value="${escapeHtml(option.value)}" ${option.value === `${item.channel_id}\u0000${item.model_id}` ? "selected" : ""}>${escapeHtml(option.label)}</option>`).join("")}</select><button class="icon-button" type="button" data-move-up title="上移">↑</button><button class="icon-button" type="button" data-move-down title="下移">↓</button><button class="icon-button" type="button" data-fallback-remove title="移除">×</button></div>`).join("");
      fallbackList.querySelectorAll("[data-fallback-row]").forEach((row, index) => {
        row.querySelector("select").addEventListener("change", () => markDirty("fallback_chain"));
        row.querySelector("[data-fallback-remove]").addEventListener("click", () => { state.fallback.splice(index, 1); renderFallback(); markDirty("fallback_chain"); });
        row.querySelector("[data-move-up]").addEventListener("click", () => { if (index) [state.fallback[index - 1], state.fallback[index]] = [state.fallback[index], state.fallback[index - 1]]; renderFallback(); markDirty("fallback_chain"); });
        row.querySelector("[data-move-down]").addEventListener("click", () => { if (index < state.fallback.length - 1) [state.fallback[index + 1], state.fallback[index]] = [state.fallback[index], state.fallback[index + 1]]; renderFallback(); markDirty("fallback_chain"); });
      });
    };

    const loadChannels = async () => {
      const [data, chain] = await Promise.all([api("/api/settings/model-channels"), api("/api/settings/fallback-chain")]);
      state.channels = data.channels; state.presets = data.presets; state.fallback = chain.items;
      if (data.capabilities.warning) { warning.hidden = false; warning.textContent = data.capabilities.warning; }
      else warning.hidden = true;
      renderChannels();
    };

    root.querySelector("[data-channel-add]").addEventListener("click", () => {
      const preset = state.presets[0] || { id: "custom", label: "新通道", base_url: "" };
      const draft = {
        id: `draft-${window.crypto.randomUUID()}`,
        _draft: true,
        name: `${preset.label} ${state.channels.length + 1}`,
        provider: preset.id,
        base_url: preset.base_url,
        timeout: 100,
        enabled: true,
        credential: { source: preset.credential_source || "none", configured: false, env_name: preset.credential_env || "" },
        models: [],
        version: 0,
      };
      channelList.querySelector(".settings-empty")?.remove();
      state.channels.push(draft);
      channelList.insertAdjacentHTML("beforeend", channelMarkup(draft));
      bindChannel(channelList.lastElementChild);
      markDirty(`channel:${draft.id}`);
    });
    root.querySelector("[data-fallback-add]").addEventListener("click", () => {
      const option = fallbackOptions().find((item) => !state.fallback.some((entry) => item.value === `${entry.channel_id}\u0000${entry.model_id}`));
      if (!option) return;
      const [channel_id, model_id] = option.value.split("\u0000");
      state.fallback.push({ channel_id, model_id }); renderFallback(); markDirty("fallback_chain");
    });
    root.querySelector("[data-fallback-save]").addEventListener("click", async () => {
      const output = [...fallbackList.querySelectorAll("select")].map((select) => {
        const [channel_id, model_id] = select.value.split("\u0000"); return { channel_id, model_id };
      });
      const status = root.querySelector("[data-fallback-state]");
      try { const data = await api("/api/settings/fallback-chain", { method: "PUT", body: JSON.stringify({ items: output }) }); state.fallback = data.items; clearDirty("fallback_chain"); status.textContent = "顺序已保存"; renderFallback(); } catch (error) { status.textContent = `保存失败：${error.message}`; }
    });

    root.querySelectorAll("[data-settings-form]").forEach(async (form) => {
      const section = form.dataset.settingsForm;
      if (!editableSections.includes(section)) return;
      const endpoint = section.replaceAll("_", "-");
      const data = await api(`/api/settings/${endpoint}`);
      Object.entries(data.value || {}).forEach(([name, value]) => {
        const input = form.elements.namedItem(name); if (!input) return;
        if (input.type === "checkbox") input.checked = Boolean(value); else input.value = value;
      });
      form.addEventListener("input", () => markDirty(section));
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        const value = {};
        [...form.elements].forEach((input) => { if (!input.name) return; value[input.name] = input.type === "checkbox" ? input.checked : input.type === "number" ? Number(input.value) : input.value; });
        const status = form.querySelector("[data-form-state]");
        try { const saved = await api(`/api/settings/${endpoint}`, { method: "PUT", body: JSON.stringify(value) }); clearDirty(section); status.textContent = `已保存 v${saved.version}`; } catch (error) { status.textContent = `保存失败：${error.message}`; }
      });
    });

    await loadChannels();
  };

  document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll("[data-push-studio]").forEach(initPushStudio);
    document.querySelectorAll("[data-detail-caption-editor]").forEach(initDetailCaptionEditor);
    document.querySelectorAll("[data-log-toggle]").forEach(initDashboardLog);
    document.querySelectorAll("[data-library-selection]").forEach(initLibrarySelection);
    document.querySelectorAll("[data-task-center]").forEach(initTaskCenter);
    document.querySelectorAll("[data-task-detail]").forEach(initTaskDetail);
    document.querySelectorAll("[data-settings-app]").forEach((root) => initSettings(root).catch(() => {
      const warning = root.querySelector("[data-settings-warning]");
      if (warning) { warning.hidden = false; warning.textContent = "配置加载失败，请刷新重试。"; }
    }));
  });
})();
