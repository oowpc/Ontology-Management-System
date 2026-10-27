/* 概念选择器：单选取 / 多选取，异步搜索，无外部依赖。 */
(function () {
  "use strict";

  function make(tag, cls, text) {
    var el = document.createElement(tag);
    if (cls) el.className = cls;
    if (text != null) el.textContent = text;
    return el;
  }

  function Picker(el) {
    this.el = el;
    this.mode = el.dataset.picker || "single";
    this.name = el.dataset.name;
    this.status = el.dataset.status || "";
    this.excludeId = el.dataset.excludeId || "";
    this.placeholder = el.dataset.placeholder || (this.mode === "single" ? "搜索概念…" : "搜索并添加概念…");
    var raw = [];
    try {
      var parsed = JSON.parse(el.dataset.selected || "null");
      if (Array.isArray(parsed)) raw = parsed;
      else if (parsed && parsed.id) raw = [parsed];
    } catch (e) { raw = []; }
    this.selected = raw.filter(function (x) { return x && x.id; });
    this.requestSeq = 0;
    this._build();
    this._bind();
    this._render();
  }

  Picker.prototype._build = function () {
    var self = this;
    this.selectedBox = make("div", "picker-selected");
    this.input = make("input", "picker-input");
    this.input.type = "text";
    this.input.autocomplete = "off";
    this.input.placeholder = this.placeholder;
    this.input.setAttribute("aria-label", this.placeholder);
    this.results = make("div", "picker-results");
    this.hiddenBox = make("div", "picker-hidden");
    this.hiddenBox.style.display = "none";
    this.el.appendChild(this.selectedBox);
    this.el.appendChild(this.input);
    this.el.appendChild(this.hiddenBox);
    this.el.appendChild(this.results);
    this.el._picker = this;
    this.el.classList.add("picker-ready");
  };

  Picker.prototype._bind = function () {
    var self = this;
    this.input.addEventListener("focus", function () { self._search(self.input.value); });
    this.input.addEventListener("input", function () { self._search(self.input.value); });
    this.input.addEventListener("keydown", function (e) {
      if (e.key === "Escape") { self._close(); return; }
      if (e.key === "ArrowDown") {
        e.preventDefault();
        var first = self.results.querySelector(".picker-result");
        if (first) first.focus();
        return;
      }
      if (e.key === "Enter") {
        var first = self.results.querySelector(".picker-result");
        if (first) { e.preventDefault(); first.click(); }
      }
    });
    document.addEventListener("click", function (e) {
      if (!self.el.contains(e.target)) self._close();
    });
  };

  Picker.prototype._render = function () {
    var self = this;
    this.selectedBox.innerHTML = "";
    this.selected.forEach(function (item) {
      var chip = make("span", "picker-chip");
      chip.appendChild(make("span", "", item.name));
      var btn = make("button", "", "×");
      btn.type = "button";
      btn.title = "移除";
      btn.addEventListener("click", function () { self._remove(item.id); });
      chip.appendChild(btn);
      self.selectedBox.appendChild(chip);
    });
    this.hiddenBox.innerHTML = "";
    if (this.mode === "single") {
      // 列表筛选用 omitEmpty：未选择时不提交空字符串（避免 int 参数 422）
      if (this.selected.length || !this.el.dataset.omitEmpty) {
        var h = make("input");
        h.type = "hidden";
        h.name = this.name;
        h.value = this.selected.length ? this.selected[0].id : "";
        this.hiddenBox.appendChild(h);
      }
    } else {
      this.selected.forEach(function (item) {
        var h = make("input");
        h.type = "hidden";
        h.name = self.name;
        h.value = item.id;
        self.hiddenBox.appendChild(h);
      });
    }
    this.input.placeholder = this.mode === "single" && this.selected.length
      ? "更换主父类…" : this.placeholder;
  };

  Picker.prototype._search = function (q) {
    var self = this;
    var seq = ++this.requestSeq;
    var qs = new URLSearchParams();
    qs.set("q", (q || "").trim());
    qs.set("limit", "20");
    if (this.status) qs.set("status", this.status);
    if (this.excludeId) qs.set("exclude_id", this.excludeId);
    this._openLoading();
    fetch("/api/concepts/search?" + qs.toString(), { headers: { "Accept": "application/json" } })
      .then(function (r) { return r.ok ? r.json() : { items: [] }; })
      .then(function (data) {
        if (seq !== self.requestSeq) return;
        self._renderResults(data.items || []);
      })
      .catch(function () {
        if (seq === self.requestSeq) self._renderResults([], "搜索失败，请重试");
      });
  };

  Picker.prototype._openLoading = function () {
    this.results.innerHTML = '<div class="picker-empty">搜索中…</div>';
    this.results.classList.add("open");
  };

  Picker.prototype._renderResults = function (items, emptyText) {
    var self = this;
    this.results.innerHTML = "";
    if (!items.length) {
      this.results.appendChild(make("div", "picker-empty", emptyText || "无匹配概念"));
      this.results.classList.add("open");
      return;
    }
    items.forEach(function (item) {
      if (self.selected.some(function (x) { return x.id === item.id; })) return;
      var row = make("div", "picker-result");
      row.tabIndex = 0;
      row.appendChild(make("span", "picker-result-name", item.name));
      row.appendChild(make("span", "picker-result-meta", item.domain || "通用"));
      row.addEventListener("click", function () { self._select(item); });
      row.addEventListener("keydown", function (e) {
        if (e.key === "Enter") { e.preventDefault(); self._select(item); }
      });
      self.results.appendChild(row);
    });
    if (!this.results.children.length) {
      this.results.appendChild(make("div", "picker-empty", "已全部选中"));
    }
    this.results.classList.add("open");
  };

  Picker.prototype._close = function () {
    this.results.classList.remove("open");
  };

  Picker.prototype._select = function (item) {
    var old = this.mode === "single" ? (this.selected[0] || null) : null;
    if (this.mode === "single") {
      this.selected = [item];
    } else if (!this.selected.some(function (x) { return x.id === item.id; })) {
      this.selected.push(item);
    }
    this.input.value = "";
    this._render();
    this._close();
    if (this.mode === "single") {
      this.el.dispatchEvent(new CustomEvent("picker:change", {
        bubbles: true, detail: { old: old, new: item }
      }));
    }
  };

  Picker.prototype._remove = function (id) {
    var old = this.selected[0] || null;
    this.selected = this.selected.filter(function (x) { return x.id !== id; });
    this._render();
    if (this.mode === "single" && old && old.id === id) {
      this.el.dispatchEvent(new CustomEvent("picker:change", {
        bubbles: true, detail: { old: old, new: this.selected[0] || null }
      }));
    }
  };

  Picker.prototype.set = function (id, name) {
    id = parseInt(id, 10);
    if (!id) return;
    this.selected = [{ id: id, name: name || String(id) }];
    this._render();
  };

  Picker.prototype.add = function (id, name) {
    id = parseInt(id, 10);
    if (!id || this.selected.some(function (x) { return x.id === id; })) return;
    this.selected.push({ id: id, name: name });
    this._render();
  };

  document.addEventListener("picker:change", function (e) {
    var el = e.target;
    var targetSel = el.dataset.oldPrimaryTarget;
    if (!targetSel) return;
    var target = document.querySelector(targetSel);
    if (!target || !target._picker) return;
    var old = e.detail.old, nw = e.detail.new;
    // 改主父类时，把旧主父类自动降为“其他父类”
    if (old && (!nw || old.id !== nw.id)) target._picker.add(old.id, old.name);
  });

  function init() {
    document.querySelectorAll(".picker").forEach(function (el) {
      if (!el._picker) new Picker(el);
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  window.initConceptPickers = init;
})();
