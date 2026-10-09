"use strict";

(() => {
  const ui = window.AntiGamblingUI;
  if (!ui) return;

  const SVG_NS = "http://www.w3.org/2000/svg";
  const AUTO_REFRESH_MS = 30000;
  const POLL_MS = 350;
  const MAX_POLL_ATTEMPTS = 150;
  const MAX_VISIBLE_CANDLES = 400;
  const MAX_VISIBLE_ROWS = 500;
  const PROVIDER_LABELS = Object.freeze({
    pionex: "Pionex 現貨",
    binance: "Binance 現貨",
    shioaji: "Shioaji 台股／期貨",
  });
  const MARKET_LABELS = Object.freeze({
    spot: "現貨",
    stock: "台股",
    futures: "台灣期貨",
  });
  const KIND_LABELS = Object.freeze({
    candles: "K 線行情",
    fills: "成交明細",
    account: "帳戶摘要",
    realizations: "已實現明細",
  });
  const INTERVAL_LABELS = Object.freeze({
    "1m": "1 分鐘",
    "5m": "5 分鐘",
    "15m": "15 分鐘",
    "30m": "30 分鐘",
    "1h": "1 小時",
    "4h": "4 小時",
    "1d": "1 日",
  });
  const FIELD_LABELS = Object.freeze({
    id: "成交編號",
    order_id: "委託編號",
    side: "買賣方向",
    time_ms: "時間",
    symbol: "標的",
    market: "市場",
    base_asset: "基礎資產",
    quote_asset: "計價資產",
    quantity: "數量",
    quote_amount: "成交金額",
    price: "成交價",
    fee_amount: "手續費",
    fee_asset: "手續費幣別",
    open: "開盤價",
    high: "最高價",
    low: "最低價",
    close: "收盤價",
    volume: "成交量",
    is_closed: "K 線狀態",
    date: "日期",
    time_precision: "時間精度",
    pnl: "已實現損益",
    fee: "手續費",
    tax: "交易稅",
    entry_price: "進場價",
    exit_price: "出場價",
    detail_id: "明細編號",
    details: "交易明細",
    asset: "資產",
    free: "可用餘額",
    locked: "凍結餘額",
    unit: "單位",
    cost_known: "成本資料",
    ref: "帳戶參照",
    label: "顯示名稱",
    section: "資料類別",
  });
  const COVERAGE_REASON_LABELS = Object.freeze({
    page_cap: "已達本次讀取的安全頁數上限",
    request_cap: "已達本次讀取的安全請求上限",
    max_pages: "已達本次讀取的安全頁數上限",
    max_requests: "已達本次讀取的安全請求上限",
    time_limit: "讀取時間已達安全期限",
    deadline: "讀取時間已達安全期限",
    truncated: "資料來源只回傳部分資料",
    source_truncated: "資料來源只回傳部分資料",
    boundary_incomplete: "查詢期間的邊界尚未完整確認",
    window_incomplete: "查詢期間尚未完整覆蓋",
    saturated: "資料來源單次回傳已達上限",
    missing_bars: "部分 K 線時間點缺少資料",
    duplicate_conflict: "發現互相衝突的重複資料列",
    detail_cap: "明細讀取已達安全上限",
    provider_limit: "資料來源限制了這次讀取",
  });

  const byId = (id) => document.getElementById(id);
  let active = false;
  let closed = false;
  let currentJobId = null;
  let currentJobStatus = null;
  let currentDataset = null;
  let currentQuery = null;
  let pollTimer = null;
  let refreshTimer = null;
  let pollNonce = 0;
  let requestInFlight = false;
  let suppressInvalidation = false;
  let dataApiCapabilities = null;
  let credentialPanelAutoOpened = false;
  let selectedProvider = null;

  function setText(node, value) {
    if (node) node.textContent = value == null ? "" : String(value);
  }

  function clearNode(node) {
    while (node && node.firstChild) node.removeChild(node.firstChild);
  }

  function providerLabel(value) {
    const key = String(value || "").toLowerCase();
    return PROVIDER_LABELS[key] || "未辨識資料來源";
  }

  function marketLabel(value) {
    const key = String(value || "").toLowerCase();
    return MARKET_LABELS[key] || "未提供市場";
  }

  function kindLabel(value) {
    const key = String(value || "").toLowerCase();
    return KIND_LABELS[key] || "未辨識資料種類";
  }

  function intervalLabel(value) {
    const key = String(value || "").toLowerCase();
    return INTERVAL_LABELS[key] || "未提供週期";
  }

  function fieldLabel(value) {
    const key = String(value || "");
    return FIELD_LABELS[key] || `其他資料（${key}）`;
  }

  function formatCount(value) {
    const number = Number(value);
    return Number.isFinite(number) ? number.toLocaleString("zh-TW") : "—";
  }

  function formatTime(value, timezone = "UTC") {
    if (value == null || value === "") return "未提供";
    let date;
    if (typeof value === "number") date = new Date(value);
    else if (/^[0-9]+$/.test(String(value))) date = new Date(Number(value));
    else date = new Date(String(value));
    if (!Number.isFinite(date.getTime())) return "無法辨識";
    try {
      const zone = timezone === "Asia/Taipei" ? "Asia/Taipei" : "UTC";
      const rendered = new Intl.DateTimeFormat("zh-TW", {
        timeZone: zone,
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
        hour12: false,
      }).format(date);
      return `${rendered}（${zone === "Asia/Taipei" ? "台北時間" : "UTC"}）`;
    } catch (_) {
      return date.toISOString();
    }
  }

  function appendDefinition(list, label, value) {
    const wrapper = document.createElement("div");
    const term = document.createElement("dt");
    const detail = document.createElement("dd");
    setText(term, label);
    setText(detail, value);
    wrapper.append(term, detail);
    list.appendChild(wrapper);
  }

  function stopTimers() {
    pollNonce += 1;
    if (pollTimer !== null) window.clearTimeout(pollTimer);
    if (refreshTimer !== null) window.clearTimeout(refreshTimer);
    pollTimer = null;
    refreshTimer = null;
    requestInFlight = false;
  }

  function isPublicCandleRequest() {
    return Boolean(
      currentQuery && currentQuery.kind === "candles" &&
      (currentQuery.provider === "pionex" || currentQuery.provider === "binance")
    );
  }

  function scheduleRefresh() {
    if (refreshTimer !== null) window.clearTimeout(refreshTimer);
    refreshTimer = null;
    if (!active || closed || !currentDataset || !isPublicCandleRequest()) return;
    const captured = currentDataset.captured_at
      ? `上次成功：${formatTime(currentDataset.captured_at, currentDataset.timezone)}`
      : "尚無成功讀取時間";
    setText(byId("data-refresh-status"), `${captured}；留在本頁時 30 秒後自動刷新。`);
    refreshTimer = window.setTimeout(() => {
      refreshTimer = null;
      if (active && !closed && !requestInFlight && currentJobStatus !== "running") {
        startSync(true);
      }
    }, AUTO_REFRESH_MS);
  }

  function setJobBusy(busy) {
    requestInFlight = Boolean(busy);
    const sync = byId("data-sync");
    const cancel = byId("data-cancel");
    if (sync) sync.disabled = busy || closed;
    if (cancel) {
      cancel.hidden = !busy;
      cancel.disabled = !busy || closed || currentJobStatus === "cancel_requested";
    }
    ["data-export-json", "data-export-csv", "data-analyze"].forEach((id) => {
      const button = byId(id);
      if (button) button.disabled = busy || closed || !currentDataset;
    });
  }

  function clearRenderedDataset(reason) {
    currentDataset = null;
    currentJobId = null;
    currentJobStatus = null;
    currentQuery = null;
    if (refreshTimer !== null) window.clearTimeout(refreshTimer);
    refreshTimer = null;
    const result = byId("data-result");
    if (result) result.hidden = true;
    clearNode(byId("data-candle-chart"));
    clearNode(byId("data-table-head"));
    clearNode(byId("data-table-body"));
    clearNode(byId("data-account-groups"));
    setText(byId("data-analysis-status"), "");
    if (reason) setText(byId("data-job-status"), reason);
    setJobBusy(false);
  }

  function invalidateForInput(reason) {
    if (suppressInvalidation || closed) return;
    const running = currentJobStatus === "running" || currentJobStatus === "cancel_requested";
    const jobId = currentJobId;
    stopTimers();
    if (running && jobId) {
      ui.post("/api/data-cancel", { job_id: jobId }).catch(() => {});
    }
    clearRenderedDataset(reason || "設定已變更；舊資料集與分析已失效。");
    ui.invalidateAnalysis("API 設定或輸入已變更；舊分析與匯出綁定已失效。");
  }

  function updateSDKHint() {
    const hint = byId("data-sdk-hint");
    if (!hint) return;
    const provider = byId("data-provider").value;
    if (provider !== "shioaji") {
      hint.hidden = true;
      setText(hint, "");
      return;
    }
    hint.hidden = false;
    const sdk = dataApiCapabilities && dataApiCapabilities.shioaji_sdk;
    if (!sdk || typeof sdk.available !== "boolean") {
      setText(hint, "正在確認本機 Shioaji SDK 狀態。Windows 可攜版通常已隨附 1.7.7 SDK；從原始碼執行時會依此安裝環境顯示提示。");
      return;
    }
    if (sdk.available) {
      const version = typeof sdk.version === "string" && sdk.version ? ` ${sdk.version}` : "";
      setText(hint, `此安裝已偵測到 Shioaji SDK${version}；仍須在本機設定唯讀憑證後才能讀取資料。`);
      return;
    }
    setText(hint, "此原始碼執行環境未偵測到 Shioaji SDK；只有這種情況才需要安裝相容版本。公開加密 K 線不受影響。");
  }

  function updateCapabilities(state) {
    const dataApi = state && state.data_api;
    dataApiCapabilities = dataApi && dataApi.capabilities && typeof dataApi.capabilities === "object"
      ? dataApi.capabilities : null;
    updateSDKHint();
  }

  function updateFormForProvider() {
    const provider = byId("data-provider").value;
    const kind = byId("data-kind").value;
    const market = byId("data-market");
    const symbol = byId("data-symbol");
    const interval = byId("data-interval");
    const start = byId("data-start");
    const end = byId("data-end");
    const limit = byId("data-limit");
    const account = byId("data-account-ref");
    const isShioaji = provider === "shioaji";
    const isCandles = kind === "candles";
    const isAccount = kind === "account";
    const credentialPanel = byId("data-credentials-panel");
    const credentialProvider = byId("credential-provider");
    if (selectedProvider !== null && selectedProvider !== provider) {
      byId("credential-key").value = "";
      byId("credential-secret").value = "";
    }
    selectedProvider = provider;
    credentialProvider.value = provider;
    credentialProvider.disabled = true;

    suppressInvalidation = true;
    try {
      if (isShioaji) {
        if (market.value === "spot") market.value = "stock";
        if (!symbol.value || /[_-]/.test(symbol.value)) symbol.value = "2330";
      } else {
        market.value = "spot";
        if (!symbol.value || /^[0-9]{3,6}$/.test(symbol.value)) {
          symbol.value = provider === "binance" ? "BTCUSDT" : "BTC_USDT";
        } else if (provider === "binance") {
          symbol.value = symbol.value.replace(/_/g, "");
        } else if (provider === "pionex" && !symbol.value.includes("_")) {
          symbol.value = symbol.value.replace(/USDT$/i, "_USDT");
        }
      }
      market.disabled = !isShioaji;
      symbol.disabled = isAccount;
      interval.disabled = !isCandles;
      start.disabled = isAccount;
      end.disabled = isAccount;
      limit.disabled = isAccount;
      account.disabled = !isShioaji;
      limit.max = kind === "fills" ? "5000" : "2000";
      if (Number(limit.value) > Number(limit.max)) limit.value = limit.max;
      byId("crypto-analysis-confirmations").hidden = isShioaji;
      byId("shioaji-analysis-confirmations").hidden = !isShioaji;
      if (credentialPanel && (isShioaji || !isCandles) && !credentialPanel.open) {
        credentialPanel.open = true;
        credentialPanelAutoOpened = true;
      } else if (credentialPanel && !isShioaji && isCandles && credentialPanelAutoOpened) {
        credentialPanel.open = false;
        credentialPanelAutoOpened = false;
      }
      setText(
        byId("data-query-hint"),
        isAccount
          ? "帳戶摘要只列原始幣別餘額或持倉單位，不把現金與持倉猜成總淨值。"
          : (isShioaji
            ? "Shioaji 需要 1.7.7 SDK 與本機唯讀憑證；多個符合帳戶時必須明確選擇。"
            : (kind === "candles" ? "公開 K 線不需要憑證；私有成交與帳戶資料需要本機設定。" : "私有資料需要本機設定；成功只代表本次唯讀查詢可用。"))
      );
    } finally {
      suppressInvalidation = false;
    }
    updateSDKHint();
  }

  function epochFromInput(id) {
    const value = byId(id).value;
    if (!value) return null;
    const timestamp = new Date(value).getTime();
    if (!Number.isFinite(timestamp)) throw new Error("開始或結束時間格式不正確。");
    return Math.trunc(timestamp);
  }

  function buildRequest() {
    const provider = byId("data-provider").value;
    const kind = byId("data-kind").value;
    const profile = String(byId("credential-profile").value || "default").trim().toLowerCase();
    const query = { market: byId("data-market").value };
    if (kind !== "account") {
      const symbol = String(byId("data-symbol").value || "").trim().toUpperCase();
      if (!symbol) throw new Error("請填標的代號。");
      query.symbol = symbol;
      const startMs = epochFromInput("data-start");
      const endMs = epochFromInput("data-end");
      if (startMs !== null) query.start_ms = startMs;
      if (endMs !== null) query.end_ms = endMs;
      const limit = Number(byId("data-limit").value);
      if (!Number.isInteger(limit) || limit < 1) throw new Error("筆數上限必須是正整數。");
      query.limit = limit;
      if (kind === "candles") query.interval = byId("data-interval").value;
    }
    const accountRef = byId("data-account-ref").value;
    if (accountRef) query.account_ref = accountRef;
    return { provider, kind, profile, query };
  }

  function renderCoverageReasons(coverage) {
    const list = byId("data-coverage-reasons");
    clearNode(list);
    const reasons = coverage && Array.isArray(coverage.reasons) ? coverage.reasons : [];
    if (!reasons.length && coverage && coverage.complete === true) {
      const item = document.createElement("li");
      setText(item, "供應者回報此有界視窗已完成；空視窗只代表沒有回傳紀錄，不代表帳戶沒有其他交易。");
      list.appendChild(item);
      return;
    }
    reasons.forEach((reason) => {
      const item = document.createElement("li");
      const raw = typeof reason === "string"
        ? reason
        : (reason && typeof reason === "object" && typeof reason.code === "string" ? reason.code : "");
      const code = raw.trim().toLowerCase();
      const suppliedHumanText = /[\u3400-\u9fff]/.test(raw);
      const label = COVERAGE_REASON_LABELS[code]
        || (suppliedHumanText ? raw : "資料涵蓋有限，請查看技術細節");
      const labelNode = document.createElement("span");
      setText(labelNode, label);
      item.appendChild(labelNode);
      if (raw && label !== raw) {
        const details = document.createElement("details");
        const summary = document.createElement("summary");
        const detail = document.createElement("code");
        setText(summary, "技術細節");
        setText(detail, raw);
        details.append(summary, detail);
        item.appendChild(details);
      }
      list.appendChild(item);
    });
  }

  function renderFacts(dataset) {
    const list = byId("data-facts");
    clearNode(list);
    const requested = dataset.requested || {};
    const coverage = dataset.coverage || {};
    const rows = Array.isArray(dataset.rows) ? dataset.rows : [];
    appendDefinition(list, "資料來源", providerLabel(dataset.provider));
    appendDefinition(list, "範圍", `${marketLabel(dataset.market)} · ${dataset.symbol || "未指定標的"} · ${kindLabel(dataset.kind)}${requested.interval ? ` · ${intervalLabel(requested.interval)}` : ""}`);
    appendDefinition(list, "要求期間", `${formatTime(requested.start_ms, dataset.timezone)} ～ ${formatTime(requested.end_ms, dataset.timezone)}`);
    appendDefinition(list, "實際期間", `${formatTime(coverage.actual_start_ms, dataset.timezone)} ～ ${formatTime(coverage.actual_end_ms, dataset.timezone)}`);
    appendDefinition(list, "回傳筆數", `${formatCount(rows.length)}（原始 ${formatCount(coverage.raw_count)}）`);
    appendDefinition(list, "涵蓋狀態", coverage.complete === true ? "有界視窗完成" : "不完整／需看原因");
    appendDefinition(list, "截斷", coverage.truncated === true ? "是；不可當成完整紀錄" : "否");
    appendDefinition(list, "重複／拒絕", `${formatCount(coverage.duplicate_rows)}／${formatCount(coverage.rejected_rows)}`);
    appendDefinition(list, "最新資料時間", rows.length ? formatTime(rows[rows.length - 1].time_ms || rows[rows.length - 1].date, dataset.timezone) : "沒有回傳紀錄");
    appendDefinition(list, "最後成功同步", formatTime(dataset.captured_at, dataset.timezone));
    const completion = byId("data-completion");
    setText(completion, coverage.complete === true ? "視窗完成" : "涵蓋不完整");
    completion.className = `status-pill ${coverage.complete === true ? "ok" : "warn"}`;
    renderCoverageReasons(coverage);
  }

  function numberValue(value) {
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
  }

  function svgElement(name, attributes = {}) {
    const node = document.createElementNS(SVG_NS, name);
    Object.entries(attributes).forEach(([key, value]) => node.setAttribute(key, String(value)));
    return node;
  }

  function movingAverage(rows, period) {
    const output = [];
    let sum = 0;
    const values = rows.map((row) => numberValue(row.close));
    values.forEach((value, index) => {
      if (value === null) {
        output.push(null);
        return;
      }
      sum += value;
      if (index >= period) {
        const old = values[index - period];
        if (old !== null) sum -= old;
      }
      output.push(index >= period - 1 ? sum / period : null);
    });
    return output;
  }

  function renderCandles(dataset) {
    const card = byId("data-chart-card");
    const container = byId("data-candle-chart");
    clearNode(container);
    const allRows = Array.isArray(dataset.rows) ? dataset.rows : [];
    const isCandles = dataset.kind === "candles";
    const isFills = dataset.kind === "fills";
    const valid = isCandles
      ? allRows.filter((row) => [row.open, row.high, row.low, row.close, row.volume].every((value) => numberValue(value) !== null))
      : (isFills ? allRows.filter((row) => numberValue(row.time_ms) !== null && numberValue(row.price) !== null && ["BUY", "SELL"].includes(String(row.side || "").toUpperCase())) : []);
    if ((!isCandles && !isFills) || !valid.length) {
      card.hidden = true;
      return;
    }
    card.hidden = false;
    const maxRows = isFills ? 400 : MAX_VISIBLE_CANDLES;
    const rows = valid.slice(-maxRows);
    const width = Math.max(280, Math.round(container.clientWidth || 860));
    const height = Math.max(320, Math.round(width * 0.52));
    const left = 62;
    const right = 18;
    const top = 26;
    const priceBottom = isCandles ? Math.round(height * 0.73) : height - 52;
    const volumeTop = Math.round(height * 0.79);
    const volumeBottom = height - 18;
    const plotWidth = width - left - right;
    const prices = isCandles ? rows.flatMap((row) => [Number(row.high), Number(row.low)]) : rows.map((row) => Number(row.price));
    const times = rows.map((row) => Number(row.time_ms));
    const firstTime = Math.min(...times);
    const lastTime = Math.max(...times);
    const timeSpan = Math.max(1, lastTime - firstTime);
    let priceMax = Math.max(...prices);
    let priceMin = Math.min(...prices);
    if (priceMax === priceMin) {
      priceMax += 1;
      priceMin -= 1;
    }
    const volumeMax = Math.max(1, ...rows.map((row) => Math.max(0, Number(row.volume || 0))));
    const x = (index) => isFills ? left + (times[index] - firstTime) * plotWidth / timeSpan : left + (index + 0.5) * plotWidth / rows.length;
    const y = (price) => top + (priceMax - price) / (priceMax - priceMin) * (priceBottom - top);
    const svg = svgElement("svg", { viewBox: `0 0 ${width} ${height}`, width, height, role: "img", "aria-labelledby": "api-chart-title api-chart-desc" });
    const title = svgElement("title", { id: "api-chart-title" });
    setText(title, isFills ? `${dataset.symbol || "標的"} 成交價格與方向` : `${dataset.symbol || "標的"} K 線圖`);
    const desc = svgElement("desc", { id: "api-chart-desc" });
    setText(desc, isFills ? `顯示最新 ${rows.length} 筆實際成交價格與 BUY／SELL 方向；完整資料請下載。` : `顯示最新 ${rows.length} 根 K 線、成交量、SMA 10 與 SMA 20；完整資料請下載。`);
    svg.append(title, desc);

    for (let tick = 0; tick <= 4; tick += 1) {
      const tickY = top + (priceBottom - top) * tick / 4;
      svg.appendChild(svgElement("line", { x1: left, y1: tickY, x2: width - right, y2: tickY, class: "api-grid-line" }));
      const label = svgElement("text", { x: left - 8, y: tickY + 4, class: "api-axis-label", "text-anchor": "end" });
      setText(label, (priceMax - (priceMax - priceMin) * tick / 4).toPrecision(6));
      svg.appendChild(label);
    }
    const timeTicks = isFills ? 3 : 0;
    for (let tick = 0; tick < timeTicks; tick += 1) {
      const fraction = tick / (timeTicks - 1);
      const timeValue = firstTime + timeSpan * fraction;
      const tickX = left + plotWidth * fraction;
      svg.appendChild(svgElement("line", { x1: tickX, y1: priceBottom, x2: tickX, y2: priceBottom + 5, class: "api-grid-line" }));
      const label = svgElement("text", { x: tickX, y: priceBottom + 22, class: "api-axis-label", "text-anchor": tick === 0 ? "start" : (tick === timeTicks - 1 ? "end" : "middle") });
      const zone = dataset.timezone === "Asia/Taipei" ? "Asia/Taipei" : "UTC";
      setText(label, new Intl.DateTimeFormat("zh-TW", { timeZone: zone, hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date(timeValue)));
      svg.appendChild(label);
    }

    const candleWidth = Math.max(1.2, Math.min(8, plotWidth / rows.length * 0.68));
    rows.forEach((row, index) => {
      if (isFills) {
        const side = String(row.side).toUpperCase();
        const markerClass = side === "BUY" ? "api-marker-buy" : "api-marker-sell";
        if (side === "BUY") {
          svg.appendChild(svgElement("circle", { cx: x(index), cy: y(Number(row.price)), r: 5, class: markerClass }));
        } else {
          const cx = x(index);
          const cy = y(Number(row.price));
          svg.appendChild(svgElement("polygon", { points: `${cx},${cy - 6} ${cx - 6},${cy + 5} ${cx + 6},${cy + 5}`, class: markerClass }));
        }
        const labelStep = Math.max(1, Math.ceil(rows.length / (width < 420 ? 8 : 12)));
        if (index % labelStep === 0 || index === rows.length - 1) {
          const label = svgElement("text", { x: x(index), y: y(Number(row.price)) + (side === "BUY" ? 18 : -10), class: markerClass, "text-anchor": "middle" });
          setText(label, side);
          svg.appendChild(label);
        }
        return;
      }
      const open = Number(row.open);
      const close = Number(row.close);
      const high = Number(row.high);
      const low = Number(row.low);
      const up = close >= open;
      const className = up ? "api-candle-up" : "api-candle-down";
      const center = x(index);
      svg.appendChild(svgElement("line", { x1: center, y1: y(high), x2: center, y2: y(low), class: className }));
      const bodyTop = Math.min(y(open), y(close));
      const bodyHeight = Math.max(1.5, Math.abs(y(open) - y(close)));
      svg.appendChild(svgElement("rect", { x: center - candleWidth / 2, y: bodyTop, width: candleWidth, height: bodyHeight, class: className }));
      const volumeHeight = Number(row.volume) / volumeMax * (volumeBottom - volumeTop);
      svg.appendChild(svgElement("rect", { x: center - candleWidth / 2, y: volumeBottom - volumeHeight, width: candleWidth, height: volumeHeight, class: up ? "api-volume-up" : "api-volume-down" }));
    });

    if (isCandles) [[10, "api-sma10"], [20, "api-sma20"]].forEach(([period, className]) => {
      const averages = movingAverage(rows, period);
      const points = averages.map((value, index) => value === null ? null : `${x(index)},${y(value)}`).filter(Boolean);
      if (points.length > 1) svg.appendChild(svgElement("polyline", { points: points.join(" "), class: className }));
    });

    const markers = isCandles && dataset.summary && Array.isArray(dataset.summary.trade_markers)
      ? dataset.summary.trade_markers : [];
    markers.slice(-100).forEach((marker) => {
      const time = Number(marker.time_ms);
      const price = numberValue(marker.price);
      if (!Number.isFinite(time) || price === null) return;
      let nearest = 0;
      let distance = Infinity;
      rows.forEach((row, index) => {
        const candidate = Math.abs(Number(row.time_ms) - time);
        if (candidate < distance) { distance = candidate; nearest = index; }
      });
      const side = String(marker.side || "").toUpperCase();
      if (side !== "BUY" && side !== "SELL") return;
      const label = svgElement("text", { x: x(nearest), y: y(price) + (side === "BUY" ? 18 : -10), class: side === "BUY" ? "api-marker-buy" : "api-marker-sell", "text-anchor": "middle" });
      setText(label, side);
      svg.appendChild(label);
    });

    container.appendChild(svg);
    const legend = card.querySelector(".api-chart-legend");
    if (legend) {
      legend.querySelectorAll(".legend-up, .legend-down, .legend-sma10, .legend-sma20").forEach((node) => { node.hidden = !isCandles; });
      const hasMarkers = isFills || markers.length > 0;
      legend.querySelectorAll(".legend-buy, .legend-sell").forEach((node) => { node.hidden = !hasMarkers; });
    }
    setText(byId("data-chart-unit"), `${dataset.timezone || "UTC"} · ${dataset.requested && dataset.requested.interval || ""}`);
    setText(
      byId("data-chart-note"),
      allRows.length > maxRows
        ? `為保持畫面流暢，只顯示最新 ${maxRows} ${isFills ? "筆成交" : "根 K 線"}；下載包含全部 ${allRows.length} 列受限資料。`
        : (isFills ? `依成交時間排列，顯示 ${allRows.length} 筆實際成交價格與方向；密集標示已省略，完整方向請查看表格。` : `顯示 ${allRows.length} 根受限資料；未收盤 K 線不視為最終值。`)
    );
  }

  function displayCell(value, column, timezone) {
    if (value == null) return "";
    if (column === "time_ms") return formatTime(value, timezone);
    if (column === "is_closed" && typeof value === "boolean") return value ? "已收盤" : "未收盤";
    if (column === "cost_known" && typeof value === "boolean") return value ? "已提供" : "未提供";
    if (column === "side") {
      const side = String(value).toUpperCase();
      if (side === "BUY") return "買入（BUY）";
      if (side === "SELL") return "賣出（SELL）";
    }
    if (column === "market") return marketLabel(value);
    if (column === "time_precision" && String(value) === "date") return "僅日期";
    if (typeof value === "object") return JSON.stringify(value);
    return String(value);
  }

  function renderTable(dataset) {
    const head = byId("data-table-head");
    const body = byId("data-table-body");
    clearNode(head);
    clearNode(body);
    const rows = Array.isArray(dataset.rows) ? dataset.rows : [];
    const visible = rows.slice(0, MAX_VISIBLE_ROWS);
    const columns = [];
    visible.forEach((row) => {
      if (!row || typeof row !== "object") return;
      Object.keys(row).forEach((key) => { if (!columns.includes(key)) columns.push(key); });
    });
    if (!columns.length) {
      setText(byId("data-table-note"), "這個有界視窗沒有回傳資料列；不代表帳戶沒有其他期間的交易。");
      return;
    }
    const headerRow = document.createElement("tr");
    columns.forEach((column) => {
      const cell = document.createElement("th");
      setText(cell, fieldLabel(column));
      headerRow.appendChild(cell);
    });
    head.appendChild(headerRow);
    visible.forEach((row) => {
      const tableRow = document.createElement("tr");
      columns.forEach((column) => {
        const cell = document.createElement("td");
        setText(cell, displayCell(row[column], column, dataset.timezone));
        tableRow.appendChild(cell);
      });
      body.appendChild(tableRow);
    });
    setText(
      byId("data-table-note"),
      rows.length > MAX_VISIBLE_ROWS
        ? `畫面顯示前 ${MAX_VISIBLE_ROWS} 列；下載包含全部 ${rows.length} 列。先保留原始成交資料，再決定是否做 FIFO／已平倉分析。`
        : `畫面顯示全部 ${rows.length} 列。先保留原始成交資料，再決定是否做 FIFO／已平倉分析。`
    );
  }

  function appendSummaryTable(container, titleText, rows, timezone) {
    if (!Array.isArray(rows) || !rows.length) return;
    const section = document.createElement("section");
    const title = document.createElement("h5");
    setText(title, titleText);
    section.appendChild(title);
    const scroll = document.createElement("div");
    scroll.className = "table-scroll compact-table";
    scroll.tabIndex = 0;
    const table = document.createElement("table");
    const columns = [];
    rows.forEach((row) => Object.keys(row || {}).forEach((key) => { if (!columns.includes(key)) columns.push(key); }));
    const thead = document.createElement("thead");
    const trh = document.createElement("tr");
    columns.forEach((column) => { const th = document.createElement("th"); setText(th, fieldLabel(column)); trh.appendChild(th); });
    thead.appendChild(trh);
    const tbody = document.createElement("tbody");
    rows.forEach((row) => {
      const tr = document.createElement("tr");
      columns.forEach((column) => { const td = document.createElement("td"); setText(td, displayCell(row[column], column, timezone)); tr.appendChild(td); });
      tbody.appendChild(tr);
    });
    table.append(thead, tbody);
    scroll.appendChild(table);
    section.appendChild(scroll);
    container.appendChild(section);
  }

  function renderAccount(dataset) {
    const card = byId("data-account-card");
    const groups = byId("data-account-groups");
    clearNode(groups);
    if (dataset.kind !== "account") {
      card.hidden = true;
      return;
    }
    card.hidden = false;
    const summary = dataset.summary || {};
    appendSummaryTable(groups, "幣別餘額", summary.balances || [], dataset.timezone);
    appendSummaryTable(groups, "持倉與原始單位", summary.positions || [], dataset.timezone);
    appendSummaryTable(groups, "可選帳戶（遮蔽標示）", summary.accounts || [], dataset.timezone);
    const select = byId("data-account-ref");
    const previous = select.value;
    while (select.options.length > 1) select.remove(1);
    (Array.isArray(summary.accounts) ? summary.accounts : []).forEach((account) => {
      if (!account || typeof account.ref !== "string") return;
      const option = document.createElement("option");
      option.value = account.ref;
      setText(option, `${account.label || "已遮蔽帳戶"} · ${account.market || ""}`);
      select.appendChild(option);
    });
    if ([...select.options].some((option) => option.value === previous)) select.value = previous;
  }

  function renderDataset(dataset, job) {
    currentDataset = dataset;
    byId("data-result").hidden = false;
    renderFacts(dataset);
    const chart = byId("data-candle-chart");
    if (chart) chart.setAttribute("aria-label", dataset.kind === "fills" ? "實際成交價格與方向圖" : "唯讀 K 線與成交量圖");
    renderCandles(dataset);
    renderTable(dataset);
    renderAccount(dataset);
    const analyzable = dataset.kind === "fills" || dataset.kind === "realizations";
    byId("data-analysis-card").hidden = !analyzable;
    byId("crypto-analysis-confirmations").hidden = dataset.provider === "shioaji";
    byId("shioaji-analysis-confirmations").hidden = dataset.provider !== "shioaji";
    setText(byId("data-analysis-status"), analyzable ? "尚未建立已平倉交易；原始表格與下載仍可使用。" : "");
    const privateRead = dataset.provider === "shioaji" || dataset.kind !== "candles";
    setText(
      byId("data-job-status"),
      privateRead && job.authentication_verified
        ? "讀取可用；只證明這次唯讀查詢成功，不證明帳戶已授予所有唯讀權限。"
        : "公開資料讀取完成；未使用私有憑證驗證。"
    );
    scheduleRefresh();
    setJobBusy(false);
  }

  function pollJob(jobId, nonce, attempt = 0) {
    if (closed || nonce !== pollNonce || jobId !== currentJobId) return;
    if (attempt >= MAX_POLL_ATTEMPTS) {
      currentJobStatus = "failed";
      setJobBusy(false);
      setText(byId("data-job-status"), "狀態輪詢已達安全上限；未採用未確認資料。");
      ui.showError("唯讀 API 工作未在安全期限內回報終態。");
      return;
    }
    pollTimer = window.setTimeout(async () => {
      if (closed || nonce !== pollNonce || jobId !== currentJobId || requestInFlight === false) return;
      try {
        const job = await ui.api(`/api/data-job/${jobId}`);
        if (closed || nonce !== pollNonce || jobId !== currentJobId) return;
        currentJobStatus = job.status;
        const progress = job.progress || {};
        const pages = Number(progress.pages) || 0;
        const records = Number(progress.records) || 0;
        const progressNode = byId("data-progress");
        progressNode.hidden = false;
        progressNode.value = Math.min(95, Math.max(2, pages * 5));
        setText(byId("data-job-status"), `已讀取 ${formatCount(pages)} 頁、${formatCount(records)} 列；仍可查看其他唯讀狀態。`);
        if (job.status === "running") {
          pollJob(jobId, nonce, attempt + 1);
          return;
        }
        progressNode.hidden = true;
        setJobBusy(false);
        if (job.status === "completed" && job.result) {
          renderDataset(job.result, job);
        } else if (job.status === "cancelled") {
          setText(byId("data-job-status"), "唯讀資料工作已取消，沒有採用部分結果。");
        } else {
          const message = job.error && job.error.message ? job.error.message : "唯讀資料工作失敗；沒有採用部分結果。";
          setText(byId("data-job-status"), message);
          ui.showError(message);
        }
      } catch (error) {
        if (nonce !== pollNonce) return;
        currentJobStatus = "failed";
        setJobBusy(false);
        const message = error && error.message ? error.message : "無法讀取唯讀資料工作狀態。";
        setText(byId("data-job-status"), message);
        ui.showError(message);
      }
    }, POLL_MS);
  }

  async function startSync(automatic = false) {
    if (closed || requestInFlight || currentJobStatus === "running") return;
    if (automatic && !active) return;
    if (refreshTimer !== null) window.clearTimeout(refreshTimer);
    refreshTimer = null;
    let request;
    try {
      request = buildRequest();
    } catch (error) {
      if (!automatic) ui.showError(error.message);
      return;
    }
    currentQuery = request;
    currentDataset = null;
    byId("data-result").hidden = true;
    ui.invalidateAnalysis("新的 API 讀取已開始；舊分析、資金情境與匯出綁定已失效。");
    currentJobStatus = "running";
    setJobBusy(true);
    byId("data-progress").hidden = false;
    byId("data-progress").value = 2;
    setText(byId("data-job-status"), automatic ? "正在刷新公開 K 線……" : "正在建立唯讀資料工作……");
    try {
      const job = await ui.post("/api/data-sync", request);
      currentJobId = job.job_id;
      currentJobStatus = job.status;
      pollNonce += 1;
      pollJob(currentJobId, pollNonce);
    } catch (error) {
      currentJobStatus = "failed";
      setJobBusy(false);
      byId("data-progress").hidden = true;
      const message = error && error.message ? error.message : "無法啟動唯讀資料工作。";
      setText(byId("data-job-status"), message);
      ui.showError(message);
    }
  }

  async function configureCredentials(event) {
    event.preventDefault();
    if (closed || requestInFlight) return;
    const form = event.currentTarget;
    const keyInput = byId("credential-key");
    const secretInput = byId("credential-secret");
    const payload = {
      provider: byId("data-provider").value,
      profile: String(byId("credential-profile").value || "default").trim().toLowerCase(),
      api_key: keyInput.value,
      api_secret: secretInput.value,
      remember: byId("credential-remember").checked,
      use_saved: byId("credential-use-saved").checked,
    };
    if (!form.reportValidity()) return;
    setText(byId("credential-status"), "正在設定本機憑證參照……");
    try {
      const configured = await ui.post("/api/data-credentials", payload);
      const location = configured.remembered ? "Windows Credential Manager" : "本次程式記憶體";
      setText(byId("credential-status"), `已設定到${location}；尚未驗證遠端登入，第一次唯讀查詢後才知道是否可讀。`);
      suppressInvalidation = true;
      byId("data-provider").value = configured.provider;
      byId("credential-provider").value = configured.provider;
      byId("credential-profile").value = configured.profile;
      suppressInvalidation = false;
      updateFormForProvider();
    } catch (error) {
      const message = error && error.message ? error.message : "無法設定本機憑證。";
      setText(byId("credential-status"), message);
      ui.showError(message);
    } finally {
      // Password fields are always cleared after the POST attempt, including
      // server-side validation failures.
      keyInput.value = "";
      secretInput.value = "";
    }
  }

  function updateSavedCredentialMode() {
    const saved = byId("credential-use-saved").checked;
    byId("credential-key").disabled = saved;
    byId("credential-secret").disabled = saved;
    byId("credential-remember").disabled = saved;
    if (saved) {
      byId("credential-key").value = "";
      byId("credential-secret").value = "";
      byId("credential-remember").checked = false;
    }
  }

  async function exportDataset(format) {
    if (!currentJobId || !currentDataset || requestInFlight) return;
    try {
      const artifact = await ui.post("/api/data-export", { job_id: currentJobId, format });
      await ui.downloadArtifact(artifact);
      ui.showNotice(`已要求下載目前資料集的完整 ${format.toUpperCase()}。`);
    } catch (error) {
      ui.showError(error && error.message ? error.message : "資料下載失敗。");
    }
  }

  async function analyzeClosed() {
    if (!currentJobId || !currentDataset || requestInFlight) return;
    const shioaji = currentDataset.provider === "shioaji";
    const opening = byId("data-opening-zero").checked;
    const transfers = byId("data-transfers-reconciled").checked;
    const pnlBasis = byId("data-pnl-basis").value;
    const totalCosts = byId("data-total-costs").checked;
    if (!shioaji && (!opening || !transfers)) {
      setText(byId("data-analysis-status"), "需先明確確認期初庫存為 0，並完成轉入／轉出對帳；原始資料仍可下載。");
      return;
    }
    if (shioaji && (pnlBasis === "unknown" || !totalCosts)) {
      setText(byId("data-analysis-status"), "需先確認損益是 gross 或 net，並確認費用／交易稅總額完整；原始資料仍可下載。");
      return;
    }
    setText(byId("data-analysis-status"), "正在將可閉合週期交給既有統計核心……");
    try {
      const result = await ui.post("/api/data-analyze", {
        job_id: currentJobId,
        opening_zero_confirmed: opening,
        transfers_reconciled: transfers,
        pnl_basis: pnlBasis,
        total_costs_confirmed: totalCosts,
      });
      if (!result.available || !result.analysis) {
        const normalization = result.normalization || {};
        const warnings = Array.isArray(normalization.warnings) ? normalization.warnings.join("；") : "";
        setText(byId("data-analysis-status"), `${normalization.reason || "資料仍有未解週期，不能建立推論式交易紀錄。"}${warnings ? `｜${warnings}` : ""}`);
        return;
      }
      setText(byId("data-analysis-status"), "已建立來源綁定分析；正在切換到分析結果。");
      ui.applyAnalysis(result.analysis);
      ui.showNotice("已用可閉合且費用口徑已確認的交易建立分析；原始 API 列仍未被改寫。");
    } catch (error) {
      const message = error && error.message ? error.message : "無法建立已平倉分析。";
      setText(byId("data-analysis-status"), message);
      ui.showError(message);
    }
  }

  function bind() {
    const credentialsForm = byId("data-credentials-form");
    const syncForm = byId("data-sync-form");
    if (!credentialsForm || !syncForm) return;
    credentialsForm.addEventListener("submit", configureCredentials);
    credentialsForm.addEventListener("input", (event) => {
      if (event.target.id === "credential-use-saved") updateSavedCredentialMode();
      invalidateForInput("憑證設定已變更；舊資料集與分析已失效。");
    });
    document.addEventListener("ui:state", (event) => {
      const state = event.detail && event.detail.state;
      if (state) updateCapabilities(state);
    });
    syncForm.addEventListener("input", () => invalidateForInput("API 查詢輸入已變更；舊資料集與分析已失效。"));
    byId("data-provider").addEventListener("change", () => { updateFormForProvider(); });
    byId("data-kind").addEventListener("change", () => { updateFormForProvider(); });
    syncForm.addEventListener("submit", (event) => { event.preventDefault(); startSync(false); });
    byId("data-cancel").addEventListener("click", async () => {
      if (!currentJobId || currentJobStatus !== "running") return;
      currentJobStatus = "cancel_requested";
      setJobBusy(true);
      setText(byId("data-job-status"), "正在取消唯讀資料工作……");
      try { await ui.post("/api/data-cancel", { job_id: currentJobId }); }
      catch (error) { ui.showError(error && error.message ? error.message : "無法送出取消要求。"); }
    });
    byId("data-export-json").addEventListener("click", () => exportDataset("json"));
    byId("data-export-csv").addEventListener("click", () => exportDataset("csv"));
    byId("data-analyze").addEventListener("click", analyzeClosed);
    ["data-opening-zero", "data-transfers-reconciled", "data-pnl-basis", "data-total-costs"].forEach((id) => {
      byId(id).addEventListener("change", () => {
        setText(byId("data-analysis-status"), "確認條件已變更；尚未建立新的已平倉分析。");
        ui.invalidateAnalysis("分析確認條件已變更；舊分析與風險情境已失效，可保留原始資料重新分析。");
      });
    });
    document.addEventListener("ui:section-change", (event) => {
      const nextActive = Boolean(event.detail && event.detail.section === "api-data");
      if (!nextActive && active) {
        const running = currentJobStatus === "running" || currentJobStatus === "cancel_requested";
        const jobId = currentJobId;
        stopTimers();
        if (running && jobId) ui.post("/api/data-cancel", { job_id: jobId }).catch(() => {});
      }
      active = nextActive;
      if (active) scheduleRefresh();
    });
    document.addEventListener("ui:session-close", () => {
      closed = true;
      stopTimers();
      byId("credential-key").value = "";
      byId("credential-secret").value = "";
      currentDataset = null;
      currentJobId = null;
      currentQuery = null;
    });
    updateSavedCredentialMode();
    updateFormForProvider();
    setJobBusy(false);
  }

  let chartResizeTimer = null;
  window.addEventListener("resize", () => {
    if (chartResizeTimer !== null) window.clearTimeout(chartResizeTimer);
    chartResizeTimer = window.setTimeout(() => {
      chartResizeTimer = null;
      if (currentDataset && (currentDataset.kind === "candles" || currentDataset.kind === "fills")) {
        renderCandles(currentDataset);
      }
    }, 100);
  });
  bind();
  if (typeof ui.getState === "function") updateCapabilities(ui.getState());
})();
