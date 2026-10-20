/* M2：提交页实时查重 + 审核页小交互。无构建链、无外部依赖。 */
(function () {
  "use strict";

  function debounce(fn, ms) {
    var t;
    return function () {
      clearTimeout(t);
      var args = arguments, self = this;
      t = setTimeout(function () { fn.apply(self, args); }, ms);
    };
  }

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function splitAliases(raw) {
    return raw.split(/[,，、;；\n]+/).map(function (s) { return s.trim(); }).filter(Boolean);
  }

  function checkSimilar() {
    var nameEl = document.querySelector("#f-name");
    var panel = document.querySelector("#similar-panel");
    if (!nameEl || !panel) return;
    var name = nameEl.value.trim();
    if (!name) { panel.innerHTML = '<span class="muted">输入名称后自动查重</span>'; return; }
    var aliases = splitAliases(document.querySelector("#f-aliases").value);
    var pid = document.querySelector("#f-parent").value;
    var qs = new URLSearchParams();
    qs.set("name", name);
    aliases.forEach(function (a) { qs.append("aliases", a); });
    if (pid) qs.set("parent_id", pid);
    fetch("/api/similarity/check?" + qs.toString(), { headers: { "Accept": "application/json" } })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) { if (data) renderSimilar(panel, data); })
      .catch(function () { /* 网络异常时静默；服务端在提交时仍会查重 */ });
  }

  function renderSimilar(panel, data) {
    var items = data.items || [];
    if (!items.length) {
      panel.innerHTML = '<span class="muted">未发现相似概念，可以提交</span>';
      return;
    }
    var blocked = items.some(function (i) { return i.score >= data.block_threshold; });
    var html = "";
    if (blocked) {
      html += '<div class="flash err">存在高相似概念：该名称无法直接入库；提交后将由审核员重点复核（建议考虑「合并」）。</div>';
    } else {
      html += '<div class="flash ok">未发现高相似概念；以下为中低相似，供审核参考。</div>';
    }
    html += '<table class="data"><thead><tr><th>概念</th><th>别名</th><th>父类</th><th>状态</th><th class="num">相似度</th><th>命中</th></tr></thead><tbody>';
    items.forEach(function (i) {
      var cls = i.score >= data.block_threshold ? ' class="row-warn"' : "";
      html += "<tr" + cls + "><td>" + esc(i.name) + "</td>"
        + '<td class="muted">' + esc((i.aliases || []).join("、")) + "</td>"
        + "<td>" + esc(i.parent_name || "—") + "</td>"
        + "<td>" + esc(i.status_label || i.status) + "</td>"
        + '<td class="num">' + Math.round(i.score * 100) + "%</td>"
        + '<td class="muted">' + esc(i.matched_on) + "「" + esc(i.matched_value || "") + "」</td></tr>";
    });
    html += "</tbody></table>";
    panel.innerHTML = html;
  }

  document.addEventListener("DOMContentLoaded", function () {
    var form = document.querySelector("#submit-form");
    if (form) {
      var handler = debounce(checkSimilar, 300);
      ["#f-name", "#f-aliases", "#f-parent"].forEach(function (sel) {
        var el = document.querySelector(sel);
        if (el) { el.addEventListener("input", handler); el.addEventListener("change", handler); }
      });
      checkSimilar();
    }
    var llmBtn = document.querySelector("#llm-dedup-btn");
    if (llmBtn) {
      llmBtn.addEventListener("click", function () {
        var name = document.querySelector("#f-name").value.trim();
        if (!name) { alert("请先输入概念名称"); return; }
        var status = document.querySelector("#llm-dedup-status");
        var panel = document.querySelector("#llm-panel");
        status.textContent = "复核中…";
        llmBtn.disabled = true;
        fetch("/api/llm/dedup", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            name: name,
            aliases: splitAliases(document.querySelector("#f-aliases").value),
            parent_id: parseInt(document.querySelector("#f-parent").value, 10) || null
          })
        }).then(function (r) { return r.json(); })
          .then(function (data) {
            llmBtn.disabled = false;
            if (!data.llm) { status.textContent = "LLM 未配置或调用失败"; return; }
            status.textContent = "";
            renderLlmDedup(panel, data.items);
          })
          .catch(function () { llmBtn.disabled = false; status.textContent = "调用失败"; });
      });
    }
  });

  var VERDICT_LABELS = {
    same: ["同义·建议合并", "var(--bad)"],
    near: ["近义·需注意", "var(--warn)"],
    distinct: ["不同概念", "var(--gray)"]
  };

  function renderLlmDedup(panel, items) {
    panel.style.display = "block";
    if (!items.length) {
      panel.innerHTML = '<span class="muted">LLM 未发现相关概念</span>';
      return;
    }
    var html = "<strong>LLM 语义复核</strong>"
      + '<table class="data"><thead><tr><th>概念</th><th>判定</th><th>理由</th></tr></thead><tbody>';
    items.forEach(function (i) {
      var v = VERDICT_LABELS[i.verdict] || [i.verdict, "var(--gray)"];
      html += "<tr><td>" + esc(i.name) + '</td><td><span class="badge" style="color:#fff;background:'
        + v[1] + '">' + v[0] + '</span></td><td class="muted">' + esc(i.reason) + "</td></tr>";
    });
    panel.innerHTML = html + "</tbody></table>";
  }

  // 审核页：LLM 审核建议（填意见草稿 + 预选合并目标）
  window.llmSuggest = function (btn, sid) {
    var card = btn.closest(".review-card");
    var rationale = card.querySelector(".llm-rationale");
    btn.disabled = true;
    rationale.textContent = "分析中…";
    fetch("/api/llm/review-suggest", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ submission_id: sid })
    }).then(function (r) { return r.json(); })
      .then(function (data) {
        btn.disabled = false;
        if (!data.llm) { rationale.textContent = "LLM 未配置或调用失败"; return; }
        var form = card.querySelector("form.review-actions");
        form.querySelector("textarea[name=comment]").value = data.comment || "";
        if (data.merge_target_id) {
          form.querySelector("select[name=merge_target_id]").value = String(data.merge_target_id);
        }
        var label = { approve: "建议通过", reject: "建议驳回", merge: "建议合并" }[data.action] || data.action;
        rationale.textContent = label + "：" + (data.rationale || "");
      })
      .catch(function () { btn.disabled = false; rationale.textContent = "调用失败"; });
  };

  // 审核页：合并前确认目标概念
  window.confirmMerge = function (btn) {
    var form = btn.closest("form");
    var sel = form.querySelector('select[name="merge_target_id"]');
    if (!sel.value) { alert("请选择合并目标概念"); return false; }
    return confirm("确认合并到「" + sel.options[sel.selectedIndex].text + "」？新概念将被停用，TMP 指向合并目标。");
  };
})();
