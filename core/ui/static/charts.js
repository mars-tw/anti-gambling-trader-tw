"use strict";

(() => {
  const NS = "http://www.w3.org/2000/svg";
  const COLORS = Object.freeze({
    ink: "#172437",
    soft: "#69737d",
    grid: "#d9d2c3",
    teal: "#147d78",
    tealPale: "#dbeae5",
    tealMid: "#7db5ad",
    amber: "#b36a18",
    red: "#9f3a32",
    paper: "#fbf8f0",
  });
  const registrations = new WeakMap();
  let chartId = 0;

  function finite(value) {
    return typeof value === "number" && Number.isFinite(value) ? value : null;
  }

  function clearChildren(node) {
    while (node && node.firstChild) node.removeChild(node.firstChild);
  }

  function unregister(node) {
    const entry = node && registrations.get(node);
    if (!entry) return;
    entry.render = null;
    entry.lastWidth = 0;
    if (entry.frame !== null && typeof window !== "undefined" && typeof window.cancelAnimationFrame === "function") {
      window.cancelAnimationFrame(entry.frame);
    }
    entry.frame = null;
  }

  function clear(node) {
    unregister(node);
    clearChildren(node);
  }

  function svgElement(name, attributes = {}) {
    const node = document.createElementNS(NS, name);
    Object.entries(attributes).forEach(([key, value]) => node.setAttribute(key, String(value)));
    return node;
  }

  function addText(svg, text, attributes = {}) {
    const node = svgElement("text", attributes);
    node.textContent = String(text);
    svg.appendChild(node);
    return node;
  }

  function baseSvg(title, description, width, height) {
    const svg = svgElement("svg", {
      viewBox: `0 0 ${width} ${height}`,
      width: "100%",
      height,
      role: "img",
      focusable: "false",
      preserveAspectRatio: "none",
      "font-family": "Microsoft JhengHei, PingFang TC, Noto Sans TC, system-ui, sans-serif",
    });
    svg.style.height = `${height}px`;
    const titleId = `chart-title-${++chartId}`;
    const descId = `chart-desc-${chartId}`;
    svg.setAttribute("aria-labelledby", `${titleId} ${descId}`);
    const titleNode = svgElement("title", { id: titleId });
    titleNode.textContent = title;
    const descNode = svgElement("desc", { id: descId });
    descNode.textContent = description;
    svg.append(titleNode, descNode);
    return svg;
  }

  function chartWidth(container) {
    if (!container) return 320;
    const measured = Number(container.clientWidth) || Number(container.getBoundingClientRect && container.getBoundingClientRect().width);
    return measured > 0 ? Math.max(1, Math.round(measured)) : 320;
  }

  function ensureResponsive(container, render) {
    if (!container) return;
    let entry = registrations.get(container);
    if (!entry) {
      entry = { container, render: null, lastWidth: 0, frame: null, resizeObserver: null, mutationObserver: null };
      registrations.set(container, entry);
      if (typeof ResizeObserver === "function") {
        entry.resizeObserver = new ResizeObserver(() => {
          const width = chartWidth(container);
          if (width <= 0 || !entry.render || Math.abs(width - entry.lastWidth) < 1) return;
          entry.lastWidth = width;
          entry.render();
        });
        entry.resizeObserver.observe(container);
      }
      if (typeof MutationObserver === "function") {
        const panel = container.closest && (container.closest("[data-panel]") || container.parentElement);
        if (panel) {
          entry.mutationObserver = new MutationObserver(() => {
            if (!entry.render) return;
            entry.lastWidth = 0;
            const width = chartWidth(container);
            if (width > 0) {
              entry.lastWidth = width;
              entry.render();
            }
          });
          entry.mutationObserver.observe(panel, { attributes: true, attributeFilter: ["hidden", "class", "style"] });
        }
      }
    }
    entry.render = render;
    entry.lastWidth = 0;
    render();
  }

  function cleanCopy(value) {
    return String(value || "")
      .replace(/\bNone\b/g, "未提供")
      .replace(/\bnull\b/gi, "未提供")
      .replace(/\bR7\b/g, "分位數")
      .replace(/\bschema\b/gi, "資料格式")
      .replace(/\bholdout\b/gi, "前後段")
      .trim();
  }

  function message(container, text) {
    clear(container);
    const paragraph = document.createElement("p");
    paragraph.className = "chart-unavailable";
    paragraph.textContent = `無法繪製：${cleanCopy(text) || "資料不足"}`;
    container.appendChild(paragraph);
  }

  function clamp(value, low = 0, high = 1) {
    return Math.min(high, Math.max(low, Number.isFinite(value) ? value : 0.5));
  }

  function domain(values, includeZero = true) {
    const valid = values.filter((value) => finite(value) !== null);
    if (!valid.length) return [-1, 1];
    let low = Math.min(...valid);
    let high = Math.max(...valid);
    if (includeZero) {
      if (low > 0) low = 0;
      if (high < 0) high = 0;
    }
    if (low === high) {
      const magnitude = Math.max(Math.abs(low), 1);
      let pad = magnitude * 0.1;
      if (!Number.isFinite(pad)) pad = magnitude * 0.01;
      if (!Number.isFinite(pad) || pad === 0) pad = 1;
      const nextLow = low - pad;
      const nextHigh = high + pad;
      low = Number.isFinite(nextLow) ? nextLow : -Number.MAX_VALUE;
      high = Number.isFinite(nextHigh) ? nextHigh : Number.MAX_VALUE;
      if (low === high) return [-1, 1];
    }
    return [low, high];
  }

  function ratio(value, low, high) {
    const magnitude = Math.max(Math.abs(low), Math.abs(high), 1);
    const normalizedLow = low / magnitude;
    const normalizedHigh = high / magnitude;
    const normalizedValue = value / magnitude;
    const span = normalizedHigh - normalizedLow;
    if (!Number.isFinite(span) || span === 0 || !Number.isFinite(normalizedValue)) return 0.5;
    return clamp((normalizedValue - normalizedLow) / span);
  }

  function xScale(index, count, left, width) {
    if (count <= 1) return left + width / 2;
    return left + clamp(index / (count - 1)) * width;
  }

  function xValueScale(value, low, high, left, width) {
    return left + ratio(value, low, high) * width;
  }

  function yScale(value, low, high, top, height) {
    return top + (1 - ratio(value, low, high)) * height;
  }

  function formatNumber(value, digits = 2) {
    if (finite(value) === null) return "—";
    const absolute = Math.abs(value);
    if (absolute >= 1e9 || (absolute > 0 && absolute < 0.01)) return value.toExponential(2);
    try {
      return new Intl.NumberFormat("zh-TW", { maximumFractionDigits: digits }).format(value);
    } catch (_) {
      return String(value);
    }
  }

  function formatDate(value) {
    return value ? String(value).slice(0, 10) : "";
  }

  function tickValues(low, high, count = 5) {
    if (!Number.isFinite(low) || !Number.isFinite(high)) return [];
    const magnitude = Math.max(Math.abs(low), Math.abs(high), 1);
    const normalizedLow = low / magnitude;
    const normalizedHigh = high / magnitude;
    const values = [];
    const total = Math.max(2, count);
    for (let index = 0; index < total; index += 1) {
      const normalized = normalizedLow + (normalizedHigh - normalizedLow) * (index / (total - 1));
      const value = normalized * magnitude;
      if (Number.isFinite(value)) values.push(value);
    }
    if (low < 0 && high > 0) values.push(0);
    return values
      .filter((value, index, all) => all.findIndex((candidate) => Object.is(candidate, value)) === index)
      .sort((a, b) => a - b);
  }

  function layout(width, height, legendHeight = 0, bottom = 54) {
    const left = width < 340 ? 48 : 58;
    const right = width < 340 ? 12 : 18;
    const top = legendHeight + 14;
    return {
      left,
      right,
      top,
      bottom,
      width: Math.max(1, width - left - right),
      height: Math.max(1, height - top - bottom),
    };
  }

  function legendLayout(width, items) {
    const start = 8;
    const gap = 14;
    const itemWidths = items.map((item) => Math.max(66, 24 + String(item.label).length * 12));
    const positions = [];
    let x = start;
    let row = 0;
    items.forEach((item, index) => {
      const itemWidth = itemWidths[index];
      if (x > start && x + itemWidth > width - 8) {
        row += 1;
        x = start;
      }
      positions.push({ ...item, x, row });
      x += itemWidth + gap;
    });
    return { positions, height: (row + 1) * 20 };
  }

  function drawLegend(svg, legend) {
    legend.positions.forEach((item) => {
      const y = 13 + item.row * 20;
      if (item.kind === "fill") {
        svg.appendChild(svgElement("rect", { x: item.x, y: y - 8, width: 14, height: 10, fill: item.color, opacity: 0.8 }));
      } else {
        svg.appendChild(svgElement("line", {
          x1: item.x, y1: y - 3, x2: item.x + 14, y2: y - 3,
          stroke: item.color, "stroke-width": item.kind === "thick" ? 2.8 : 1.8,
          "stroke-dasharray": item.kind === "dash" ? "4 3" : "none",
        }));
      }
      addText(svg, item.label, { x: item.x + 20, y, fill: COLORS.ink, "font-size": 12 });
    });
  }

  function drawYAxis(svg, bounds, low, high, label, zeroColor = COLORS.red) {
    svg.appendChild(svgElement("line", {
      x1: bounds.left, y1: bounds.top, x2: bounds.left, y2: bounds.top + bounds.height,
      stroke: COLORS.ink, "stroke-width": 1,
    }));
    const ticks = tickValues(low, high);
    ticks.forEach((value) => {
      const y = yScale(value, low, high, bounds.top, bounds.height);
      const isZero = value === 0;
      svg.appendChild(svgElement("line", {
        x1: bounds.left, y1: y, x2: bounds.left + bounds.width, y2: y,
        stroke: isZero ? zeroColor : COLORS.grid,
        "stroke-width": isZero ? 1.4 : 1,
        "stroke-dasharray": isZero ? "none" : "2 4",
      }));
      svg.appendChild(svgElement("line", {
        x1: bounds.left - 4, y1: y, x2: bounds.left, y2: y,
        stroke: COLORS.ink, "stroke-width": 1,
      }));
      addText(svg, formatNumber(value), {
        x: bounds.left - 7, y: y + 4, "text-anchor": "end", fill: COLORS.soft, "font-size": 12,
      });
    });
    addText(svg, label, { x: bounds.left, y: Math.max(12, bounds.top - 5), fill: COLORS.soft, "font-size": 12 });
  }

  function drawXAxis(svg, bounds, values, labels, label, height) {
    svg.appendChild(svgElement("line", {
      x1: bounds.left, y1: bounds.top + bounds.height,
      x2: bounds.left + bounds.width, y2: bounds.top + bounds.height,
      stroke: COLORS.ink, "stroke-width": 1,
    }));
    values.forEach((value, index) => {
      const x = xValueScale(value, values[0], values[values.length - 1], bounds.left, bounds.width);
      svg.appendChild(svgElement("line", {
        x1: x, y1: bounds.top + bounds.height, x2: x, y2: bounds.top + bounds.height + 4,
        stroke: COLORS.ink, "stroke-width": 1,
      }));
      const anchor = index === 0 ? "start" : (index === values.length - 1 ? "end" : "middle");
      addText(svg, labels[index], {
        x, y: bounds.top + bounds.height + 17, "text-anchor": anchor, fill: COLORS.soft, "font-size": 12,
      });
    });
    addText(svg, label, {
      x: bounds.left + bounds.width / 2, y: height - 5, "text-anchor": "middle", fill: COLORS.soft, "font-size": 12,
    });
  }

  function historicalXLabels(rows, indexes) {
    return indexes.map((position) => {
      const actualIndex = rows[position] && Number.isInteger(rows[position].index)
        ? rows[position].index
        : position;
      return `第${actualIndex}筆`;
    });
  }

  function renderHistorical(container, historical, currency) {
    clear(container);
    if (!historical || !historical.available) {
      message(container, historical && historical.reason);
      return;
    }
    const rows = Array.isArray(historical.points) ? historical.points : [];
    const cumulative = rows.map((row) => finite(row.cum_pnl));
    const drawdown = rows.map((row) => finite(row.drawdown));
    if (!rows.length || cumulative.includes(null) || drawdown.includes(null)) {
      message(container, "曲線數值不完整");
      return;
    }
    const data = { rows, cumulative: cumulative.map(Number), drawdown: drawdown.map((value) => -Number(value)), currency };
    const draw = () => {
      clearChildren(container);
      const width = chartWidth(container);
      const height = 270;
      const legend = legendLayout(width, [
        { label: "累積損益", color: COLORS.teal, kind: "thick" },
        { label: "回撤", color: COLORS.amber, kind: "line" },
      ]);
      const bounds = layout(width, height, legend.height, 65);
      const [low, high] = domain(data.cumulative.concat(data.drawdown), true);
      const svg = baseSvg(
        "累積已實現淨損益與回撤金額",
        "青色為逐筆累積損益，琥珀色為負向回撤金額。圖上同時顯示金額、交易序號與日期端點；交易序號不是按日曆等距排列。",
        width,
        height,
      );
      drawLegend(svg, legend);
      drawYAxis(svg, bounds, low, high, `Y：金額${data.currency ? ` · ${data.currency}` : ""}`);
      const lastIndex = data.rows.length - 1;
      const indexes = Array.from(new Set([0, Math.floor(lastIndex / 2), lastIndex])).sort((a, b) => a - b);
      const xValues = indexes.map((index) => index);
      const xLabels = historicalXLabels(data.rows, indexes);
      drawXAxis(svg, bounds, xValues, xLabels, "X：交易序號（日期端點；非日曆等距）", height);
      const firstDate = formatDate(data.rows[0] && data.rows[0].exit_time);
      const lastDate = formatDate(data.rows[data.rows.length - 1] && data.rows[data.rows.length - 1].exit_time);
      if (firstDate) addText(svg, firstDate, { x: bounds.left, y: height - 22, fill: COLORS.soft, "font-size": 12 });
      if (lastDate) addText(svg, lastDate, { x: bounds.left + bounds.width, y: height - 22, "text-anchor": "end", fill: COLORS.soft, "font-size": 12 });
      svg.appendChild(svgElement("polyline", {
        points: data.cumulative.map((value, index) => `${xScale(index, data.cumulative.length, bounds.left, bounds.width).toFixed(2)},${yScale(value, low, high, bounds.top, bounds.height).toFixed(2)}`).join(" "),
        fill: "none", stroke: COLORS.teal, "stroke-width": 2.8, "vector-effect": "non-scaling-stroke",
      }));
      svg.appendChild(svgElement("polyline", {
        points: data.drawdown.map((value, index) => `${xScale(index, data.drawdown.length, bounds.left, bounds.width).toFixed(2)},${yScale(value, low, high, bounds.top, bounds.height).toFixed(2)}`).join(" "),
        fill: "none", stroke: COLORS.amber, "stroke-width": 2.2, "vector-effect": "non-scaling-stroke",
      }));
      const cut = historical.holdout_cut_index;
      if (Number.isInteger(cut) && cut > 0 && cut < data.rows.length) {
        const cutX = xScale(cut, data.rows.length, bounds.left, bounds.width);
        svg.appendChild(svgElement("line", {
          x1: cutX, y1: bounds.top, x2: cutX, y2: bounds.top + bounds.height,
          stroke: COLORS.amber, "stroke-width": 1.5, "stroke-dasharray": "5 4",
        }));
        addText(svg, "前後段切點", {
          x: clamp(cutX, bounds.left + 36, bounds.left + bounds.width - 36), y: bounds.top - 6,
          "text-anchor": "middle", fill: COLORS.amber, "font-size": 12,
        });
      }
      container.appendChild(svg);
    };
    ensureResponsive(container, draw);
  }

  function renderDistribution(container, distribution, currency) {
    clear(container);
    if (!distribution || !distribution.available) {
      message(container, distribution && distribution.reason);
      return;
    }
    const bins = Array.isArray(distribution.histogram) ? distribution.histogram : [];
    const summary = distribution.summary || {};
    const counts = bins.map((bin) => Number.isInteger(bin.count) && bin.count >= 0 ? bin.count : null);
    const boundsValues = bins.flatMap((bin) => [finite(bin.lower), finite(bin.upper)]);
    const keys = ["minimum", "p05", "q1", "median", "q3", "p95", "maximum"];
    const values = Object.fromEntries(keys.map((key) => [key, finite(summary[key])]));
    if (!bins.length || boundsValues.includes(null) || counts.includes(null)) {
      message(container, "直方圖資料不完整");
      return;
    }
    if (Object.values(values).includes(null)) {
      message(container, "分位數資料不完整");
      return;
    }
    const data = {
      bins,
      counts: counts.map(Number),
      minimum: Math.min(...boundsValues),
      maximum: Math.max(...boundsValues),
      values,
      currency,
    };
    if (data.minimum === data.maximum) {
      const expanded = domain([data.minimum], false);
      data.minimum = expanded[0];
      data.maximum = expanded[1];
    }
    const draw = () => {
      clearChildren(container);
      const width = chartWidth(container);
      const height = 300;
      const legend = legendLayout(width, [
        { label: "P05–P95", color: COLORS.amber, kind: "line" },
        { label: "Q1–Q3", color: COLORS.tealPale, kind: "fill" },
        { label: "中位數", color: COLORS.red, kind: "thick" },
      ]);
      const bounds = layout(width, height, legend.height, 68);
      const histogramHeight = Math.min(142, Math.max(104, bounds.height * 0.58));
      const histogramBounds = { ...bounds, height: histogramHeight };
      const boxY = bounds.top + histogramHeight + 42;
      const [xLow, xHigh] = domain([data.minimum, data.maximum], false);
      const maxCount = Math.max(...data.counts, 1);
      const svg = baseSvg(
        "每筆已實現淨損益直方圖與分位數箱圖",
        "柱狀圖顯示每筆損益的筆數與金額範圍；箱圖以 P05 到 P95、Q1 到 Q3 及中位數標記分布。",
        width,
        height,
      );
      drawLegend(svg, legend);
      drawYAxis(svg, histogramBounds, 0, maxCount, "Y：筆數", COLORS.red);
      const xTickValues = [xLow];
      if (xLow < 0 && xHigh > 0) xTickValues.push(0);
      xTickValues.push(xHigh);
      const uniqueX = xTickValues.filter((value, index, all) => all.indexOf(value) === index).sort((a, b) => a - b);
      drawXAxis(svg, histogramBounds, uniqueX, uniqueX.map((value) => formatNumber(value)), `X：單筆損益金額${data.currency ? ` · ${data.currency}` : ""}`, height);
      data.bins.forEach((bin, index) => {
        const lower = finite(bin.lower);
        const upper = finite(bin.upper);
        const x0 = xValueScale(lower, xLow, xHigh, bounds.left, bounds.width);
        const x1 = xValueScale(upper, xLow, xHigh, bounds.left, bounds.width);
        const barHeight = data.counts[index] / maxCount * histogramHeight;
        svg.appendChild(svgElement("rect", {
          x: Math.min(x0, x1) + 1,
          y: histogramBounds.top + histogramHeight - barHeight,
          width: Math.max(1, Math.abs(x1 - x0) - 2),
          height: Math.max(0, barHeight),
          fill: COLORS.teal,
          opacity: 0.78,
        }));
      });
      if (xLow <= 0 && xHigh >= 0) {
        const zeroX = xValueScale(0, xLow, xHigh, bounds.left, bounds.width);
        svg.appendChild(svgElement("line", {
          x1: zeroX, y1: histogramBounds.top, x2: zeroX, y2: histogramBounds.top + histogramHeight,
          stroke: COLORS.red, "stroke-width": 1.5,
        }));
      }
      const q1X = xValueScale(data.values.q1, xLow, xHigh, bounds.left, bounds.width);
      const q3X = xValueScale(data.values.q3, xLow, xHigh, bounds.left, bounds.width);
      svg.appendChild(svgElement("line", {
        x1: xValueScale(data.values.minimum, xLow, xHigh, bounds.left, bounds.width), y1: boxY,
        x2: xValueScale(data.values.maximum, xLow, xHigh, bounds.left, bounds.width), y2: boxY,
        stroke: COLORS.ink, "stroke-width": 1.4,
      }));
      svg.appendChild(svgElement("rect", {
        x: Math.min(q1X, q3X), y: boxY - 18, width: Math.max(1, Math.abs(q3X - q1X)), height: 36,
        fill: COLORS.tealPale, stroke: COLORS.teal,
      }));
      ["p05", "p95"].forEach((key) => {
        const x = xValueScale(data.values[key], xLow, xHigh, bounds.left, bounds.width);
        svg.appendChild(svgElement("line", { x1: x, y1: boxY - 13, x2: x, y2: boxY + 13, stroke: COLORS.amber, "stroke-width": 1.5 }));
      });
      const medianX = xValueScale(data.values.median, xLow, xHigh, bounds.left, bounds.width);
      svg.appendChild(svgElement("line", {
        x1: medianX, y1: boxY - 21, x2: medianX, y2: boxY + 21,
        stroke: COLORS.red, "stroke-width": 2.5,
      }));
      addText(svg, `Q1 ${formatNumber(data.values.q1)}`, { x: bounds.left, y: boxY + 34, fill: COLORS.soft, "font-size": 12 });
      addText(svg, `中位數 ${formatNumber(data.values.median)}`, { x: bounds.left + bounds.width / 2, y: boxY + 34, "text-anchor": "middle", fill: COLORS.red, "font-size": 12 });
      addText(svg, `Q3 ${formatNumber(data.values.q3)}`, { x: bounds.left + bounds.width, y: boxY + 34, "text-anchor": "end", fill: COLORS.soft, "font-size": 12 });
      container.appendChild(svg);
    };
    ensureResponsive(container, draw);
  }

  function renderHoldout(container, holdout, currency) {
    clear(container);
    if (!holdout || !holdout.available) {
      message(container, holdout && holdout.reason);
      return;
    }
    const front = holdout.in_sample || {};
    const back = holdout.out_sample || {};
    if (!front.amounts_available || !back.amounts_available) {
      message(container, front.amounts_reason || back.amounts_reason || "同幣別金額不可比較");
      return;
    }
    const values = [finite(front.mean), finite(front.median), finite(back.mean), finite(back.median)];
    if (values.includes(null)) {
      message(container, "前後段統計不完整");
      return;
    }
    const data = { values: values.map(Number), currency };
    const draw = () => {
      clearChildren(container);
      const width = chartWidth(container);
      const height = 280;
      const legend = legendLayout(width, [{ label: "前段", color: COLORS.teal, kind: "fill" }, { label: "後段", color: COLORS.amber, kind: "fill" }]);
      const svg = baseSvg(
        "前段與後段的平均及中位淨損益",
        "四條長條使用同一個正負金額刻度；青色為前段，琥珀色為後段，數字標籤顯示每條長條的實際金額。",
        width,
        height,
      );
      drawLegend(svg, legend);
      const plotLeft = Math.max(76, Math.min(120, Math.round(width * 0.3)));
      const valueRight = width - 8;
      const plotRight = Math.max(plotLeft + 70, valueRight - 78);
      const center = plotLeft + (plotRight - plotLeft) / 2;
      const maxAbs = Math.max(...data.values.map((value) => Math.abs(value)), 0) || 1;
      const labels = ["前段平均", "前段中位數", "後段平均", "後段中位數"];
      data.values.forEach((value, index) => {
        const y = legend.height + 30 + index * 43;
        const barWidth = Math.abs(value) / maxAbs * ((plotRight - plotLeft) / 2);
        const x = value >= 0 ? center : center - barWidth;
        addText(svg, labels[index], { x: 8, y: y + 15, fill: COLORS.ink, "font-size": 12 });
        svg.appendChild(svgElement("rect", {
          x, y, width: Math.max(barWidth, value === 0 ? 1 : 0), height: 22,
          fill: index < 2 ? COLORS.teal : COLORS.amber, opacity: 0.82,
        }));
        addText(svg, `${formatNumber(value)}${data.currency ? ` ${data.currency}` : ""}`, {
          x: valueRight, y: y + 15, "text-anchor": "end", fill: COLORS.ink, "font-size": 12,
        });
      });
      const axisY = legend.height + 30 + 4 * 43 - 7;
      svg.appendChild(svgElement("line", { x1: center, y1: legend.height + 20, x2: center, y2: axisY, stroke: COLORS.red, "stroke-width": 1.4 }));
      [ -maxAbs, 0, maxAbs ].forEach((value, index) => {
        const x = center + (value / maxAbs) * ((plotRight - plotLeft) / 2);
        addText(svg, formatNumber(value), {
          x, y: axisY + 18, "text-anchor": index === 0 ? "start" : (index === 2 ? "end" : "middle"), fill: COLORS.soft, "font-size": 12,
        });
      });
      addText(svg, `X：每筆金額（同一刻度）${data.currency ? ` · ${data.currency}` : ""}`, {
        x: (plotLeft + plotRight) / 2, y: height - 6, "text-anchor": "middle", fill: COLORS.soft, "font-size": 12,
      });
      container.appendChild(svg);
    };
    ensureResponsive(container, draw);
  }

  function renderHistogramSvg(bins, currency, containerWidth = 320) {
    if (!Array.isArray(bins) || !bins.length) return null;
    const counts = bins.map((bin) => Number.isInteger(bin.count) && bin.count >= 0 ? bin.count : null);
    const rawBounds = bins.flatMap((bin) => [finite(bin.lower), finite(bin.upper)]);
    if (rawBounds.includes(null) || counts.includes(null)) return null;
    const [minimum, maximum] = domain(rawBounds, true);
    const width = containerWidth;
    const height = 225;
    const legend = legendLayout(width, [{ label: "期末資金路徑數", color: COLORS.amber, kind: "fill" }]);
    const bounds = layout(width, height, legend.height, 58);
    const maxCount = Math.max(...counts, 1);
    const svg = baseSvg("期末資金分布", "資金情境在停止假設下的期末資金路徑數與金額範圍。", width, height);
    drawLegend(svg, legend);
    drawYAxis(svg, bounds, 0, maxCount, "Y：路徑數", COLORS.red);
    const xTicks = [minimum];
    if (minimum < 0 && maximum > 0) xTicks.push(0);
    xTicks.push(maximum);
    const uniqueTicks = xTicks.filter((value, index, all) => all.indexOf(value) === index).sort((a, b) => a - b);
    drawXAxis(svg, bounds, uniqueTicks, uniqueTicks.map((value) => formatNumber(value)), `X：期末資金${currency ? ` · ${currency}` : ""}`, height);
    bins.forEach((bin, index) => {
      const x0 = xValueScale(Number(bin.lower), minimum, maximum, bounds.left, bounds.width);
      const x1 = xValueScale(Number(bin.upper), minimum, maximum, bounds.left, bounds.width);
      const barHeight = counts[index] / maxCount * bounds.height;
      svg.appendChild(svgElement("rect", {
        x: Math.min(x0, x1) + 1,
        y: bounds.top + bounds.height - barHeight,
        width: Math.max(1, Math.abs(x1 - x0) - 2),
        height: Math.max(0, barHeight),
        fill: COLORS.amber,
        opacity: 0.78,
      }));
    });
    if (minimum <= 0 && maximum >= 0) {
      const zeroX = xValueScale(0, minimum, maximum, bounds.left, bounds.width);
      svg.appendChild(svgElement("line", { x1: zeroX, y1: bounds.top, x2: zeroX, y2: bounds.top + bounds.height, stroke: COLORS.red, "stroke-width": 1.4 }));
    }
    return svg;
  }

  function renderRisk(container, risk) {
    clear(container);
    const fan = risk && Array.isArray(risk.fan) ? risk.fan : [];
    if (!risk || !fan.length) {
      message(container, "尚未執行資金情境");
      return;
    }
    const keys = ["p05", "q1", "median", "q3", "p95"];
    const series = Object.fromEntries(keys.map((key) => [key, fan.map((point) => finite(point[key]))]));
    const threshold = finite(risk.threshold_amount);
    if (threshold === null || Object.values(series).some((values) => values.includes(null))) {
      message(container, "資金扇形數值不完整");
      return;
    }
    const data = { fan, series, threshold, risk };
    const draw = () => {
      clearChildren(container);
      const width = chartWidth(container);
      const height = 315;
      const legend = legendLayout(width, [
        { label: "P05–P95", color: COLORS.tealPale, kind: "fill" },
        { label: "Q1–Q3", color: COLORS.tealMid, kind: "fill" },
        { label: "扇形中位數", color: COLORS.teal, kind: "thick" },
        { label: "警戒線", color: COLORS.red, kind: "dash" },
      ]);
      const bounds = layout(width, height, legend.height, 60);
      const futureTrades = finite(risk.future_trades) !== null && risk.future_trades > 0 ? risk.future_trades : Math.max(1, fan.length - 1);
      const all = Object.values(data.series).flat().map(Number).concat([data.threshold]);
      const [low, high] = domain(all, true);
      const svg = baseSvg(
        "資金警戒線情境分位扇形",
        "淺色為 P05 到 P95，深色為 Q1 到 Q3，青線為扇形中位數，紅色虛線為嚴格跌破才算命中的資金警戒線。橫軸是未來交易筆數。",
        width,
        height,
      );
      drawLegend(svg, legend);
      drawYAxis(svg, bounds, low, high, `Y：資金${risk.currency ? ` · ${risk.currency}` : ""}`);
      const xValues = [0, futureTrades / 2, futureTrades].filter((value, index, allValues) => allValues.indexOf(value) === index);
      drawXAxis(svg, bounds, xValues, xValues.map((value) => formatNumber(value, 0)), "X：未來交易筆數", height);
      const coords = (key) => data.series[key].map((value, index) => {
        const tradeIndex = fan.length <= 1 ? 0 : index / (fan.length - 1) * futureTrades;
        return [xValueScale(tradeIndex, 0, futureTrades, bounds.left, bounds.width), yScale(value, low, high, bounds.top, bounds.height)];
      });
      const polygon = (pairs) => pairs.map(([x, y]) => `${Number.isFinite(x) ? x.toFixed(2) : bounds.left.toFixed(2)},${Number.isFinite(y) ? y.toFixed(2) : (bounds.top + bounds.height / 2).toFixed(2)}`).join(" ");
      svg.appendChild(svgElement("polygon", { points: polygon(coords("p05").concat(coords("p95").reverse())), fill: COLORS.tealPale, opacity: 0.82 }));
      svg.appendChild(svgElement("polygon", { points: polygon(coords("q1").concat(coords("q3").reverse())), fill: COLORS.tealMid, opacity: 0.7 }));
      svg.appendChild(svgElement("polyline", { points: polygon(coords("median")), fill: "none", stroke: COLORS.teal, "stroke-width": 2.8, "vector-effect": "non-scaling-stroke" }));
      const thresholdY = yScale(data.threshold, low, high, bounds.top, bounds.height);
      svg.appendChild(svgElement("line", {
        x1: bounds.left, y1: thresholdY, x2: bounds.left + bounds.width, y2: thresholdY,
        stroke: COLORS.red, "stroke-width": 1.5, "stroke-dasharray": "5 4",
      }));
      addText(svg, `警戒線 ${formatNumber(data.threshold)}${risk.currency ? ` ${risk.currency}` : ""}`, {
        x: bounds.left + 5, y: clamp(thresholdY - 6, bounds.top + 14, bounds.top + bounds.height - 5), fill: COLORS.red, "font-size": 12,
      });
      container.appendChild(svg);
      const terminal = renderHistogramSvg(risk.terminal_histogram, risk.currency || "", width);
      if (terminal) {
        terminal.style.marginTop = "12px";
        container.appendChild(terminal);
      }
    };
    ensureResponsive(container, draw);
  }

  window.EvidenceCharts = Object.freeze({
    clear,
    renderHistorical,
    renderDistribution,
    renderHoldout,
    renderRisk,
  });
})();
