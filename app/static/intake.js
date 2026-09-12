export function openSmartImport({
  modal,
  request,
  jsonRequest,
  esc,
  money,
  sourceName,
  navigate,
}) {
  const d = modal(
    "导入账单",
    `<div class="steps" aria-label="导入进度"><span class="active">1 上传</span><span>2 预览</span><span>3 确认</span></div>
    <div class="intake-drop" tabindex="0" aria-label="拖入或粘贴账单文件"><strong>拖入账单，或在这里粘贴文件</strong><p>自动识别支付宝、微信、建设银行、农业银行、招商银行</p><label class="button primary">选择文件<input id="intake-files" type="file" multiple accept=".csv,.xls,.xlsx,.pdf,.zip"></label><label class="button">选择文件夹<input id="intake-folder" type="file" webkitdirectory multiple></label><small>CSV / Excel / PDF / ZIP · 可以混合上传</small></div>
    <div id="intake-preview" aria-live="polite"></div><details id="intake-history"><summary>最近导入记录</summary><div></div></details>`,
    { wide: true, id: "import-dialog" },
  );
  const $ = (s) => d.querySelector(s);
  const target = $("#intake-preview");
  let files = [],
    plan = null,
    version = 0,
    closed = false,
    busy = false;
  const sources = {},
    accounts = {},
    decisions = {};
  const pages = {};
  const pageSize = 25;
  d.addEventListener("close", () => {
    closed = true;
    version++;
    files = [];
    plan = null;
  });
  function step(n) {
    d.querySelectorAll(".steps span").forEach((el, i) => {
      el.classList.toggle("active", i === n);
      if (i === n) el.setAttribute("aria-current", "step");
      else el.removeAttribute("aria-current");
    });
  }
  function error(e) {
    target.innerHTML = `<p class="error" role="alert">${esc(e.message || e)}</p><button data-intake="retry">重新预览</button>`;
  }
  function choose(selected) {
    if (busy) return;
    files = Array.from(selected);
    plan = null;
    Object.keys(sources).forEach((k) => delete sources[k]);
    Object.keys(accounts).forEach((k) => delete accounts[k]);
    Object.keys(decisions).forEach((k) => delete decisions[k]);
    Object.keys(pages).forEach((k) => delete pages[k]);
    preview();
  }
  $("#intake-files").addEventListener("change", (e) => choose(e.target.files));
  $("#intake-folder").addEventListener("change", (e) => choose(e.target.files));
  d.addEventListener("dragover", (e) => {
    e.preventDefault();
    $(".intake-drop").classList.add("dragging");
  });
  d.addEventListener("dragleave", () =>
    $(".intake-drop").classList.remove("dragging"),
  );
  d.addEventListener("drop", (e) => {
    e.preventDefault();
    $(".intake-drop").classList.remove("dragging");
    if (e.dataTransfer.files.length) choose(e.dataTransfer.files);
  });
  d.addEventListener("paste", (e) => {
    if (e.clipboardData.files.length) {
      e.preventDefault();
      choose(e.clipboardData.files);
    }
  });
  $(".intake-drop").focus();
  $("#intake-history").addEventListener("toggle", async (e) => {
    if (!e.target.open) return;
    const box = e.target.querySelector("div");
    try {
      const history = await request("/api/intake/history");
      box.innerHTML = history.length
        ? history
            .map(
              (h) =>
                `<details><summary>${esc(h.filename)} · ${h.row_count} 条</summary><button data-load-batch="${h.id}">查看原始记录</button><div></div></details>`,
            )
            .join("")
        : "<p>还没有导入记录。</p>";
    } catch (e) {
      box.textContent = e.message;
    }
  });
  const encode = (file) =>
    new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result).split(",")[1]);
      reader.onerror = () => reject(new Error("无法读取文件，请重试"));
      reader.readAsDataURL(file);
    });
  async function preview() {
    const epoch = ++version;
    plan = null;
    if (!files.length) return;
    if (
      files.length > 100 ||
      files.some((f) => f.size > 25 * 1024 * 1024) ||
      files.reduce((n, f) => n + f.size, 0) > 100 * 1024 * 1024
    )
      return error("每批最多 100 个文件、100 MB，单个文件最多 25 MB");
    const passwords = {};
    d.querySelectorAll("[data-password]").forEach((el) => {
      passwords[el.dataset.password] = el.value;
      el.value = "";
    });
    step(0);
    target.innerHTML = '<p class="busy">正在识别来源、账户并检查重复…</p>';
    try {
      const payload = {
        files: await Promise.all(
          files.map(async (f, i) => ({
            filename: f.name,
            content_base64: await encode(f),
            source_type: sources[i] || null,
            password: passwords[i] || null,
          })),
        ),
      };
      const result = await jsonRequest("/api/intake/preview", "POST", payload);
      if (closed || version !== epoch) return;
      plan = result;
      render();
    } catch (e) {
      if (!closed && version === epoch) error(e);
    }
  }
  function render() {
    step(1);
    const count = plan.counts;
    const actionNames = {
      new: "新增",
      supplement: "已有交易 · 补充证据",
      record: "保留原始记录",
      duplicate_file: "重复记录 · 跳过，不重复入账",
      error: "需要核对",
      ambiguous: "选择匹配",
    };
    const natureNames = {
      ordinary: "收支",
      neutral: "账户转移 / 理财，不计个人收支",
      refund: "退款，不计收入",
      non_posted: "已关闭，不计金额",
    };
    target.innerHTML =
      `<p class="notice">预计新增 ${count.new || 0} 笔，补充 ${count.supplement || 0} 条证据，保留 ${count.record || 0} 条非收付记录，跳过 ${count.duplicate_file || 0} 条已导入记录。</p>` +
      plan.documents
        .map((doc, i) => {
          const pageCount = Math.max(1, Math.ceil(doc.rows.length / pageSize));
          const page = pages[i] = Math.min(pages[i] || 0, pageCount - 1);
          const visibleRows = doc.rows.slice(page * pageSize, (page + 1) * pageSize);
          const grouping = new Map();
          doc.rows.forEach((r) => {
            if (r.account) grouping.set(r.detected_account_identity, r.account);
          });
          return `<article class="import-file"><h3>${esc(doc.filename)} <span class="badge">${doc.duplicate ? "已导入" : `${doc.rows.length} 条`}</span></h3>
        <label>来源<select data-source="${i}"><option value="">自动识别</option>${["alipay", "wechat", "ccb", "abc", "cmb"].map((s) => `<option value="${s}" ${doc.source_type === s ? "selected" : ""}>${sourceName(s)}</option>`).join("")}</select></label>
        ${doc.error ? `<p class="error">${esc(doc.error)}</p>${/ZIP|密码|password/i.test(doc.error) ? `<label>压缩包密码<input type="password" data-password="${i}" autocomplete="off"></label><button data-intake="retry">解锁并预览</button>` : ""}` : ""}
        ${Array.from(grouping)
          .map(
            ([identity, a]) =>
              `<label>资金账户<select data-account="${esc(identity)}">${plan.accounts.map((o) => `<option value="${esc(o.identity)}" ${a.identity === o.identity ? "selected" : ""}>${esc(o.display_name)}${o.owner ? ` · ${esc(o.owner)}` : ""}</option>`).join("")}</select></label>`,
          )
          .join("")}
        ${doc.rows.length ? `<div class="intake-table"><table><thead><tr><th>日期 / 时间</th><th>交易方与说明</th><th>金额</th><th>处理结果</th></tr></thead><tbody>${visibleRows.map((r, offset) => `<tr><td>${esc(r.occurred_at?.replace("T", " ").slice(0, r.time_precision === "day" ? 10 : 19) || "待核对")}</td><td>${esc(r.merchant || "")}<small>${esc(r.note || "")}</small><small>${esc(r.account?.display_name || "")} · ${esc(natureNames[r.nature] || r.disposition)}</small></td><td class="money">${r.amount_minor == null ? "—" : money(r.amount_minor / 100)}</td><td>${actionNames[r.action] || esc(r.action)}${r.error ? `<p class="error">${esc(r.error)}</p>` : ""}${r.action === "ambiguous" ? `<select data-decision="${r.row_id}"><option value="">请选择</option><option value="new">保留为新交易</option>${r.candidates.map((id) => `<option value="match:${id}">补充流水 #${id}</option>`).join("")}</select>` : ""}<details data-raw-doc="${i}" data-raw-row="${page * pageSize + offset}"><summary>原始字段</summary></details></td></tr>`).join("")}</tbody></table></div><div class="pagination"><span>共 ${doc.rows.length} 条 · ${page * pageSize + 1}–${Math.min((page + 1) * pageSize, doc.rows.length)}</span><button data-intake-page="${i}" data-step="-1" ${page === 0 ? "disabled" : ""}>上一页</button><span>第 ${page + 1} / ${pageCount} 页</span><button data-intake-page="${i}" data-step="1" ${page + 1 >= pageCount ? "disabled" : ""}>下一页</button>${doc.rows.some(r => r.action === "error" || r.action === "ambiguous") ? `<button data-intake-issue="${i}">定位待核对记录</button>` : ""}</div>` : ""}</article>`;
        })
        .join("") +
      `<div class="intake-confirm"><p>${plan.can_confirm ? "核对无误后确认，重复证据不会重复计入金额。" : "请在本页处理标出的项目，再确认。"}</p><button class="primary" data-intake="confirm" ${plan.can_confirm ? "" : "disabled"}>确认导入</button><button data-intake="refresh">刷新预览</button></div>`;
  }
  // Collapsed evidence must not create hundreds of JSON blocks in the DOM.
  d.addEventListener("toggle", (e) => {
    const details = e.target;
    if (!details.open || details.dataset.rawDoc === undefined || details.querySelector("pre")) return;
    const row = plan?.documents[Number(details.dataset.rawDoc)]?.rows[Number(details.dataset.rawRow)];
    if (!row) return;
    const pre = document.createElement("pre");
    pre.textContent = JSON.stringify(row.raw, null, 2);
    details.append(pre);
  }, true);
  d.addEventListener("click", (e) => {
    const b = e.target.closest("[data-intake-page],[data-intake-issue]");
    if (!b || b.disabled || !plan || busy) return;
    const index = Number(b.dataset.intakePage ?? b.dataset.intakeIssue);
    if (b.dataset.intakeIssue !== undefined) {
      const row = plan.documents[index].rows.findIndex(r => r.action === "error" || r.action === "ambiguous");
      pages[index] = Math.floor(Math.max(0, row) / pageSize);
    } else pages[index] = Math.max(0, (pages[index] || 0) + Number(b.dataset.step));
    render();
    target.querySelectorAll(".import-file")[index]?.scrollIntoView({block: "nearest"});
  });
  async function refresh() {
    if (!plan) return;
    const previous = plan;
    const epoch = ++version;
    plan = null;
    d.querySelectorAll("[data-account],[data-decision]").forEach(
      (el) => (el.disabled = true),
    );
    target.querySelector("[data-intake=confirm]")?.setAttribute("disabled", "");
    try {
      const result = await jsonRequest(
        `/api/intake/${previous.token}/preview`,
        "PUT",
        { accounts, decisions },
      );
      if (!closed && version === epoch) {
        plan = result;
        render();
      }
    } catch (e) {
      if (!closed && version === epoch) error(e);
    }
  }
  d.addEventListener("change", async (e) => {
    if (busy) return;
    if (e.target.dataset.source !== undefined) {
      sources[e.target.dataset.source] = e.target.value;
      await preview();
    }
    if (e.target.dataset.account) {
      accounts[e.target.dataset.account] = e.target.value;
      await refresh();
    }
    if (e.target.dataset.decision) {
      decisions[e.target.dataset.decision] = e.target.value;
      await refresh();
    }
  });
  d.addEventListener("click", async (e) => {
    const button = e.target.closest("[data-intake]");
    if (!button || busy) return;
    const action = button.dataset.intake;
    if (action === "retry") return preview();
    if (action === "refresh") return refresh();
    if (action === "done") {
      d.close();
      navigate("data", "scope=all");
      return;
    }
    if (action !== "confirm" || !plan) return;
    busy = true;
    button.disabled = true;
    step(2);
    d.querySelectorAll("input,select").forEach((el) => (el.disabled = true));
    try {
      const result = await jsonRequest(
        `/api/intake/${plan.token}/confirm`,
        "POST",
        { version: plan.version },
      );
      if (closed) return;
      const c = result.counts;
      const skipped = c.duplicate_file || 0;
      const onlyDuplicates = skipped > 0 && !(c.new || c.supplement || c.record);
      target.innerHTML = `<div class="success" role="status">${onlyDuplicates ? "全部记录已导入，本次未新增入账。" : "导入完成："}新增 ${c.new || 0} 笔，补充 ${c.supplement || 0} 条证据，保留 ${c.record || 0} 条非收付记录，跳过重复 ${skipped} 条。</div><p>${onlyDuplicates ? "检测到与已导入文件内容相同，已跳过；原有流水与证据保持不变。" : "补充证据不会新增一笔交易；重复记录不会重复入账。已关闭交易不计入金额。"}</p><button class="primary" data-intake="done">查看全部流水</button>${result.batch_ids.map((id) => `<details><summary>本次导入记录 · 批次 ${id}</summary><button data-load-batch="${id}">查看原始记录</button><div></div></details>`).join("")}`;
      plan = null;
    } catch (e) {
      if (!closed) {
        step(1);
        button.disabled = false;
        button.insertAdjacentHTML(
          "beforebegin",
          `<p class="error" role="alert">${esc(e.message)}</p>`,
        );
      }
    } finally {
      busy = false;
      d.querySelectorAll("input,select").forEach((el) => (el.disabled = false));
    }
  });
  d.addEventListener("click", async (e) => {
    const b = e.target.closest("[data-load-batch]");
    if (!b) return;
    b.disabled = true;
    try {
      const rows = await request(
        `/api/intake/batches/${b.dataset.loadBatch}/rows`,
      );
      b.nextElementSibling.innerHTML = rows
        .map(
          (r) =>
            `<details><summary>第 ${r.record.row_number} 行 · ${esc({new:"已入账",supplement:"补充证据",non_posted:"已关闭，不计金额",neutral_evidence:"中性原始记录"}[r.disposition] || "原始记录")}</summary><pre>${esc(JSON.stringify(r.record.raw, null, 2))}</pre></details>`,
        )
        .join("");
    } catch (e) {
      b.disabled = false;
      b.nextElementSibling.textContent = e.message;
    }
  });
}
