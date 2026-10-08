"use strict";

(() => {
  const tokenMeta = document.querySelector("meta[name='ui-token']");
  let sessionToken = tokenMeta ? tokenMeta.content : "";
  const MAX_FILE_BYTES = 2 * 1024 * 1024;
  const STAGE_TITLES = Object.freeze({
    stop_real_money: "停止真錢交易",
    paper_only: "僅紙上模擬",
    paper_until_oos: "紙上模擬，直到樣本外驗證",
    paper_until_risk_data: "補齊風險資料後再評估",
    tiny_live_validation: "極小額驗證",
  });
  const AUTOMATION_STATUS_LABELS = Object.freeze({
    available: "有資料可檢視",
    paper_scaffold: "可建立",
    unverified: "尚未驗證",
    not_provided: "未提供",
  });
  const AUTOMATION_KEY_LABELS = Object.freeze({
    data: "資料",
    data_available: "資料可用性",
    paper: "紙上交易專案",
    paper_broker: "紙上交易專案",
    live: "真實下單",
    live_trading: "真實下單",
    broker: "券商連線",
    credentials: "券商憑證",
    rules: "策略規則",
  });

  let currentState = null;
  let currentAnalysisId = null;
  let currentAnalysisRevision = null;
  let currentSimulationId = null;
  let currentRiskJobId = null;
  let currentRiskJobStatus = null;
  let riskFormRevision = 0;
  let riskPollTimer = null;
  let riskPollInFlight = false;
  let riskPollNonce = 0;
  let localResultInvalidated = false;
  let actionRunning = false;
  let sessionClosed = false;
  let pendingConfirmation = null;
  let confirmationReturnFocus = null;

  const byId = (id) => document.getElementById(id);
  const notice = byId("notice");
  const errorBox = byId("error");

  function setText(node, value) {
    if (node) node.textContent = value == null ? "" : String(value);
  }

  function uiCopy(value, fallback = "") {
    const text = value == null ? "" : String(value);
    const cleaned = text
      .replace(/\bNone\b/g, "未提供")
      .replace(/\bnull\b/gi, "未提供")
      .replace(/\bR7\b/g, "分位數")
      .replace(/\bschema\b/gi, "資料格式")
      .replace(/\bholdout\b/gi, "前後段")
      .trim();
    return cleaned || fallback;
  }

  function automationStatus(status, key) {
    if (String(key || "").toLowerCase() === "paper_scaffold") return "可建立";
    return AUTOMATION_STATUS_LABELS[status] || uiCopy(status, "未提供");
  }

  function automationLabel(item) {
    const key = String(item && item.key || "").toLowerCase();
    return uiCopy(item && item.label, AUTOMATION_KEY_LABELS[key] || "自動化項目");
  }

  function clearNode(node) {
    while (node && node.firstChild) node.removeChild(node.firstChild);
  }

  function showNotice(message) {
    errorBox.hidden = true;
    notice.hidden = false;
    setText(notice, message);
  }

  function showError(message) {
    notice.hidden = true;
    errorBox.hidden = false;
    setText(errorBox, message || "操作失敗，請稍後再試。");
  }

  function clearMessages() {
    notice.hidden = true;
    errorBox.hidden = true;
    setText(notice, "");
    setText(errorBox, "");
  }

  function clearSensitiveWorkspace() {
    currentState = null;
    currentAnalysisId = null;
    currentAnalysisRevision = null;
    currentSimulationId = null;
    currentRiskJobId = null;
    currentRiskJobStatus = null;
    riskFormRevision += 1;
    riskPollNonce += 1;
    if (riskPollTimer !== null) window.clearTimeout(riskPollTimer);
    riskPollTimer = null;
    riskPollInFlight = false;
    localResultInvalidated = true;
    pendingConfirmation = null;
    confirmationReturnFocus = null;
    sessionToken = "";

    try {
      if (tokenMeta) tokenMeta.content = "";
    } catch (_) {
      // Continue clearing the rendered workspace even if metadata is unavailable.
    }

    const workspace = byId("workspace");
    if (workspace) {
      try {
        workspace.querySelectorAll("form").forEach((form) => {
          try { form.reset(); } catch (_) { /* continue clearing other forms */ }
        });
      } catch (_) {
        // Continue with the remaining cleanup steps.
      }
      try {
        workspace.querySelectorAll("input[type='file']").forEach((input) => {
          try { input.value = ""; } catch (_) { /* continue clearing other inputs */ }
        });
      } catch (_) {
        // Continue with the remaining cleanup steps.
      }
      try {
        const scanText = byId("scan-text");
        if (scanText) scanText.value = "";
      } catch (_) {
        // Continue with the remaining cleanup steps.
      }
      try {
        workspace.replaceChildren();
      } catch (_) {
        // Hiding the workspace below still prevents the old content from being used.
      }
      try {
        workspace.hidden = true;
      } catch (_) {
        // Nothing else is required to show the closed-screen status.
      }
    }

    try {
      const shell = document.querySelector(".shell");
      if (shell) shell.hidden = true;
    } catch (_) {
      // The closed-screen status remains available even if the shell cannot be hidden.
    }
  }

  function settleConfirmation(accepted) {
    if (!pendingConfirmation) return;
    const resolve = pendingConfirmation;
    pendingConfirmation = null;
    byId("confirm-panel").hidden = true;
    const returnFocus = confirmationReturnFocus;
    confirmationReturnFocus = null;
    if (returnFocus && typeof returnFocus.focus === "function") returnFocus.focus();
    resolve(Boolean(accepted));
  }

  function requestConfirmation({ title, message, acceptLabel, trigger }) {
    if (pendingConfirmation) return Promise.resolve(false);
    const panel = byId("confirm-panel");
    setText(byId("confirm-title"), title);
    setText(byId("confirm-message"), message);
    setText(byId("confirm-accept"), acceptLabel);
    confirmationReturnFocus = trigger || document.activeElement;
    panel.hidden = false;
    return new Promise((resolve) => {
      pendingConfirmation = resolve;
      window.setTimeout(() => byId("confirm-cancel").focus(), 0);
    });
  }

  function bindConfirmation() {
    const panel = byId("confirm-panel");
    byId("confirm-cancel").addEventListener("click", () => settleConfirmation(false));
    byId("confirm-accept").addEventListener("click", () => settleConfirmation(true));
    panel.addEventListener("click", (event) => {
      if (event.target === panel) settleConfirmation(false);
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && pendingConfirmation) {
        event.preventDefault();
        settleConfirmation(false);
      }
    });
  }

  function actionButtons() {
    return document.querySelectorAll(
      "form button, .sample-button, .analysis-download, #download-template, #download-records, #analyze-records, #go-paper"
    );
  }

  function updateControls() {
    const hasRows = Boolean(currentState && currentState.manual_rows.length);
    const hasAnalysis = Boolean(currentAnalysisId && !localResultInvalidated);
    const riskRunning = currentRiskJobStatus === "running" || currentRiskJobStatus === "cancel_requested";
    actionButtons().forEach((button) => {
      let disabled = actionRunning || sessionClosed || riskRunning;
      if (button.id === "analyze-records") disabled = disabled || !hasRows;
      if (button.classList.contains("analysis-download")) disabled = disabled || !hasAnalysis;
      if (button.id === "run-risk") {
        const available = Boolean(
          currentState && currentState.analysis && currentState.analysis.visuals &&
          currentState.analysis.visuals.risk_simulation_available
        );
        disabled = actionRunning || sessionClosed || riskRunning || !hasAnalysis || !available;
      }
      button.disabled = disabled;
    });
    const cancel = byId("cancel-risk");
    if (cancel) {
      cancel.hidden = !riskRunning;
      cancel.disabled = sessionClosed || currentRiskJobStatus === "cancel_requested";
    }
  }

  async function api(path, options = {}) {
    const method = options.method || "GET";
    const headers = new Headers(options.headers || {});
    headers.set("X-UI-Token", sessionToken);
    if (method === "POST") headers.set("Content-Type", "application/json");
    const response = await fetch(path, {
      method,
      headers,
      body: options.body,
      cache: "no-store",
      credentials: "omit",
      referrerPolicy: "no-referrer",
    });
    const contentType = response.headers.get("Content-Type") || "";
    let payload = null;
    if (contentType.includes("application/json")) {
      payload = await response.json();
    }
    if (!response.ok) {
      const message = payload && payload.error ? payload.error : `操作失敗（HTTP ${response.status}）`;
      const err = new Error(message);
      err.status = response.status;
      throw err;
    }
    return payload;
  }

  async function post(path, payload) {
    return api(path, { method: "POST", body: JSON.stringify(payload) });
  }

  async function runAction(button, task, options = {}) {
    if (actionRunning || sessionClosed) return;
    clearMessages();
    actionRunning = true;
    updateControls();
    if (button) button.setAttribute("aria-busy", "true");
    try {
      await task();
    } catch (error) {
      if (options.invalidateResult) {
        invalidateLocalResult("新的輸入尚未成功分析，舊結果已停止顯示。");
        try {
          const state = await api("/api/state");
          renderState(state, { allowAnalysis: false });
        } catch (_) {
          // Preserve the original, more useful operation error.
        }
      }
      showError(error && error.message ? error.message : "操作失敗，請稍後再試。");
    } finally {
      actionRunning = false;
      if (button) button.removeAttribute("aria-busy");
      updateControls();
    }
  }

  function selectSection(name, focus = false) {
    byId("workspace").classList.toggle("results-mode", name === "results");
    document.querySelectorAll("[data-panel]").forEach((section) => {
      section.hidden = section.dataset.panel !== name;
    });
    document.querySelectorAll(".nav-button").forEach((button) => {
      const active = button.dataset.section === name;
      button.classList.toggle("is-active", active);
      if (active) button.setAttribute("aria-current", "page");
      else button.removeAttribute("aria-current");
    });
    if (focus) byId("workspace").focus({ preventScroll: true });
    const reduced = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    window.scrollTo({ top: 0, behavior: reduced ? "auto" : "smooth" });
  }

  function invalidateLocalResult(reason) {
    invalidateRiskResult("分析已變更；舊資金情境不再適用。", true);
    currentAnalysisId = null;
    currentAnalysisRevision = null;
    localResultInvalidated = true;
    byId("analysis-content").hidden = true;
    byId("no-analysis").hidden = false;
    const message = byId("no-analysis").querySelector("p:last-child");
    if (message && reason) setText(message, reason);
    updateControls();
  }

  function appendDefinition(list, label, value, className = "") {
    const wrapper = document.createElement("div");
    if (className) wrapper.className = className;
    const term = document.createElement("dt");
    const detail = document.createElement("dd");
    setText(term, label);
    setText(detail, value);
    wrapper.append(term, detail);
    list.appendChild(wrapper);
  }

  function formatNumber(value, digits = 2) {
    if (value === null || value === undefined || typeof value !== "number" || !Number.isFinite(value)) {
      return "無法計算";
    }
    return new Intl.NumberFormat("zh-TW", {
      maximumFractionDigits: digits,
      minimumFractionDigits: 0,
    }).format(value);
  }

  function formatPercent(value, reliable = true) {
    if (!reliable || value === null || value === undefined || typeof value !== "number" || !Number.isFinite(value)) {
      return "無法計算";
    }
    return `${formatNumber(value * 100, 2)}%`;
  }

  function appendCells(row, values) {
    values.forEach((value) => {
      const cell = document.createElement("td");
      setText(cell, value);
      row.appendChild(cell);
    });
  }

  function clearRiskRendered() {
    currentSimulationId = null;
    const result = byId("risk-result");
    if (result) result.hidden = true;
    ["risk-facts", "risk-summary-body", "risk-warning-list"].forEach((id) => clearNode(byId(id)));
    if (window.EvidenceCharts) window.EvidenceCharts.clear(byId("risk-chart"));
    const progress = byId("risk-progress");
    if (progress) {
      progress.hidden = true;
      progress.value = 0;
    }
    setText(byId("risk-zero-note"), "");
  }

  function invalidateRiskResult(reason, requestCancel = false, clearInput = false) {
    riskFormRevision += 1;
    clearRiskRendered();
    if (reason) setText(byId("risk-status"), reason);
    const running = currentRiskJobStatus === "running" || currentRiskJobStatus === "cancel_requested";
    const jobId = currentRiskJobId;
    if (requestCancel && running && jobId) {
      currentRiskJobStatus = "cancel_requested";
      post("/api/cancel-risk-simulation", { job_id: jobId }).catch(() => {
        // Polling owns the authoritative terminal state; never attach a stale result.
      });
    }
    if (clearInput) {
      const startEquity = byId("risk-start-equity");
      if (startEquity) startEquity.value = "";
      stopRiskPolling();
      currentRiskJobId = null;
      currentRiskJobStatus = null;
    } else if (!running && currentRiskJobStatus !== "cancel_requested") {
      currentRiskJobId = null;
      currentRiskJobStatus = null;
    }
    updateControls();
  }

  function renderSegmentCard(container, title, segment, currency) {
    const card = document.createElement("article");
    card.className = "segment-card";
    const heading = document.createElement("h5");
    setText(heading, title);
    card.appendChild(heading);
    const list = document.createElement("dl");
    const amountAvailable = Boolean(segment && segment.amounts_available);
    const pairs = [
      ["筆數", formatNumber(segment && (segment.count ?? segment.n_trades), 0)],
      ["平均", amountAvailable ? `${formatNumber(segment.mean)} ${currency}` : "無法計算"],
      ["中位數", amountAvailable ? `${formatNumber(segment.median)} ${currency}` : "無法計算"],
      ["勝率", formatPercent(segment && segment.win_rate)],
      ["日期", segment && segment.start_time && segment.end_time ? `${String(segment.start_time).slice(0, 10)} ～ ${String(segment.end_time).slice(0, 10)}` : "無法計算"],
    ];
    pairs.forEach(([label, value]) => {
      const wrapper = document.createElement("div");
      const term = document.createElement("dt");
      const detail = document.createElement("dd");
      setText(term, label);
      setText(detail, value);
      wrapper.append(term, detail);
      list.appendChild(wrapper);
    });
    card.appendChild(list);
    container.appendChild(card);
  }

  function renderVisuals(visuals) {
    const payload = visuals || {};
    const currency = payload.currency || "";
    setText(byId("historical-unit"), currency ? `金額 · ${currency}` : "幣別未確認");
    setText(byId("distribution-unit"), currency ? `每筆 · ${currency}` : "幣別未確認");
    const charts = window.EvidenceCharts;
    if (charts) {
      charts.renderHistorical(byId("historical-chart"), payload.historical || {}, currency);
      charts.renderDistribution(byId("distribution-chart"), payload.distribution || {}, currency);
      charts.renderHoldout(byId("holdout-chart"), payload.holdout || {}, currency);
    }

    const historical = payload.historical || {};
    if (historical.available && Array.isArray(historical.points) && historical.points.length) {
      const last = historical.points[historical.points.length - 1];
      setText(
        byId("historical-summary"),
        `期末累積 ${formatNumber(last.cum_pnl)} ${currency}；最大回撤金額 ${formatNumber(historical.max_drawdown_amount)} ${currency}。僅含已平倉交易，不含浮動損益、入金或出金。`
      );
    } else {
      setText(byId("historical-summary"), uiCopy(historical.reason, "無法建立可靠時間曲線。"));
    }

    const distribution = payload.distribution || {};
    const distributionBody = byId("distribution-summary-body");
    clearNode(distributionBody);
    if (distribution.available && distribution.summary) {
      const summary = distribution.summary;
      const row = document.createElement("tr");
      appendCells(row, [
        formatNumber(summary.minimum), formatNumber(summary.p05), formatNumber(summary.q1),
        formatNumber(summary.median), formatNumber(summary.q3), formatNumber(summary.p95),
        formatNumber(summary.maximum), formatNumber(summary.mean),
      ]);
      distributionBody.appendChild(row);
    } else {
      const row = document.createElement("tr");
      const cell = document.createElement("td");
      cell.colSpan = 8;
      setText(cell, distribution.reason || "無法計算");
      row.appendChild(cell);
      distributionBody.appendChild(row);
    }

    const holdout = payload.holdout || {};
    const cards = byId("holdout-cards");
    clearNode(cards);
    if (holdout.available) {
      renderSegmentCard(cards, "樣本內（前段）", holdout.in_sample || {}, currency);
      renderSegmentCard(cards, "樣本外（後段）", holdout.out_sample || {}, currency);
      setText(byId("holdout-headline"), holdout.headline || "核心未提供前後段結論。");
    } else {
      setText(byId("holdout-headline"), uiCopy(holdout.reason, "目前無法做前後段切分。"));
    }
    const holdoutNotes = byId("holdout-notes");
    clearNode(holdoutNotes);
    const notes = Array.isArray(holdout.interpretation) ? holdout.interpretation : [];
    notes.concat([
      "這是完成交易的一次時序切分，不是市場價格策略回測。",
      "前後段切分不能證明後段在策略設計時完全未被看過。",
    ]).forEach((note) => {
      const item = document.createElement("li");
      setText(item, uiCopy(note));
      holdoutNotes.appendChild(item);
    });

    const automation = byId("automation-readiness");
    clearNode(automation);
    (Array.isArray(payload.automation_readiness) ? payload.automation_readiness : []).forEach((item) => {
      const row = document.createElement("li");
      row.dataset.status = item.status || "unknown";
      const copy = document.createElement("div");
      const strong = document.createElement("strong");
      const detail = document.createElement("small");
      setText(strong, `${automationLabel(item)}｜${automationStatus(item.status, item.key)}`);
      setText(detail, uiCopy(item.detail));
      copy.append(strong, detail);
      row.appendChild(copy);
      automation.appendChild(row);
    });

    const warningList = byId("visual-warning-list");
    clearNode(warningList);
    (Array.isArray(payload.warnings) ? payload.warnings : []).forEach((warning) => {
      const item = document.createElement("li");
      setText(item, uiCopy(warning));
      warningList.appendChild(item);
    });
    const currencyInput = byId("risk-currency");
    if (currencyInput) currencyInput.value = currency;
    setText(
      byId("risk-availability"),
      payload.risk_simulation_available
        ? `可執行情境：${currency} 固定金額損益重抽樣。這不是未來預測。`
        : uiCopy(payload.risk_simulation_reason, "目前資料不能執行資金情境。")
    );
  }

  function renderRiskResult(risk) {
    clearRiskRendered();
    currentSimulationId = risk.simulation_id || null;
    byId("risk-result").hidden = false;
    const facts = byId("risk-facts");
    appendDefinition(facts, "起始資金", `${formatNumber(risk.start_equity)} ${risk.currency || ""}`);
    appendDefinition(facts, "資金警戒線", `${formatNumber(risk.threshold_amount)} ${risk.currency || ""}`);
    appendDefinition(facts, "曾跌破路徑", `${formatNumber(risk.hit_count, 0)} / ${formatNumber(risk.paths, 0)}（${formatPercent(risk.hit_fraction)}）`);
    appendDefinition(facts, "首次跌破中位筆數", risk.first_hit ? formatNumber(risk.first_hit.median, 1) : "本次未出現");
    appendDefinition(facts, "期末 P05", `${formatNumber(risk.terminal && risk.terminal.p05)} ${risk.currency || ""}`);
    appendDefinition(facts, "期末中位數", `${formatNumber(risk.terminal && risk.terminal.median)} ${risk.currency || ""}`);
    appendDefinition(facts, "期末 P95", `${formatNumber(risk.terminal && risk.terminal.p95)} ${risk.currency || ""}`);
    appendDefinition(facts, "停止假設", "嚴格跌破後停止並持有穿越金額");
    setText(byId("risk-zero-note"), risk.zero_hit_note || (risk.initially_below ? "起始資金已嚴格低於門檻，全部路徑在第 0 筆命中。" : "情境頻率不是未來機率。"));
    if (window.EvidenceCharts) window.EvidenceCharts.renderRisk(byId("risk-chart"), risk);
    const body = byId("risk-summary-body");
    clearNode(body);
    const terminal = risk.terminal || {};
    const row = document.createElement("tr");
    appendCells(row, [
      formatNumber(terminal.minimum), formatNumber(terminal.p05), formatNumber(terminal.q1),
      formatNumber(terminal.median), formatNumber(terminal.q3), formatNumber(terminal.p95),
      formatNumber(terminal.maximum),
    ]);
    body.appendChild(row);
    const warnings = byId("risk-warning-list");
    clearNode(warnings);
    (Array.isArray(risk.warnings) ? risk.warnings : []).forEach((warning) => {
      const item = document.createElement("li");
      setText(item, warning);
      warnings.appendChild(item);
    });
  }

  function stopRiskPolling() {
    riskPollNonce += 1;
    if (riskPollTimer !== null) window.clearTimeout(riskPollTimer);
    riskPollTimer = null;
    riskPollInFlight = false;
  }

  function pollRisk(jobId, formRevision, nonce, attempt = 0, failures = 0) {
    if (sessionClosed || nonce !== riskPollNonce || jobId !== currentRiskJobId) return;
    if (attempt >= 120) {
      stopRiskPolling();
      currentRiskJobStatus = "failed";
      showError("資金情境狀態輪詢已達上限；沒有附加未確認結果。");
      updateControls();
      return;
    }
    riskPollTimer = window.setTimeout(async () => {
      if (riskPollInFlight || sessionClosed || nonce !== riskPollNonce || jobId !== currentRiskJobId) return;
      riskPollInFlight = true;
      try {
        const job = await api(`/api/risk-simulation/${jobId}`);
        if (sessionClosed || nonce !== riskPollNonce || jobId !== currentRiskJobId) return;
        const progress = job.progress || {};
        const completed = Number(progress.completed_paths) || 0;
        const total = Number(progress.paths) || 1;
        byId("risk-progress").hidden = false;
        byId("risk-progress").value = Math.max(0, Math.min(100, completed / total * 100));
        setText(byId("risk-status"), `已完成 ${completed.toLocaleString("zh-TW")} / ${total.toLocaleString("zh-TW")} 條路徑`);
        if (job.status === "running") {
          currentRiskJobStatus = job.cancel_requested ? "cancel_requested" : "running";
          pollRisk(jobId, formRevision, nonce, attempt + 1, 0);
        } else {
          stopRiskPolling();
          currentRiskJobStatus = job.status;
          byId("risk-progress").hidden = true;
          if (job.status === "completed" && job.result && formRevision === riskFormRevision && job.analysis_id === currentAnalysisId && job.revision === currentAnalysisRevision) {
            renderRiskResult(job.result);
            setText(byId("risk-status"), "資金情境完成；匯出時可明確附帶這一版結果。");
          } else if (job.status === "cancelled") {
            setText(byId("risk-status"), "資金情境已取消，未附加任何結果。");
          } else if (job.status === "failed") {
            showError(job.error || "資金情境計算失敗，未附加任何結果。");
          }
          updateControls();
        }
      } catch (pollError) {
        if (nonce !== riskPollNonce) return;
        if (failures < 2) pollRisk(jobId, formRevision, nonce, attempt + 1, failures + 1);
        else {
          stopRiskPolling();
          currentRiskJobStatus = "failed";
          showError(pollError && pollError.message ? pollError.message : "無法讀取資金情境狀態。");
          updateControls();
        }
      } finally {
        riskPollInFlight = false;
      }
    }, 250);
  }

  function renderManualRows(state) {
    const rows = Array.isArray(state.manual_rows) ? state.manual_rows : [];
    const body = byId("manual-body");
    clearNode(body);
    rows.forEach((row, index) => {
      const tr = document.createElement("tr");
      const values = [
        String(index + 1),
        row["代號"] || "—",
        row["損益"] === "" || row["損益"] == null ? "由價量推算" : row["損益"],
        row["損益幣別"] || "—",
        row["出場時間"] || "—",
        row["方向"] || "未知",
      ];
      values.forEach((value) => {
        const td = document.createElement("td");
        setText(td, value);
        tr.appendChild(td);
      });
      const actionCell = document.createElement("td");
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "remove-button";
      setText(remove, "移除");
      remove.setAttribute("aria-label", `移除第 ${index + 1} 筆紀錄`);
      remove.addEventListener("click", async () => {
        if (actionRunning || sessionClosed) return;
        const confirmed = await requestConfirmation({
          title: "移除這筆紀錄？",
          message: `第 ${index + 1} 筆會從本次表格移除；原始匯入檔不會被修改。`,
          acceptLabel: "移除這筆",
          trigger: remove,
        });
        if (!confirmed) return;
        runAction(remove, async () => {
          const next = await post("/api/remove-record", {
            index,
            revision: state.manual_revision,
          });
          renderState(next);
          showNotice(`已移除第 ${index + 1} 筆；原始匯入檔沒有被修改。`);
        }, { invalidateResult: true });
      });
      actionCell.appendChild(remove);
      tr.appendChild(actionCell);
      body.appendChild(tr);
    });
    setText(byId("record-count"), `${rows.length} 筆`);
    byId("empty-records").hidden = rows.length !== 0;
  }

  function renderAnalysis(analysis) {
    if (!analysis || localResultInvalidated) {
      invalidateLocalResult();
      return;
    }
    const analysisChanged = currentAnalysisId !== analysis.id || currentAnalysisRevision !== analysis.revision;
    if (analysisChanged) invalidateRiskResult("目前分析尚未執行資金情境。", true, true);
    currentAnalysisId = analysis.id;
    currentAnalysisRevision = analysis.revision;
    localResultInvalidated = false;
    byId("no-analysis").hidden = true;
    byId("analysis-content").hidden = false;

    const result = analysis.result || {};
    const integrity = result.integrity || {};
    const stage = result.stage || {};
    const metrics = analysis.metrics || {};
    const verdict = result.verdict || {};

    const sourceText = result.source ? `來源：${result.source}` : "來源未標示";
    if (analysis.origin === "demo") {
      setText(byId("origin-banner"), `內建示範資料｜${sourceText}｜這不是你的績效`);
    } else if (analysis.origin === "manual") {
      setText(byId("origin-banner"), `本次手動紀錄｜${sourceText}｜分析可能使用本機暫存檔；正常完成或結束會清理`);
    } else {
      setText(byId("origin-banner"), `使用者匯入資料｜${sourceText}｜未經券商身分驗證`);
    }

    const integrityStatus = byId("integrity-status");
    const complete = integrity.complete === true;
    setText(integrityStatus, complete ? "完整性檢查通過" : "有完整性警告");
    integrityStatus.className = `status-pill ${complete ? "ok" : "warn"}`;
    const integrityList = byId("integrity-list");
    clearNode(integrityList);
    appendDefinition(integrityList, "分析接受筆數", formatNumber(metrics.total_trades, 0));
    appendDefinition(integrityList, "拒絕資料列", formatNumber(integrity.rejected_row_count, 0));
    appendDefinition(integrityList, "疑似重複列", formatNumber(integrity.suspected_duplicate_count, 0));
    const reasonList = byId("integrity-reasons");
    clearNode(reasonList);
    const reasons = Array.isArray(integrity.rejected_row_reasons) ? integrity.rejected_row_reasons : [];
    if (!complete && reasons.length === 0) {
      const item = document.createElement("li");
      setText(item, "資料完整性未通過；請展開完整報告查看核心說明。");
      reasonList.appendChild(item);
    }
    reasons.forEach((reason) => {
      const item = document.createElement("li");
      setText(item, uiCopy(reason));
      reasonList.appendChild(item);
    });

    setText(byId("stage-title"), STAGE_TITLES[stage.code] || (/[\u3400-\u9fff]/.test(String(stage.title || "")) ? uiCopy(stage.title) : "核心未提供階段名稱"));
    setText(byId("stage-reason"), uiCopy(stage.reason, "核心未提供階段理由。"));
    const actionList = byId("stage-actions");
    clearNode(actionList);
    const actions = Array.isArray(stage.next_actions) ? stage.next_actions : [];
    actions.forEach((action) => {
      const item = document.createElement("li");
      setText(item, uiCopy(action));
      actionList.appendChild(item);
    });
    const redFlags = Array.isArray(verdict.red_flags) ? verdict.red_flags : [];
    redFlags.forEach((flag) => {
      const item = document.createElement("li");
      setText(item, `警告：${uiCopy(flag.message || flag.code, "核心風險訊號")}`);
      actionList.appendChild(item);
    });
    (Array.isArray(verdict.reasons) ? verdict.reasons : []).forEach((reason) => {
      const item = document.createElement("li");
      setText(item, `核心理由：${uiCopy(reason)}`);
      actionList.appendChild(item);
    });
    (Array.isArray(verdict.advice) ? verdict.advice : []).forEach((advice) => {
      const item = document.createElement("li");
      setText(item, `核心建議：${uiCopy(advice)}`);
      actionList.appendChild(item);
    });

    const currency = metrics.pnl_currency ? ` ${metrics.pnl_currency}` : "";
    const metricList = byId("metrics-list");
    clearNode(metricList);
    appendDefinition(metricList, "交易筆數", formatNumber(metrics.total_trades, 0));
    appendDefinition(metricList, "總淨損益", `${formatNumber(metrics.total_pnl)}${currency}`);
    appendDefinition(metricList, "勝率", formatPercent(metrics.win_rate));
    appendDefinition(metricList, "每筆期望值", `${formatNumber(metrics.expectancy)}${currency}`);
    const visuals = analysis.visuals || {};
    const distribution = visuals.distribution || {};
    const distributionSummary = distribution.summary || {};
    const medianValue = Number.isFinite(distributionSummary.median)
      ? `${formatNumber(distributionSummary.median)}${currency}`
      : uiCopy(distribution.reason, "—");
    appendDefinition(metricList, "每筆中位數", medianValue, "metric-highlight");
    appendDefinition(metricList, "盈虧比", formatNumber(metrics.payoff_ratio));
    appendDefinition(metricList, "獲利因子", formatNumber(metrics.profit_factor));
    appendDefinition(metricList, "最大回撤金額", metrics.sequence_metrics_reliable === false ? "無法計算" : `${formatNumber(metrics.max_drawdown)}${currency}`);
    appendDefinition(metricList, "最大回撤比例", formatPercent(metrics.max_drawdown_pct, metrics.drawdown_pct_reliable !== false));

    const readiness = byId("readiness-list");
    clearNode(readiness);
    (Array.isArray(analysis.readiness) ? analysis.readiness : []).forEach((gate) => {
      const item = document.createElement("li");
      const mark = document.createElement("span");
      mark.className = `gate-mark ${gate.passed ? "pass" : "fail"}`;
      setText(mark, gate.passed ? "✓" : "—");
      mark.setAttribute("aria-label", gate.passed ? "通過" : "未通過");
      const copy = document.createElement("div");
      const strong = document.createElement("strong");
      const detail = document.createElement("small");
      setText(strong, uiCopy(gate.label || gate.key, "研究檢查項目"));
      setText(detail, uiCopy(gate.detail, gate.passed ? "通過" : "目前未通過"));
      copy.append(strong, detail);
      item.append(mark, copy);
      readiness.appendChild(item);
    });

    setText(byId("text-report"), analysis.text_report || "核心未提供文字報告。");
    renderVisuals(visuals);
    updateControls();
  }

  function renderState(state, options = {}) {
    currentState = state;
    setText(byId("mode-badge"), state.mode === "desktop" ? "桌面視窗版" : "本機瀏覽器版");
    const excel = Boolean(state.capabilities && state.capabilities.excel);
    setText(
      byId("excel-note"),
      excel ? "此安裝可讀取 Excel（.xlsx）。" : "此安裝未啟用 Excel；請改用 CSV／JSON，或安裝 excel 選配套件。"
    );
    renderManualRows(state);
    if (options.allowAnalysis === false) {
      invalidateLocalResult();
    } else if (state.analysis) {
      renderAnalysis(state.analysis);
    } else {
      invalidateLocalResult();
    }
    const riskJob = state.risk_simulation;
    if (
      riskJob && riskJob.status === "running" && state.analysis &&
      riskJob.analysis_id === state.analysis.id && riskJob.revision === state.analysis.revision &&
      currentRiskJobId !== riskJob.job_id
    ) {
      currentRiskJobId = riskJob.job_id;
      currentRiskJobStatus = riskJob.cancel_requested ? "cancel_requested" : "running";
      riskPollNonce += 1;
      const nonce = riskPollNonce;
      pollRisk(currentRiskJobId, riskFormRevision, nonce);
    }
    if (state.busy) showNotice("本機正在處理一項工作；狀態仍可查看，其他操作請稍候。");
    updateControls();
  }

  async function refreshState(options = {}) {
    const state = await api("/api/state");
    renderState(state, options);
    return state;
  }

  function fileAsBase64(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onerror = () => reject(new Error("瀏覽器無法讀取這個檔案。"));
      reader.onload = () => {
        const value = String(reader.result || "");
        const comma = value.indexOf(",");
        if (comma < 0) reject(new Error("檔案轉換失敗。"));
        else resolve(value.slice(comma + 1));
      };
      reader.readAsDataURL(file);
    });
  }

  function filenameFromDisposition(header, fallback) {
    if (!header) return fallback;
    const utf = header.match(/filename\*=UTF-8''([^;]+)/i);
    if (utf) {
      try { return decodeURIComponent(utf[1]); } catch (_) { return fallback; }
    }
    return fallback;
  }

  async function downloadArtifact(metadata) {
    const response = await fetch(metadata.download_url, {
      headers: { "X-UI-Token": sessionToken },
      cache: "no-store",
      credentials: "omit",
      referrerPolicy: "no-referrer",
    });
    if (!response.ok) {
      let message = "下載失敗";
      try {
        const payload = await response.json();
        if (payload.error) message = payload.error;
      } catch (_) { /* response was not JSON */ }
      throw new Error(message);
    }
    const blob = await response.blob();
    const name = filenameFromDisposition(response.headers.get("Content-Disposition"), metadata.filename || "download.bin");
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = name;
    anchor.rel = "noopener";
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  async function requestDownload(kind, analysisId = null, simulationId = null) {
    const payload = { kind };
    if (analysisId) payload.analysis_id = analysisId;
    if (simulationId) payload.simulation_id = simulationId;
    const metadata = await post("/api/export", payload);
    await downloadArtifact(metadata);
  }

  function bindNavigation() {
    document.querySelectorAll(".nav-button").forEach((button) => {
      button.addEventListener("click", () => selectSection(button.dataset.section, true));
    });
  }

  function bindSamples() {
    document.querySelectorAll(".sample-button").forEach((button) => {
      button.addEventListener("click", () => {
        runAction(button, async () => {
          localResultInvalidated = false;
          const analysis = await post("/api/analyze", { sample: button.dataset.sample });
          if (currentState) currentState.analysis = analysis;
          renderAnalysis(analysis);
          selectSection("results", true);
          showNotice("已完成內建示範分析；這不是你的交易結果。");
        }, { invalidateResult: true });
      });
    });
  }

  function bindUpload() {
    const input = byId("trade-file");
    input.addEventListener("change", () => {
      const file = input.files && input.files[0];
      setText(byId("file-name"), file ? file.name : "尚未選擇檔案");
      invalidateLocalResult("已選擇新的輸入；分析完成前不沿用舊結果。");
    });
    byId("upload-form").addEventListener("submit", (event) => {
      event.preventDefault();
      const button = event.submitter;
      runAction(button, async () => {
        const file = input.files && input.files[0];
        if (!file) throw new Error("請先選擇交易紀錄檔。");
        if (file.size === 0) throw new Error("這個檔案是空的。");
        if (file.size > MAX_FILE_BYTES) throw new Error("檔案超過 2MiB 上限。");
        const ext = file.name.toLowerCase().split(".").pop();
        if (!["csv", "json", "xlsx"].includes(ext)) throw new Error("僅支援 CSV、JSON 或 XLSX。");
        if (ext === "xlsx" && !(currentState && currentState.capabilities.excel)) {
          throw new Error("此安裝尚未啟用 Excel。請另存 CSV，或安裝 excel 選配套件。");
        }
        const contentBase64 = await fileAsBase64(file);
        localResultInvalidated = false;
        const analysis = await post("/api/analyze", {
          filename: file.name,
          content_base64: contentBase64,
        });
        if (currentState) currentState.analysis = analysis;
        renderAnalysis(analysis);
        selectSection("results", true);
        showNotice("檔案分析完成；原始檔沒有被修改。");
      }, { invalidateResult: true });
    });
  }

  function bindRestoreRecords() {
    const form = byId("restore-records-form");
    const input = byId("records-file");
    const name = byId("records-file-name");
    input.addEventListener("change", () => {
      const file = input.files && input.files[0];
      setText(name, file ? file.name : "尚未選擇 CSV");
    });
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (actionRunning || sessionClosed) return;
      const button = event.submitter;
      const file = input.files && input.files[0];
      if (!file) {
        showError("請先選擇先前下載的逐筆紀錄 CSV。");
        return;
      }
      if (file.size === 0) {
        showError("這個逐筆紀錄 CSV 是空的。");
        return;
      }
      if (file.size > MAX_FILE_BYTES) {
        showError("逐筆紀錄 CSV 超過 2MiB 上限。");
        return;
      }
      if (!file.name.toLowerCase().endsWith(".csv")) {
        showError("逐筆紀錄只接受從這個工作台下載的 CSV。");
        return;
      }
      const rows = currentState && Array.isArray(currentState.manual_rows) ? currentState.manual_rows : [];
      if (rows.length) {
        const confirmed = await requestConfirmation({
          title: "取代本次表格？",
          message: `載入的 CSV 會取代目前 ${rows.length} 筆本次紀錄。需要保留請先下載；原始 CSV 不會被修改。`,
          acceptLabel: "載入並取代",
          trigger: button,
        });
        if (!confirmed) return;
      }
      runAction(button, async () => {
        invalidateLocalResult("正在載入先前紀錄；完成前不沿用舊結果。");
        const contentBase64 = await fileAsBase64(file);
        const next = await post("/api/import-records", {
          filename: file.name,
          content_base64: contentBase64,
        });
        renderState(next, { allowAnalysis: false });
        input.value = "";
        setText(name, "尚未選擇 CSV");
        const restored = Array.isArray(next.manual_rows) ? next.manual_rows.length : 0;
        showNotice(`已載入 ${restored} 筆逐筆紀錄；原始 CSV 沒有被修改。`);
      }, { invalidateResult: true });
    });
  }

  function bindRecordForm() {
    const form = byId("record-form");
    form.addEventListener("input", () => {
      invalidateLocalResult("手動輸入已變更；請新增並重新分析目前紀錄。");
    });
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      const button = event.submitter;
      runAction(button, async () => {
        if (!form.reportValidity()) throw new Error("請補齊標示星號的欄位。");
        const data = Object.fromEntries(new FormData(form).entries());
        const next = await post("/api/record", data);
        renderState(next);
        form.reset();
        showNotice("已加入本次表格；分析可能使用本機暫存檔，正常完成或結束會清理，非預期中止可能留下系統暫存。需要保留請下載紀錄 CSV。");
      }, { invalidateResult: true });
    });

    byId("analyze-records").addEventListener("click", (event) => {
      runAction(event.currentTarget, async () => {
        localResultInvalidated = false;
        const analysis = await post("/api/analyze-records", {});
        if (currentState) currentState.analysis = analysis;
        renderAnalysis(analysis);
        selectSection("results", true);
        showNotice("本次手動紀錄分析完成。");
      }, { invalidateResult: true });
    });

    byId("download-records").addEventListener("click", (event) => {
      runAction(event.currentTarget, async () => {
        await requestDownload("records");
        showNotice("已要求下載目前手動紀錄 CSV。");
      });
    });
    byId("download-template").addEventListener("click", (event) => {
      runAction(event.currentTarget, async () => {
        await requestDownload("template");
        showNotice("已要求下載空白 CSV 範本。");
      });
    });
  }

  function bindResultDownloads() {
    document.querySelectorAll(".analysis-download").forEach((button) => {
      button.addEventListener("click", () => {
        runAction(button, async () => {
          if (!currentAnalysisId || localResultInvalidated) throw new Error("目前沒有可下載的分析結果。");
          await requestDownload(button.dataset.kind, currentAnalysisId, currentSimulationId);
          showNotice("已要求下載目前這一版分析產物。");
        });
      });
    });
  }

  function bindRiskSimulation() {
    const form = byId("risk-form");
    const kind = byId("risk-threshold-kind");
    const threshold = byId("risk-threshold-value");

    function updateThresholdLabel() {
      if (kind.value === "remaining_amount") {
        setText(byId("risk-threshold-label"), "剩餘固定金額");
        threshold.removeAttribute("max");
      } else if (kind.value === "loss_fraction") {
        setText(byId("risk-threshold-label"), "虧損本金（%）");
        threshold.max = "100";
      } else {
        setText(byId("risk-threshold-label"), "剩餘本金（%）");
        threshold.max = "100";
      }
      threshold.min = "0";
    }

    form.addEventListener("input", () => {
      invalidateRiskResult("情境參數已變更；舊結果已停止顯示與匯出。", true);
    });
    kind.addEventListener("change", () => {
      updateThresholdLabel();
      invalidateRiskResult("門檻意思已變更；舊結果已停止顯示與匯出。", true);
    });
    updateThresholdLabel();

    form.addEventListener("submit", (event) => {
      event.preventDefault();
      const button = event.submitter;
      runAction(button, async () => {
        if (!currentAnalysisId || !currentAnalysisRevision || localResultInvalidated) {
          throw new Error("目前沒有可綁定的分析結果。");
        }
        if (!form.reportValidity()) throw new Error("請補齊資金情境參數。");
        const data = Object.fromEntries(new FormData(form).entries());
        const startEquity = Number(data.start_equity);
        const rawThreshold = Number(data.threshold_value);
        const futureTrades = Number(data.future_trades);
        const paths = Number(data.paths);
        if (!Number.isFinite(startEquity) || startEquity <= 0) throw new Error("起始資金必須是大於 0 的有限數字。");
        if (!Number.isFinite(rawThreshold) || rawThreshold < 0) throw new Error("門檻數值必須是非負有限數字。");
        if (!Number.isInteger(futureTrades) || futureTrades < 1 || futureTrades > 1000) throw new Error("未來交易筆數必須是 1 到 1,000 的整數。");
        if (!Number.isInteger(paths) || paths < 100 || paths > 5000) throw new Error("模擬路徑數必須是 100 到 5,000 的整數。");
        if (futureTrades * paths > 1000000) throw new Error("交易筆數 × 路徑數不可超過 1,000,000。");
        const fractionKind = data.threshold_kind === "remaining_fraction" || data.threshold_kind === "loss_fraction";
        if (fractionKind && rawThreshold > 100) throw new Error("比例不可超過 100%。");
        const apiThreshold = fractionKind ? rawThreshold / 100 : rawThreshold;
        const currency = String(data.currency || "").trim().toUpperCase();
        if (!currency) throw new Error("目前紀錄沒有已確認的結算幣別。");

        clearRiskRendered();
        setText(byId("risk-status"), "正在建立有界資金情境……");
        byId("risk-progress").hidden = false;
        byId("risk-progress").value = 0;
        const formRevision = riskFormRevision;
        const job = await post("/api/risk-simulation", {
          analysis_id: currentAnalysisId,
          revision: currentAnalysisRevision,
          start_equity: startEquity,
          currency,
          threshold_kind: data.threshold_kind,
          threshold_value: apiThreshold,
          future_trades: futureTrades,
          paths,
        });
        currentRiskJobId = job.job_id;
        currentRiskJobStatus = "running";
        if (currentState) currentState.busy = true;
        riskPollNonce += 1;
        const nonce = riskPollNonce;
        pollRisk(currentRiskJobId, formRevision, nonce);
        updateControls();
      });
    });

    byId("cancel-risk").addEventListener("click", async () => {
      if (!currentRiskJobId || currentRiskJobStatus !== "running" || sessionClosed) return;
      currentRiskJobStatus = "cancel_requested";
      updateControls();
      setText(byId("risk-status"), "正在取消資金情境……");
      try {
        await post("/api/cancel-risk-simulation", { job_id: currentRiskJobId });
      } catch (cancelError) {
        showError(cancelError && cancelError.message ? cancelError.message : "無法送出取消要求。");
      }
    });

    byId("go-paper").addEventListener("click", () => selectSection("paper", true));
  }

  function bindScanner() {
    const text = byId("scan-text");
    text.addEventListener("input", () => setText(byId("scan-count"), `${text.value.length.toLocaleString("zh-TW")} / 20,000`));
    byId("scan-form").addEventListener("submit", (event) => {
      event.preventDefault();
      runAction(event.submitter, async () => {
        if (!text.value.trim()) throw new Error("請先貼上要檢查的文字。");
        const output = await post("/api/scan", { text: text.value });
        const result = output.result || {};
        byId("scan-result").hidden = false;
        setText(byId("scan-level"), `風險等級：${result.risk_level || "資訊不足"}`);
        const discussion = byId("scan-discussion");
        setText(discussion, result.is_discussion ? "可能是討論／求證語境" : "一般訊息語境");
        discussion.className = `status-pill ${result.is_discussion ? "ok" : "warn"}`;
        setText(byId("scan-headline"), result.headline || "核心未提供摘要。");
        const hits = byId("scan-hits");
        clearNode(hits);
        const found = Array.isArray(result.hits) ? result.hits : [];
        if (!found.length) {
          const item = document.createElement("li");
          setText(item, "沒有命中已知話術特徵；這不等於對方一定安全。");
          hits.appendChild(item);
        }
        found.forEach((hit) => {
          const item = document.createElement("li");
          const parts = [hit.matched_text || hit.phrase, hit.reason, hit.excerpt].filter(Boolean);
          setText(item, parts.join("｜"));
          hits.appendChild(item);
        });
        const advice = byId("scan-advice");
        clearNode(advice);
        (Array.isArray(result.advice) ? result.advice : []).forEach((line) => {
          const item = document.createElement("li");
          setText(item, line);
          advice.appendChild(item);
        });
        setText(byId("scan-report"), output.text_report || "核心未提供掃描報告。");
        showNotice("訊息檢查完成；結果沒有使用未校準的詐騙機率百分比。");
      });
    });
  }

  function bindScaffold() {
    const form = byId("scaffold-form");
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      runAction(event.submitter, async () => {
        if (!form.reportValidity()) throw new Error("請填專案名稱與至少一個標的代號。");
        const data = Object.fromEntries(new FormData(form).entries());
        const symbols = String(data.symbols || "").split(",").map((item) => item.trim()).filter(Boolean);
        if (!symbols.length) throw new Error("請填至少一個標的代號。");
        const artifact = await post("/api/scaffold", {
          project_name: data.project_name,
          symbols,
          market: data.market,
        });
        await downloadArtifact(artifact);
        showNotice("已要求下載紙上交易專案 ZIP；它尚未執行，也沒有連接真實券商。");
      });
    });
  }

  function bindQuit() {
    byId("quit-button").addEventListener("click", async () => {
      if (sessionClosed || actionRunning) return;
      const confirmed = await requestConfirmation({
        title: "結束本次使用？",
        message: "分析可能使用本機暫存檔；正常結束會清理，但非預期失敗或強制停止可能留下系統暫存。需要保留的報告或 CSV 請先下載。",
        acceptLabel: "送出結束要求",
        trigger: byId("quit-button"),
      });
      if (!confirmed) return;
      actionRunning = true;
      updateControls();
      let shutdownAcknowledged = false;
      try {
        await post("/api/shutdown", {});
        shutdownAcknowledged = true;
      } catch (error) {
        showError(error && error.message ? error.message : "無法結束本次使用。");
      } finally {
        if (shutdownAcknowledged) {
          sessionClosed = true;
          try { clearSensitiveWorkspace(); } catch (_) { /* keep the closed status available */ }
          byId("closed-screen").hidden = false;
        }
        actionRunning = false;
        updateControls();
      }
    });
  }

  async function initialize() {
    bindConfirmation();
    bindNavigation();
    bindSamples();
    bindUpload();
    bindRestoreRecords();
    bindRecordForm();
    bindResultDownloads();
    bindRiskSimulation();
    bindScanner();
    bindScaffold();
    bindQuit();
    try {
      await refreshState();
      clearMessages();
    } catch (error) {
      showError(error && error.message ? error.message : "無法連線到本機工作階段。");
    }
  }

  initialize();
})();
