/**
 * Backup Core — 两级备份控制台：总览 / 历史 / 设置 / 检查 / 闸门。
 * 只用宿主确认暴露的 antd 组件（Button/Tabs/Tag/Space/Input/InputNumber/
 * Switch/Empty/message），布局用原生 div/table，避免精简 antd 缺组件白屏。
 */
(function () {
  var QwenPaw = window.QwenPaw;
  if (!QwenPaw || !QwenPaw.host) {
    console.error("[backup-core] window.QwenPaw not ready");
    return;
  }
  var host = QwenPaw.host;
  var React = host.React;
  var antd = host.antd;
  if (!React || !antd) {
    console.error("[backup-core] Host React and antd are required");
    return;
  }
  var h = React.createElement;
  var useState = React.useState;
  var useEffect = React.useEffect;
  var useHostTheme = typeof host.useTheme === "function" ? host.useTheme : function () { return { isDark: false }; };

  var Button = antd.Button, Tabs = antd.Tabs, Tag = antd.Tag, Space = antd.Space;
  var Input = antd.Input, InputNumber = antd.InputNumber, Switch = antd.Switch;
  var Empty = antd.Empty, message = antd.message;

  var API = "/backup-core";
  function apiUrl(path) {
    return typeof host.getApiUrl === "function" ? host.getApiUrl(API + path) : API + path;
  }
  function req(method, path, body) {
    var opt = { method: method, headers: { "Content-Type": "application/json" } };
    var token = host.getApiToken ? host.getApiToken() : "";
    if (token) opt.headers["Authorization"] = "Bearer " + token;
    if (body !== undefined) opt.body = JSON.stringify(body);
    return fetch(apiUrl(path), opt).then(function (r) {
      if (!r.ok) return r.json().then(function (e) { throw new Error(e.detail || r.status); });
      return r.json();
    });
  }

  var STATUS_COLOR = { ok: "green", warn: "orange", fail: "red", pushed: "green",
                       committed: "green", "no-change": "default", error: "red",
                       skipped: "default", recorded: "green", empty: "default" };

  // 简易布局件（原生）
  function Box(props) {
    return h("div", { style: Object.assign({ border: "1px solid #e8e8e8", borderRadius: 8,
      padding: 12, marginBottom: 12 }, props.style) }, props.children);
  }
  function Title(t) { return h("div", { style: { fontWeight: 600, marginBottom: 8 } }, t); }
  function KV(k, v) {
    return h("div", { style: { display: "flex", gap: 8, marginBottom: 4, fontSize: 13 } },
      h("span", { style: { color: "#888", minWidth: 90 } }, k),
      h("span", { style: { wordBreak: "break-all" } }, v));
  }
  function Tbl(heads, rows) {
    return h("table", { style: { width: "100%", borderCollapse: "collapse", fontSize: 13 } },
      h("thead", null, h("tr", null, heads.map(function (x) {
        return h("th", { key: x, style: { textAlign: "left", padding: "6px 8px", borderBottom: "1px solid #e8e8e8" } }, x); }))),
      h("tbody", null, rows.map(function (r, i) {
        return h("tr", { key: i }, r.map(function (c, j) {
          return h("td", { key: j, style: { padding: "6px 8px", borderBottom: "1px solid #f0f0f0", verticalAlign: "top" } }, c); })); })));
  }

  // ── 总览 ────────────────────────────────────────────────
  function Overview() {
    var _s = useState(null), ov = _s[0], setOv = _s[1];
    var _b = useState(false), busy = _b[0], setBusy = _b[1];
    function load() { req("GET", "/overview").then(setOv).catch(function (e) { message.error(String(e)); }); }
    useEffect(load, []);
    function act(fn, label) {
      setBusy(true);
      fn().then(function (r) { message.success(label + "：" + JSON.stringify(r).slice(0, 100)); load(); })
        .catch(function (e) { message.error(label + " 失败：" + e); })
        .finally(function () { setBusy(false); });
    }
    if (!ov) return h(Box, null, "加载中…");
    var last = ov.last || {};
    return h("div", null,
      h("div", { style: { display: "flex", gap: 12, flexWrap: "wrap", marginBottom: 12 } },
        [["raw 快照天数", ov.raw_snapshots], ["精炼仓 commits", ov.repo_commits],
         ["连续失败", ov.fail_streak], ["调度器", ov.scheduler_running ? "运行中" : "停止"]].map(function (x) {
          return h("div", { key: x[0], style: { border: "1px solid #e8e8e8", borderRadius: 8,
            padding: "10px 18px", minWidth: 120 } },
            h("div", { style: { color: "#888", fontSize: 12 } }, x[0]),
            h("div", { style: { fontSize: 22, fontWeight: 600,
              color: (x[0] === "连续失败" && x[1] > 0) ? "#cf1322" : undefined } }, String(x[1]))); })),
      h(Box, null, Title("最近运行"),
        KV("最近积累", JSON.stringify(last.last || "—")),
        KV("最近日记/总结", JSON.stringify(last.last_distill || "—")),
        KV("最近推送", JSON.stringify(last.last_push || "—"))),
      h(Box, null, Title("手动操作"),
        h(Space, { wrap: true },
          h(Button, { loading: busy, onClick: function () { act(function () { return req("POST", "/accumulate"); }, "积累"); } }, "立即积累"),
          h(Button, { loading: busy, onClick: function () { act(function () { return req("POST", "/commit-local"); }, "本地提交"); } }, "本地提交"),
          h(Button, { loading: busy, onClick: function () { act(function () { return req("POST", "/push"); }, "推送"); } }, "推送远端"),
          h(Button, { onClick: load }, "刷新")),
        h("div", { style: { marginTop: 8, color: "#888", fontSize: 12 } },
          "vault: " + ov.vault + " ｜ 绑定: " + (ov.agent_id || "未绑定"))));
  }

  // ── 历史 ────────────────────────────────────────────────
  function History() {
    var _s = useState(null), d = _s[0], setD = _s[1];
    useEffect(function () { req("GET", "/history").then(setD).catch(function (e) { message.error(String(e)); }); }, []);
    if (!d) return h(Box, null, "加载中…");
    return h("div", null,
      h(Box, null, Title("精炼仓提交历史"),
        d.commits.length ? Tbl(["日期", "commit", "说明"],
          d.commits.map(function (c) { return [c.date, c.hash, c.message]; }))
          : h(Empty, { description: "暂无提交" })),
      h(Box, null, Title("raw 快照（近 14 天）"),
        d.raw_days.length ? h(Space, { wrap: true }, d.raw_days.map(function (x) { return h(Tag, { key: x }, x); }))
          : h(Empty, { description: "暂无快照" })),
      h(Box, null, Title("运行记录"),
        (d.runs || []).length ? Tbl(["时间", "结果"],
          (d.runs || []).slice().reverse().map(function (r) {
            return [r.at, (r.results || []).map(function (x, i) {
              return h(Tag, { key: i, color: STATUS_COLOR[x.state] || "default" }, x.target + ":" + x.state); })]; }))
          : h(Empty, { description: "暂无记录" })));
  }

  // ── 设置 ────────────────────────────────────────────────
  function Settings() {
    var _s = useState(null), cfg = _s[0], setCfg = _s[1];
    var _b = useState(false), busy = _b[0], setBusy = _b[1];
    useEffect(function () { req("GET", "/settings").then(function (d) { setCfg(d.config || d); }).catch(function (e) { message.error(String(e)); }); }, []);
    function set(k, v) { var o = {}; o[k] = v; setCfg(Object.assign({}, cfg, o)); }
    function save() {
      setBusy(true);
      req("PUT", "/settings", cfg).then(function () { message.success("已保存"); })
        .catch(function (e) { message.error("保存失败：" + e); }).finally(function () { setBusy(false); });
    }
    if (!cfg) return h(Box, null, "加载中…");
    var ts = cfg.targets || [];
    var t0 = ts[0] || { name: "local", kind: "local", path: "", branch: "main" };
    var t1 = ts[1] || { name: "gitee", kind: "remote", url: "", branch: "backup-core" };
    function setT(idx, patch) {
      var arr = ts.slice(); while (arr.length < 2) arr.push({ name: idx ? "gitee" : "local", kind: idx ? "remote" : "local", path: "", url: "", branch: "main" });
      arr[idx] = Object.assign({}, arr[idx], patch); set("targets", arr);
    }
    return h(Box, null, Title("备份设置"),
      h(Space, { direction: "vertical", style: { width: "100%" }, size: 10 },
        h(Space, null, h("span", null, "启用"), h(Switch, { checked: !!cfg.enabled, onChange: function (v) { set("enabled", v); } })),
        KV("绑定智能体", h(Input, { style: { width: 260 }, value: cfg.agent_id || "", onChange: function (e) { set("agent_id", e.target.value); } })),
        KV("vault 路径", h(Input, { style: { width: 400 }, value: cfg.vault_path || "", placeholder: "留空=绑定agent工作区/backups/vault", onChange: function (e) { set("vault_path", e.target.value); } })),
        h(Space, { wrap: true },
          h("span", null, "积累间隔(分)"), h(InputNumber, { min: 10, max: 1440, value: cfg.accumulate_interval_minutes, onChange: function (v) { set("accumulate_interval_minutes", v); } }),
          h("span", null, "总结时间"), h(Input, { style: { width: 70 }, value: cfg.distill_time, onChange: function (e) { set("distill_time", e.target.value); } }),
          h("span", null, "推送时间"), h(Input, { style: { width: 70 }, value: cfg.push_time, onChange: function (e) { set("push_time", e.target.value); } })),
        h(Box, { style: { marginBottom: 8 } }, Title("本地仓库"),
          h(Input, { style: { width: 400 }, value: t0.path || "", placeholder: "本地精炼仓路径", onChange: function (e) { setT(0, { path: e.target.value }); } })),
        h(Box, { style: { marginBottom: 8 } }, Title("远端"),
          h(Space, { direction: "vertical" },
            h(Input, { style: { width: 400 }, value: t1.url || "", placeholder: "git@gitee.com:xxx/repo.git", onChange: function (e) { setT(1, { kind: "remote", url: e.target.value }); } }),
            h(Input, { style: { width: 150 }, value: t1.branch || "", placeholder: "远端分支", onChange: function (e) { setT(1, { branch: e.target.value }); } }))),
        h(Box, { style: { marginBottom: 8 } }, Title("日记模型（仅2h日记；总结走主模型）"),
          h(Space, { direction: "vertical" },
            h(Input, { style: { width: 400 }, value: cfg.base_url || "", placeholder: "base_url", onChange: function (e) { set("base_url", e.target.value); } }),
            h(Input, { style: { width: 240 }, value: cfg.model || "", placeholder: "model", onChange: function (e) { set("model", e.target.value); } }))),
        h(Button, { type: "primary", loading: busy, onClick: save }, "保存设置")));
  }

  // ── 检查 ────────────────────────────────────────────────
  function Checkup() {
    var _s = useState(null), d = _s[0], setD = _s[1];
    function load() { req("GET", "/checkup").then(setD).catch(function (e) { message.error(String(e)); }); }
    useEffect(load, []);
    if (!d) return h(Box, null, "加载中…");
    return h(Box, null,
      h(Space, { style: { width: "100%", justifyContent: "space-between" } },
        Title("自检清单"), h(Button, { size: "small", onClick: load }, "重新检查")),
      Tbl(["状态", "检查项", "详情"],
        d.items.map(function (x) {
          return [h(Tag, { color: STATUS_COLOR[x.status] }, x.status), x.label, x.detail || ""]; })));
  }

  // ── 闸门 ────────────────────────────────────────────────
  function Gate() {
    var _s = useState(null), d = _s[0], setD = _s[1];
    useEffect(function () { req("GET", "/gate-log").then(setD).catch(function (e) { message.error(String(e)); }); }, []);
    if (!d) return h(Box, null, "加载中…");
    if (!d.length) return h(Box, null, h(Empty, { description: "暂无拦截记录（好事）" }));
    return h(Box, null, Title("敏感内容拦截记录"),
      Tbl(["时间", "会话", "拦截行数", "样例"],
        d.slice().reverse().map(function (r) {
          return [r.at, (r.session || "").slice(0, 8), r.dropped_lines, (r.samples || []).join(" / ").slice(0, 100)]; })));
  }

  // ── 根组件 ──────────────────────────────────────────────
  function BackupPanel() {
    return h("div", { style: { padding: 20, fontSize: 18 } }, "HELLO-BACKUP-PANEL-RENDER-OK");
    var theme = useHostTheme();
    return h("div", { style: { padding: 12, maxWidth: 960, color: theme.isDark ? "#eee" : undefined } },
      h(Tabs, { defaultActiveKey: "overview", items: [
        { key: "overview", label: "📊 总览", children: h(Overview) },
        { key: "history", label: "🗂 历史", children: h(History) },
        { key: "settings", label: "⚙️ 设置", children: h(Settings) },
        { key: "checkup", label: "🩺 检查", children: h(Checkup) },
        { key: "gate", label: "🛡 闸门", children: h(Gate) }
      ] }));
  }

  // ── Self-register（与 companion-core 同款）───────────────
  if (QwenPaw.registerRoutes) {
    try {
      QwenPaw.registerRoutes("backup-core", [{
        path: "/apps/backup-core",
        component: BackupPanel,
        label: "备份核心",
        icon: "📦",
      }]);
    } catch (e) { console.warn("[backup-core] registerRoutes failed", e); }
  }
  if (QwenPaw.menu && QwenPaw.menu.add) {
    try {
      QwenPaw.menu.add("backup-core", [{
        id: "backup-core.menu", location: "primary.settings",
        label: "备份核心", icon: function () { return h("span", null, "📦"); },
        route: "/apps/backup-core", order: 46,
      }]);
    } catch (e) { console.warn("[backup-core] menu.add failed", e); }
  }
})();
