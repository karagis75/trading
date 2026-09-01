(() => {
  const openLink = document.getElementById("open-pinball-chart");
  const modal = document.getElementById("pinball-modal");
  const canvas = document.getElementById("pinball-canvas");
  const meta = document.getElementById("pinball-modal-meta");
  if (!openLink || !modal || !canvas) return;

  const symbol = openLink.dataset.symbol;
  let lastPayload = null;
  const EMA_STYLES = {
    ema9: { color: "#4d8fd6", label: "EMA9 stop" },
    ema18: { color: "#c9a227", label: "EMA18 stop" },
    ema20w: { color: "#e8eef5", label: "Weekly EMA20", dash: [5, 4] },
  };

  function setMeta(text) {
    if (meta) meta.textContent = text;
  }

  function openModal() {
    modal.hidden = false;
    modal.classList.add("is-open");
    modal.setAttribute("aria-hidden", "false");
    document.body.classList.add("modal-open");
    if (!lastPayload) {
      loadChart();
    } else {
      drawChart(lastPayload);
    }
  }

  function closeModal() {
    modal.hidden = true;
    modal.classList.remove("is-open");
    modal.setAttribute("aria-hidden", "true");
    document.body.classList.remove("modal-open");
  }

  function metaText(payload) {
    const regime = payload.regime || {};
    const stops = payload.stops || {};
    const wave = payload.wave;
    const side = regime.side ? regime.side[0].toUpperCase() + regime.side.slice(1) : "";
    const stopBit =
      stops.ema9 != null && stops.ema18 != null
        ? ` · stops EMA9 ${stops.ema9} / EMA18 ${stops.ema18}`
        : "";
    const weekly =
      regime.weekly_ema20 != null ? ` · weekly EMA20 ${regime.weekly_ema20}` : "";
    if (wave) {
      return `${side} ${wave["Wave Position"]} · confidence ${wave.Confidence}${weekly}${stopBit}`;
    }
    return (payload.error || `${payload.bars.length} cached bars`) + stopBit;
  }

  async function loadChart() {
    setMeta("Loading cached Yahoo bars…");
    try {
      const response = await fetch(`/api/stocks/${encodeURIComponent(symbol)}/pinball-chart`);
      const payload = await response.json();
      lastPayload = payload;
      if (!payload.bars || payload.bars.length === 0) {
        const db = payload.database ? ` Database: ${payload.database}.` : "";
        setMeta((payload.error || "No cached bars.") + db);
        clearCanvas();
        return;
      }
      setMeta(metaText(payload));
      drawChart(payload);
    } catch (error) {
      setMeta("Could not load the pinball chart.");
      clearCanvas();
    }
  }

  function clearCanvas() {
    const ctx = canvas.getContext("2d");
    ctx.clearRect(0, 0, canvas.width, canvas.height);
  }

  function drawEma(ctx, bars, key, xAt, yAt) {
    const style = EMA_STYLES[key];
    const points = bars
      .map((bar, index) => (bar[key] == null ? null : { x: xAt(index), y: yAt(bar[key]) }))
      .filter(Boolean);
    if (points.length < 2) return;
    ctx.beginPath();
    ctx.strokeStyle = style.color;
    ctx.lineWidth = key === "ema18" ? 2 : 1.5;
    ctx.setLineDash(style.dash || []);
    points.forEach((point, index) => {
      if (index === 0) ctx.moveTo(point.x, point.y);
      else ctx.lineTo(point.x, point.y);
    });
    ctx.stroke();
    ctx.setLineDash([]);
  }

  function drawChart(payload) {
    const bars = payload.bars || [];
    if (!bars.length) return;
    const wrap = canvas.parentElement;
    const cssWidth = Math.max(640, wrap ? wrap.clientWidth : 920);
    const cssHeight = 500;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.floor(cssWidth * dpr);
    canvas.height = Math.floor(cssHeight * dpr);
    canvas.style.width = `${cssWidth}px`;
    canvas.style.height = `${cssHeight}px`;
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    const pad = { top: 18, right: 86, bottom: 52, left: 16 };
    const width = cssWidth - pad.left - pad.right;
    const height = cssHeight - pad.top - pad.bottom;
    const highs = bars.map((bar) => bar.high);
    const lows = bars.map((bar) => bar.low);
    (payload.levels || []).forEach((level) => highs.push(level.price));
    (payload.markers || []).forEach((marker) => highs.push(marker.price));
    bars.forEach((bar) => {
      ["ema9", "ema18", "ema20w"].forEach((key) => {
        if (bar[key] != null) {
          highs.push(bar[key]);
          lows.push(bar[key]);
        }
      });
    });
    const min = Math.min(...lows);
    const max = Math.max(...highs);
    const span = max - min || 1;
    const slot = width / bars.length;
    const xAt = (index) => pad.left + slot * (index + 0.5);
    const yAt = (price) => pad.top + ((max - price) / span) * height;

    ctx.fillStyle = "#171d25";
    ctx.fillRect(0, 0, cssWidth, cssHeight);

    ctx.strokeStyle = "#2a3441";
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(pad.left, pad.top);
    ctx.lineTo(pad.left, pad.top + height);
    ctx.lineTo(pad.left + width, pad.top + height);
    ctx.stroke();

    (payload.levels || []).forEach((level) => {
      const y = yAt(level.price);
      ctx.strokeStyle = level.label === "1.618" || level.label === "1.000" ? "#2f9e8a" : "#3a4656";
      ctx.setLineDash([4, 4]);
      ctx.beginPath();
      ctx.moveTo(pad.left, y);
      ctx.lineTo(pad.left + width, y);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = "#8b9aab";
      ctx.font = "11px ui-monospace, Consolas, monospace";
      ctx.textAlign = "left";
      ctx.fillText(`${level.label}  ${Number(level.price).toFixed(2)}`, pad.left + width + 6, y + 4);
    });

    bars.forEach((bar, index) => {
      const x = xAt(index);
      const openY = yAt(bar.open);
      const closeY = yAt(bar.close);
      const highY = yAt(bar.high);
      const lowY = yAt(bar.low);
      const up = bar.close >= bar.open;
      ctx.strokeStyle = up ? "#3fa36c" : "#d4676a";
      ctx.fillStyle = up ? "#3fa36c" : "#d4676a";
      ctx.beginPath();
      ctx.moveTo(x, highY);
      ctx.lineTo(x, lowY);
      ctx.stroke();
      const bodyTop = Math.min(openY, closeY);
      const bodyH = Math.max(1, Math.abs(closeY - openY));
      ctx.fillRect(x - Math.max(1, slot * 0.28), bodyTop, Math.max(2, slot * 0.56), bodyH);
    });

    drawEma(ctx, bars, "ema20w", xAt, yAt);
    drawEma(ctx, bars, "ema18", xAt, yAt);
    drawEma(ctx, bars, "ema9", xAt, yAt);

    const last = bars[bars.length - 1];
    [
      ["ema9", last.ema9],
      ["ema18", last.ema18],
    ].forEach(([key, value]) => {
      if (value == null) return;
      ctx.fillStyle = EMA_STYLES[key].color;
      ctx.font = "11px ui-monospace, Consolas, monospace";
      ctx.textAlign = "left";
      ctx.fillText(`${key === "ema9" ? "9" : "18"}  ${Number(value).toFixed(2)}`, pad.left + width + 6, yAt(value) + 4);
    });

    const dateIndex = new Map(bars.map((bar, index) => [bar.date, index]));
    (payload.markers || []).forEach((marker) => {
      const index = dateIndex.get(marker.date);
      if (index === undefined) return;
      const x = xAt(index);
      const y = yAt(marker.price);
      ctx.fillStyle = "#e8eef5";
      ctx.beginPath();
      ctx.arc(x, y, 4, 0, Math.PI * 2);
      ctx.fill();
      ctx.fillStyle = "#e8eef5";
      ctx.font = "bold 12px Segoe UI, sans-serif";
      ctx.textAlign = "center";
      ctx.fillText(marker.label, x, y - 8);
    });

    ctx.fillStyle = "#8b9aab";
    ctx.font = "11px ui-monospace, Consolas, monospace";
    ctx.textAlign = "left";
    ctx.fillText(bars[0].date, pad.left, cssHeight - 28);
    ctx.textAlign = "right";
    ctx.fillText(bars[bars.length - 1].date, pad.left + width, cssHeight - 28);

    let legendX = pad.left;
    const legendY = cssHeight - 12;
    Object.values(EMA_STYLES).forEach((style) => {
      ctx.strokeStyle = style.color;
      ctx.lineWidth = 2;
      ctx.setLineDash(style.dash || []);
      ctx.beginPath();
      ctx.moveTo(legendX, legendY - 3);
      ctx.lineTo(legendX + 16, legendY - 3);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = style.color;
      ctx.font = "11px Segoe UI, sans-serif";
      ctx.textAlign = "left";
      ctx.fillText(style.label, legendX + 20, legendY);
      legendX += 128;
    });
  }

  openLink.addEventListener("click", (event) => {
    event.preventDefault();
    openModal();
  });
  modal.querySelectorAll("[data-close-pinball]").forEach((node) => {
    node.addEventListener("click", closeModal);
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !modal.hidden) closeModal();
  });
  window.addEventListener("resize", () => {
    if (!modal.hidden && lastPayload) drawChart(lastPayload);
  });
})();
