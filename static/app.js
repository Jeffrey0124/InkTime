(() => {
  const clamp = (value, min, max) => Math.min(max, Math.max(min, value));

  const parseJson = (value, fallback = {}) => {
    try {
      const parsed = JSON.parse(value || "");
      return parsed && typeof parsed === "object" ? parsed : fallback;
    } catch {
      return fallback;
    }
  };

  const initPushStudio = (root) => {
    const stage = root.querySelector("[data-editor-stage]");
    const image = root.querySelector("[data-editor-image]");
    const captionInput = root.querySelector("[data-caption-input]");
    const captionPreview = root.querySelector("[data-caption-preview]");
    const captionBar = root.querySelector("[data-caption-bar]");
    const metaPreview = root.querySelector("[data-meta-preview]");
    const zoomInput = root.querySelector("[data-zoom-input]");
    const zoomValue = root.querySelector("[data-zoom-value]");
    const panX = root.querySelector("[data-pan-x]");
    const panY = root.querySelector("[data-pan-y]");
    const saveButton = root.querySelector("[data-save-button]");
    const saveState = root.querySelector("[data-save-state]");
    const resetButton = root.querySelector("[data-reset-button]");
    const fitButton = root.querySelector("[data-fit-button]");
    const captionToggle = root.querySelector("[data-toggle-caption]");
    const dateToggle = root.querySelector("[data-toggle-date]");
    const locationToggle = root.querySelector("[data-toggle-location]");
    if (!stage || !image || !captionInput || !zoomInput) return;

    const initialCrop = parseJson(root.dataset.crop, {});
    const initialRender = parseJson(root.dataset.render, {});
    const state = {
      x: Number(initialCrop.x || 0),
      y: Number(initialCrop.y || 0),
      scale: clamp(Number(initialCrop.scale || 1), 0.6, 2.8),
      dragging: false,
      dragStartX: 0,
      dragStartY: 0,
      originX: 0,
      originY: 0,
    };

    const sync = () => {
      image.style.setProperty("--pan-x", `${Math.round(state.x)}px`);
      image.style.setProperty("--pan-y", `${Math.round(state.y)}px`);
      image.style.setProperty("--zoom", state.scale.toFixed(3));
      zoomInput.value = state.scale.toFixed(2);
      if (zoomValue) zoomValue.textContent = state.scale.toFixed(2);
      if (panX) panX.textContent = String(Math.round(state.x));
      if (panY) panY.textContent = String(Math.round(state.y));
      if (captionPreview) captionPreview.textContent = captionInput.value.trim() || "未命名照片";
      if (captionBar && captionToggle) captionBar.hidden = !captionToggle.checked;
      if (metaPreview) {
        const parts = [];
        if (locationToggle?.checked && metaPreview.dataset.location) parts.push(metaPreview.dataset.location);
        if (dateToggle?.checked && metaPreview.dataset.date) parts.push(metaPreview.dataset.date);
        metaPreview.textContent = parts.join(" · ");
      }
    };

    metaPreview.dataset.location = root.dataset.location || metaPreview.textContent || "";
    metaPreview.dataset.date = root.dataset.date || "";

    stage.addEventListener("pointerdown", (event) => {
      if (event.button !== 0) return;
      state.dragging = true;
      state.dragStartX = event.clientX;
      state.dragStartY = event.clientY;
      state.originX = state.x;
      state.originY = state.y;
      stage.setPointerCapture(event.pointerId);
      stage.classList.add("dragging");
    });

    stage.addEventListener("pointermove", (event) => {
      if (!state.dragging) return;
      state.x = state.originX + event.clientX - state.dragStartX;
      state.y = state.originY + event.clientY - state.dragStartY;
      sync();
    });

    const endDrag = (event) => {
      if (!state.dragging) return;
      state.dragging = false;
      stage.classList.remove("dragging");
      if (stage.hasPointerCapture(event.pointerId)) stage.releasePointerCapture(event.pointerId);
    };
    stage.addEventListener("pointerup", endDrag);
    stage.addEventListener("pointercancel", endDrag);

    stage.addEventListener(
      "wheel",
      (event) => {
        event.preventDefault();
        const direction = event.deltaY > 0 ? -1 : 1;
        state.scale = clamp(state.scale + direction * 0.06, 0.6, 2.8);
        sync();
      },
      { passive: false },
    );

    zoomInput.addEventListener("input", () => {
      state.scale = clamp(Number(zoomInput.value || 1), 0.6, 2.8);
      sync();
    });

    captionInput.addEventListener("input", sync);
    captionToggle?.addEventListener("change", sync);
    dateToggle?.addEventListener("change", sync);
    locationToggle?.addEventListener("change", sync);

    resetButton?.addEventListener("click", () => {
      state.x = 0;
      state.y = 0;
      state.scale = 1;
      sync();
    });

    fitButton?.addEventListener("click", () => {
      state.x = 0;
      state.y = 0;
      state.scale = 1;
      sync();
    });

    saveButton?.addEventListener("click", async () => {
      const payload = {
        custom_side_caption: captionInput.value.trim(),
        manual_crop_json: {
          x: Math.round(state.x),
          y: Math.round(state.y),
          scale: Number(state.scale.toFixed(3)),
        },
        render_overrides_json: {
          show_caption: Boolean(captionToggle?.checked),
          show_date: Boolean(dateToggle?.checked),
          show_location: Boolean(locationToggle?.checked),
        },
      };
      saveButton.disabled = true;
      if (saveState) saveState.textContent = "保存中...";
      try {
        const response = await fetch(root.dataset.saveUrl, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok || !data.ok) {
          throw new Error(data.error || response.statusText);
        }
        if (saveState) saveState.textContent = "已保存到这张照片的人工覆盖参数。";
      } catch (error) {
        if (saveState) saveState.textContent = `保存失败：${error.message}`;
      } finally {
        saveButton.disabled = false;
      }
    });

    sync();
  };

  document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll("[data-push-studio]").forEach(initPushStudio);
  });
})();
