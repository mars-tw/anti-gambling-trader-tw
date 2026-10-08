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

  let currentState = null;
  let currentAnalysisId = null;
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
      "form button, .sample-button, .analysis-download, #download-template, #download-records, #analyze-records"
    );
  }

  function updateControls() {
    const hasRows = Boolean(currentState && currentState.manual_rows.length);
    const hasAnalysis = Boolean(currentAnalysisId && !localResultInvalidated);
    actionButtons().forEach((button) => {
      let disabled = actionRunning || sessionClosed;
      if (button.id === "analyze-records") disabled = disabled || !hasRows;
      if (button.classList.contains("analysis-download")) disabled = disabled || !hasAnalysis;
      button.disabled = disabled;
    });
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
    currentAnalysisId = null;
    localResultInvalidated = true;
    byId("analysis-content").hidden = true;
    byId("no-analysis").hidden = false;
    const message = byId("no-analysis").querySelector("p:last-child");
    if (message && reason) setText(message, reason);
    updateControls();
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
    currentAnalysisId = analysis.id;
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
      setText(item, reason);
      reasonList.appendChild(item);
    });

    setText(byId("stage-title"), STAGE_TITLES[stage.code] || (/[\u3400-\u9fff]/.test(String(stage.title || "")) ? stage.title : "核心未提供階段名稱"));
    setText(byId("stage-reason"), stage.reason || "核心未提供階段理由。");
    const actionList = byId("stage-actions");
    clearNode(actionList);
    const actions = Array.isArray(stage.next_actions) ? stage.next_actions : [];
    actions.forEach((action) => {
      const item = document.createElement("li");
      setText(item, action);
      actionList.appendChild(item);
    });
    const redFlags = Array.isArray(verdict.red_flags) ? verdict.red_flags : [];
    redFlags.forEach((flag) => {
      const item = document.createElement("li");
      setText(item, `警告：${flag.message || flag.code || "核心風險訊號"}`);
      actionList.appendChild(item);
    });
    (Array.isArray(verdict.reasons) ? verdict.reasons : []).forEach((reason) => {
      const item = document.createElement("li");
      setText(item, `核心理由：${reason}`);
      actionList.appendChild(item);
    });
    (Array.isArray(verdict.advice) ? verdict.advice : []).forEach((advice) => {
      const item = document.createElement("li");
      setText(item, `核心建議：${advice}`);
      actionList.appendChild(item);
    });

    const currency = metrics.pnl_currency ? ` ${metrics.pnl_currency}` : "";
    const metricList = byId("metrics-list");
    clearNode(metricList);
    appendDefinition(metricList, "交易筆數", formatNumber(metrics.total_trades, 0));
    appendDefinition(metricList, "總淨損益", `${formatNumber(metrics.total_pnl)}${currency}`);
    appendDefinition(metricList, "勝率", formatPercent(metrics.win_rate));
    appendDefinition(metricList, "每筆期望值", `${formatNumber(metrics.expectancy)}${currency}`);
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
      setText(strong, gate.label || gate.key);
      setText(detail, gate.detail || (gate.passed ? "通過" : "目前未通過"));
      copy.append(strong, detail);
      item.append(mark, copy);
      readiness.appendChild(item);
    });

    setText(byId("text-report"), analysis.text_report || "核心未提供文字報告。");
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

  async function requestDownload(kind, analysisId = null) {
    const payload = { kind };
    if (analysisId) payload.analysis_id = analysisId;
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
          await requestDownload(button.dataset.kind, currentAnalysisId);
          showNotice("已要求下載目前這一版分析產物。");
        });
      });
    });
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
