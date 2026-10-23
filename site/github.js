(function () {
  "use strict";
  const names = {
    contains: "目标拆分：父目标 → 子 Issue（不表示执行先后）",
    blocks: "阻塞依赖：前置 Issue → 后续 Issue",
    delivers: "交付关联：PR → Issue（合入不自动完成整个目标）",
  };
  const prerequisiteLabels = {
    satisfied: "已记录前置满足",
    blocked: "前置未完成",
    unrecorded: "未记录前置",
    unknown: "前置未知",
    invalid: "依赖环 / 受环影响",
    needs_assessment: "取消 / 关闭原因待核定",
    not_applicable: "不适用",
  };
  function safeUrl(value) {
    return typeof value === "string" &&
      /^https:\/\/github\.com\/[\w.-]+\/[\w.-]+\/(issues|pull)\/[1-9][0-9]*\/?$/.test(
        value,
      )
      ? value
      : null;
  }
  function label(n) {
    return n.repo
      ? n.repo + (n.kind === "pr" ? " PR #" : " #") + n.number
      : "受限 / 未知引用";
  }
  function businessState(n) {
    return n.state === "closed"
      ? "Closed · " + (n.state_reason || "原因未知")
      : n.state || "unknown";
  }
  function closure(seeds, edges, direction) {
    const ids = new Set(seeds),
      queue = [...seeds],
      adjacent = new Map();
    edges.forEach((e) => {
      const a = direction === "up" ? e.to : e.from,
        b = direction === "up" ? e.from : e.to;
      if (!adjacent.has(a)) adjacent.set(a, []);
      adjacent.get(a).push(b);
    });
    for (let i = 0; i < queue.length; i++)
      for (const id of adjacent.get(queue[i]) || [])
        if (!ids.has(id)) {
          ids.add(id);
          queue.push(id);
        }
    return ids;
  }
  function isTerminal(n) {
    return n.state === "closed" || n.state === "merged";
  }
  function selectGraph(data, options) {
    const edges = data.edges.filter((e) => e.kind === options.kind);
    const focus = options.focus
      ? closure([options.focus.id], edges, options.focus.direction)
      : null;
    const matches = data.nodes.filter(
      (n) =>
        (options.kind !== "contains" || n.kind !== "pr") &&
        (!options.repo || n.repo === options.repo) &&
        (!options.owner || (n.assignees || []).includes(options.owner)) &&
        (!options.state ||
          (options.state === "active"
            ? !isTerminal(n)
            : n.state === options.state)) &&
        (!options.search ||
          (label(n) + " " + n.title)
            .toLowerCase()
            .includes(options.search.toLowerCase())) &&
        (!focus || focus.has(n.id)),
    );
    const primary = new Set(matches.map((n) => n.id));
    // Retain upstream context for explicit filters; the working view hides terminal
    // nodes even at boundaries. Full relationships remain available in details.
    const seeds = new Set(primary);
    if (options.kind !== "contains")
      for (const e of edges) if (primary.has(e.from)) seeds.add(e.to);
    const ids = closure(seeds, edges, "up");
    if (focus) for (const id of ids) if (!focus.has(id)) ids.delete(id);
    if (options.state === "active")
      for (const n of data.nodes) if (isTerminal(n)) ids.delete(n.id);
    const visibleEdges = edges.filter((e) => ids.has(e.from) && ids.has(e.to));
    if (options.kind !== "contains") {
      const connected = new Set(visibleEdges.flatMap((e) => [e.from, e.to]));
      for (const id of ids) if (!connected.has(id)) ids.delete(id);
    }
    return {
      nodes: data.nodes.filter((n) => ids.has(n.id)),
      edges: visibleEdges,
      primary: new Set([...primary].filter((id) => ids.has(id))),
    };
  }
  function childIds(data, id) {
    return new Set(
      data.edges
        .filter((e) => e.kind === "contains" && e.from === id)
        .map((e) => e.to),
    );
  }
  function initialCollapsed(data) {
    const edges = data.edges.filter((e) => e.kind === "contains");
    const children = new Set(edges.map((e) => e.to));
    return new Set(
      edges.filter((e) => children.has(e.from)).map((e) => e.from),
    );
  }
  function collapseGraph(view, collapsed) {
    const incoming = new Set(view.edges.map((e) => e.to));
    const next = new Map(view.nodes.map((n) => [n.id, []]));
    view.edges.forEach((e) => next.get(e.from)?.push(e.to));
    // Keep malformed cyclic components visible so folding cannot hide diagnostics.
    const unresolved = layout(view.nodes, view.edges).unresolved;
    const visible = new Set(unresolved);
    const queue = view.nodes
      .filter((n) => !incoming.has(n.id))
      .map((n) => n.id);
    for (let i = 0; i < queue.length; i++) {
      const id = queue[i];
      if (visible.has(id)) continue;
      visible.add(id);
      if (!collapsed.has(id)) queue.push(...(next.get(id) || []));
    }
    return {
      nodes: view.nodes.filter((n) => visible.has(n.id)),
      edges: view.edges.filter(
        (e) =>
          visible.has(e.from) &&
          visible.has(e.to) &&
          (!collapsed.has(e.from) || unresolved.has(e.from)),
      ),
      primary: new Set([...view.primary].filter((id) => visible.has(id))),
    };
  }
  function nodeSummary(data, n) {
    const ids = childIds(data, n.id);
    const children = data.nodes.filter((c) => ids.has(c.id));
    const completed = children.filter(
      (c) => c.state === "closed" && c.state_reason === "completed",
    ).length;
    const lines = [
      label(n),
      n.title,
      "负责人：" + ((n.assignees || []).join(", ") || "未指派 / 未知"),
    ];
    for (const [key, title] of [
      ["goal", "目标"],
      ["progress", "进展"],
      ["next", "下一步"],
    ]) {
      const section = n.summary?.[key];
      if (section?.items?.length) {
        const value = section.items[0];
        lines.push(
          title + "：" + value.slice(0, 160) + (value.length > 160 ? "…" : ""),
        );
      }
    }
    if (
      ["blocked", "unknown", "invalid", "needs_assessment"].includes(
        n.prerequisites,
      )
    )
      lines.push("前置：" + prerequisiteLabels[n.prerequisites]);
    if (n.kind === "issue" && ids.size) {
      lines.push(
        `直接子项：${completed} / ${ids.size} 已完成` +
          (n.relations_complete?.contains === true
            ? "（已采集）"
            : "（关系未完整核实）"),
      );
    }
    if (n.kind === "pr") {
      const c = n.checks;
      lines.push("审查：" + (n.review_decision || "未知"));
      lines.push(
        c
          ? `当前 head 检查：${c.successful} 成功 / ${c.failed} 失败 / ${c.pending} 等待 / ${c.skipped} 跳过或中性`
          : "当前 head 检查：未知",
      );
    }
    if (n.errors?.length) lines.push("注意：存在采集错误，详情见右侧");
    return lines;
  }
  function layout(nodes, edges) {
    const indegree = new Map(nodes.map((n) => [n.id, 0])),
      next = new Map(nodes.map((n) => [n.id, []])),
      depth = new Map();
    edges.forEach((e) => {
      if (next.has(e.from) && indegree.has(e.to)) {
        next.get(e.from).push(e.to);
        indegree.set(e.to, indegree.get(e.to) + 1);
      }
    });
    const queue = nodes
      .filter((n) => indegree.get(n.id) === 0)
      .map((n) => n.id);
    queue.forEach((id) => depth.set(id, 0));
    for (let i = 0; i < queue.length; i++)
      for (const id of next.get(queue[i])) {
        depth.set(id, Math.max(depth.get(id) || 0, depth.get(queue[i]) + 1));
        indegree.set(id, indegree.get(id) - 1);
        if (!indegree.get(id)) queue.push(id);
      }
    const unresolved = new Set(
      nodes.filter((n) => indegree.get(n.id) > 0).map((n) => n.id),
    );
    const rows = new Map(),
      positions = new Map();
    let maxX = 0,
      maxY = 0;
    // Cyclic components are displayed in a diagnostic column, never treated as a DAG.
    nodes.forEach((n) => {
      const d = unresolved.has(n.id) ? 0 : depth.get(n.id) || 0,
        row = rows.get(d) || 0;
      rows.set(d, row + 1);
      const x = 30 + d * 340,
        y = 60 + row * 124;
      positions.set(n.id, { x, y });
      maxX = Math.max(maxX, x);
      maxY = Math.max(maxY, y);
    });
    return {
      positions,
      unresolved,
      width: Math.max(700, maxX + 310),
      height: Math.max(380, maxY + 125),
    };
  }
  function health(data, now) {
    const time = Date.parse(data.generated_at);
    return !Number.isFinite(time) ||
      now - time > data.stale_after_seconds * 1000
      ? "stale"
      : data.status;
  }
  const model = {
    childIds,
    initialCollapsed,
    collapseGraph,
    nodeSummary,
    safeUrl,
    label,
    businessState,
    closure,
    selectGraph,
    layout,
    health,
  };
  if (typeof module !== "undefined" && module.exports) module.exports = model;
  if (typeof document === "undefined") return;
  const $ = (id) => document.getElementById(id),
    NS = "http://www.w3.org/2000/svg";
  let data = null,
    selected = null,
    focus = null,
    scale = 1,
    drawing = null,
    refreshFailed = false,
    collapsed = null,
    summaryAnchor = null,
    summaryTimer = null;
  const tooltip = $("node-summary");
  function el(tag, text, className) {
    const n = document.createElement(tag);
    if (text !== undefined) n.textContent = text;
    if (className) n.className = className;
    return n;
  }
  function svg(tag, attrs, text) {
    const n = document.createElementNS(NS, tag);
    Object.entries(attrs || {}).forEach(([k, v]) =>
      n.setAttribute(k, String(v)),
    );
    if (text !== undefined) n.textContent = text;
    return n;
  }
  function trim(s, n) {
    let result = "",
      width = 0;
    for (const ch of String(s)) {
      width += ch.codePointAt(0) > 255 ? 2 : 1;
      if (width > n) return result + "…";
      result += ch;
    }
    return result;
  }
  function hideSummary() {
    clearTimeout(summaryTimer);
    if (summaryAnchor) summaryAnchor.removeAttribute("aria-describedby");
    summaryAnchor = null;
    tooltip.hidden = true;
  }
  function deferHideSummary() {
    clearTimeout(summaryTimer);
    summaryTimer = setTimeout(hideSummary, 180);
  }
  function showSummary(n, anchor) {
    hideSummary();
    summaryAnchor = anchor;
    tooltip.replaceChildren();
    nodeSummary(data, n).forEach((line, i) =>
      tooltip.append(el(i === 1 ? "strong" : "div", line)),
    );
    tooltip.hidden = false;
    anchor.setAttribute("aria-describedby", "node-summary");
    const rect = anchor.getBoundingClientRect();
    const width = tooltip.offsetWidth,
      height = tooltip.offsetHeight;
    let left = rect.right + 10;
    if (left + width > window.innerWidth - 12) left = rect.left - width - 10;
    tooltip.style.left = Math.max(12, left) + "px";
    tooltip.style.top =
      Math.max(12, Math.min(rect.top, window.innerHeight - height - 12)) + "px";
  }
  function bindSummary(anchor, n) {
    anchor.onmouseenter = () => showSummary(n, anchor);
    anchor.onmouseleave = deferHideSummary;
    anchor.onfocus = () => showSummary(n, anchor);
    anchor.onblur = deferHideSummary;
  }
  function toggleNode(id, source) {
    if (collapsed.has(id)) collapsed.delete(id);
    else collapsed.add(id);
    render();
    // Rendering replaces the SVG/table; keep keyboard navigation at the toggle.
    [...document.querySelectorAll("[data-toggle]")]
      .find((n) => n.dataset.toggle === id && n.dataset.source === source)
      ?.focus();
  }
  function updateHealth() {
    if (!data) return;
    const status = health(data, Date.now());
    $("health").textContent = refreshFailed
      ? "刷新失败 · 仍显示上次快照"
      : status === "stale"
        ? "数据已过期"
        : status === "partial"
          ? "部分采集 · 存在未知或受限信息"
          : "";
    $("health").parentElement.classList.toggle(
      "warning",
      refreshFailed || status !== "complete",
    );
    $("health").parentElement.hidden = !refreshFailed && status === "complete";
  }
  function fillFilter(id, values) {
    const select = $(id),
      old = select.value;
    while (select.options.length > 1) select.remove(1);
    [...new Set(values)].sort().forEach((v) => select.add(new Option(v, v)));
    select.value = old;
  }
  function choose(id) {
    selected = id;
    render();
  }
  function renderDetails() {
    const panel = $("details");
    panel.replaceChildren();
    const n = data.nodes.find((n) => n.id === selected);
    if (!n) {
      panel.append(
        el("h2", "选择一个任务"),
        el("p", "查看目标、当前进展、剩余工作和下一步。"),
      );
      return;
    }
    panel.append(el("h2", n.title));
    if (safeUrl(n.url)) {
      const a = el("a", label(n));
      a.href = n.url;
      a.target = "_blank";
      a.rel = "noopener noreferrer";
      panel.append(a);
    }
    const summary = n.summary || {};
    const core = el("div", undefined, "core-summary");
    core.append(el("p", "摘自 GitHub 正文 · 以原文更新为准", "muted"));
    if (n.errors?.some((e) => e.field === "observation"))
      core.append(
        el(
          "p",
          "采集中来源发生变化或未能复核，请打开原文确认。",
          "summary-warning",
        ),
      );
    let hasSummary = false;
    for (const [key, title] of [
      ["goal", "要做什么"],
      ["progress", "当前进展"],
      ["remaining", "剩余工作 / 阻塞"],
      ["next", "下一步"],
    ]) {
      const section = summary[key];
      if (!section?.items?.length) continue;
      hasSummary = true;
      core.append(el("h3", title));
      section.items.forEach((text) => core.append(el("p", text)));
      if (section.truncated)
        core.append(
          el("small", "部分摘录，完整内容见原 Issue / PR。", "muted"),
        );
    }
    if (!hasSummary) core.append(el("p", "暂无摘要，可打开 GitHub 查看详情。"));
    panel.append(core);
    const source = el("details", undefined, "source-details");
    source.append(el("summary", "来源详情与关联任务"));
    panel.append(source);
    const list = el("dl");
    const fields = [
      ["业务状态", businessState(n)],
      ["负责人", (n.assignees || []).join(", ") || "未指派"],
      ["前置条件", prerequisiteLabels[n.prerequisites] || "未知"],
      ["来源更新时间", n.updated_at || "未知"],
      ["本次观察时间", n.observed_at || "未知"],
    ];
    if (n.kind === "pr") {
      fields.push(["观察到的 head", n.head_sha], ["审查", n.review_decision]);
      const c = n.checks;
      fields.push([
        "当前 head 检查",
        c
          ? `${c.total} 项 · ${c.successful} 成功 / ${c.failed} 失败 / ${c.pending} 等待 / ${c.skipped} 跳过或中性`
          : "未知",
      ]);
    }
    fields.forEach(([title, value]) =>
      list.append(el("dt", title), el("dd", value || "未知")),
    );
    source.append(list);
    [
      ["查看前置", "up"],
      ["查看后继", "down"],
    ].forEach(([text, direction]) => {
      const button = el("button", text);
      button.onclick = () => {
        focus = { id: n.id, direction };
        render();
      };
      source.append(button);
    });
    const linked = data.edges.filter((e) => e.from === n.id || e.to === n.id);
    if (linked.length) {
      source.append(el("h3", "原生关系"));
      const ul = el("ul");
      linked.forEach((e) => {
        const other = data.nodes.find(
          (x) => x.id === (e.from === n.id ? e.to : e.from),
        );
        const li = el("li"),
          b = el("button", `${e.kind} · ${label(other)}`);
        b.onclick = () => choose(other.id);
        li.append(b);
        ul.append(li);
      });
      source.append(ul);
    }
    if ((n.errors || []).length)
      source.append(
        el(
          "p",
          "未完成读取：" +
            n.errors.map((e) => e.field + " / " + e.code).join("；"),
        ),
      );
  }
  function applyScale() {
    if (drawing) {
      drawing.svg.style.width = drawing.width * scale + "px";
      drawing.svg.style.height = drawing.height * scale + "px";
    }
  }
  function render() {
    if (!data) return;
    const options = {
      kind: $("relation").value,
      repo: $("repo").value,
      owner: $("owner").value,
      state: $("state").value,
      search: $("search").value,
      focus,
    };
    hideSummary();
    const filtered = selectGraph(data, options);
    const filtering = Boolean(
      options.repo ||
      options.owner ||
      (options.state && options.state !== "active") ||
      options.search ||
      options.focus,
    );
    const folding = options.kind === "contains" && !filtering;
    const view = folding ? collapseGraph(filtered, collapsed) : filtered,
      diagram = layout(view.nodes, view.edges),
      canvas = $("graph");
    canvas.replaceChildren();
    $("tree-controls").hidden = options.kind !== "contains";
    $("expand-all").disabled = $("collapse-all").disabled = !folding;
    $("caption").textContent =
      `${view.nodes.length} 个任务 · ${view.edges.length} 条关系` +
      (diagram.unresolved.size ? " · 存在依赖环" : "");
    $("reset").hidden = !focus;
    if (!view.nodes.length) {
      canvas.append(
        el(
          "p",
          options.kind === "blocks"
            ? "当前范围没有已记录的阻塞关系。"
            : options.kind === "delivers"
              ? "当前范围没有交付关联。"
              : "没有匹配的任务。",
          "empty",
        ),
      );
      drawing = null;
    } else {
      const root = svg("svg", {
        viewBox: `0 0 ${diagram.width} ${diagram.height}`,
        role: "group",
        "aria-label": names[options.kind],
      });
      const defs = svg("defs"),
        marker = svg("marker", {
          id: "arrow",
          viewBox: "0 0 10 10",
          refX: 9,
          refY: 5,
          markerWidth: 7,
          markerHeight: 7,
          orient: "auto-start-reverse",
        });
      marker.append(
        svg("path", { d: "M 0 0 L 10 5 L 0 10 z", fill: "#9dabb9" }),
      );
      defs.append(marker);
      root.append(defs);
      view.edges.forEach((e) => {
        const a = diagram.positions.get(e.from),
          b = diagram.positions.get(e.to),
          x = a.x + 280,
          y = a.y + 46;
        root.append(
          svg("path", {
            d: `M${x},${y} C${x + 45},${y} ${b.x - 45},${b.y + 46} ${b.x},${b.y + 46}`,
            class: "edge",
            "marker-end": "url(#arrow)",
          }),
        );
      });
      view.nodes.forEach((n) => {
        const pos = diagram.positions.get(n.id),
          boundary = !view.primary.has(n.id) || n.kind === "boundary";
        const g = svg("g", {
          transform: `translate(${pos.x},${pos.y})`,
          class:
            "node" +
            (boundary ? " boundary" : "") +
            (selected === n.id ? " selected" : ""),
          role: "button",
          tabindex: 0,
          "aria-label": label(n) + " " + n.title,
          "data-node": n.id,
        });
        g.append(
          svg("rect", { width: 280, height: 94, rx: 7 }),
          svg("text", { x: 13, y: 22, class: "label" }, trim(label(n), 30)),
          svg("text", { x: 13, y: 46, class: "title" }, trim(n.title, 28)),
          svg(
            "text",
            { x: 13, y: 67, class: "meta" },
            trim((n.assignees || []).join(", ") || "未指派 / 未知", 36),
          ),
          svg(
            "text",
            { x: 13, y: 84, class: "meta" },
            trim(
              businessState(n) +
                (["blocked", "unknown", "invalid", "needs_assessment"].includes(
                  n.prerequisites,
                )
                  ? " · " + prerequisiteLabels[n.prerequisites]
                  : ""),
              38,
            ),
          ),
        );
        bindSummary(g, n);
        g.onclick = () => choose(n.id);
        g.onkeydown = (event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            choose(n.id);
          }
        };
        root.append(g);
        const count = childIds(filtered, n.id).size;
        if (folding && count) {
          const toggle = svg("g", {
            transform: `translate(${pos.x + 246},${pos.y + 4})`,
            class: "tree-toggle",
            role: "button",
            tabindex: 0,
            "data-toggle": n.id,
            "data-source": "graph",
            "aria-expanded": !collapsed.has(n.id),
            "aria-label": `${collapsed.has(n.id) ? "展开" : "收起"} ${n.title} 的 ${count} 个直接子项`,
          });
          toggle.append(
            svg("rect", { width: 30, height: 26, rx: 5 }),
            svg(
              "text",
              { x: 15, y: 19, "text-anchor": "middle" },
              collapsed.has(n.id) ? "+" : "−",
            ),
          );
          toggle.onclick = () => toggleNode(n.id, "graph");
          toggle.onkeydown = (event) => {
            if (event.key === "Enter" || event.key === " ") {
              event.preventDefault();
              toggleNode(n.id, "graph");
            }
          };
          root.append(toggle);
        }
      });
      canvas.append(root);
      drawing = { svg: root, width: diagram.width, height: diagram.height };
      applyScale();
    }
    const rows = $("rows");
    rows.replaceChildren();
    view.nodes.forEach((n) => {
      const tr = el("tr"),
        td = el("td"),
        button = el("button", n.title);
      button.onclick = () => choose(n.id);
      bindSummary(button, n);
      if (folding && childIds(filtered, n.id).size) {
        const toggle = el(
          "button",
          collapsed.has(n.id) ? "＋" : "−",
          "table-toggle",
        );
        toggle.dataset.toggle = n.id;
        toggle.dataset.source = "table";
        toggle.setAttribute("aria-expanded", String(!collapsed.has(n.id)));
        toggle.setAttribute(
          "aria-label",
          `${collapsed.has(n.id) ? "展开" : "收起"} ${n.title}`,
        );
        toggle.onclick = () => toggleNode(n.id, "table");
        td.append(toggle);
      }
      td.append(button, el("small", label(n)));
      tr.append(
        td,
        el("td", (n.assignees || []).join(", ") || "未指派 / 未知"),
        el("td", businessState(n)),
        el("td", prerequisiteLabels[n.prerequisites] || "未知"),
      );
      rows.append(tr);
    });
    renderDetails();
    updateHealth();
  }
  async function load() {
    $("refresh").disabled = true;
    try {
      const response = await fetch("graph.json", { cache: "no-store" });
      if (!response.ok) throw new Error("snapshot_unavailable");
      const next = await response.json();
      if (
        next.format !== "github-business-graph" ||
        !Array.isArray(next.nodes) ||
        !Array.isArray(next.edges)
      )
        throw new Error("invalid_snapshot");
      data = next;
      if (collapsed === null) collapsed = initialCollapsed(data);
      refreshFailed = false;
      const repos = [...new Set(data.nodes.map((n) => n.repo).filter(Boolean))];
      fillFilter("repo", repos);
      $("repo").closest("label").hidden = repos.length <= 1;
      if (repos.length <= 1) $("repo").value = "";
      fillFilter(
        "owner",
        data.nodes.flatMap((n) => n.assignees || []),
      );
      $("diagnostics-label").textContent =
        `采集诊断 · ${data.diagnostics.length} 项`;
      $("diagnostics").closest("details").hidden = !data.diagnostics.length;
      $("diagnostics").replaceChildren();
      data.diagnostics.forEach((d) =>
        $("diagnostics").append(
          el(
            "li",
            [d.code, d.field, d.node, d.message].filter(Boolean).join(" · "),
          ),
        ),
      );
      render();
    } catch (error) {
      refreshFailed = true;
      if (data) updateHealth();
      else {
        $("health").textContent = "快照读取失败 · 无法判断业务状态";
        $("health").parentElement.classList.add("warning");
        $("graph").replaceChildren(
          el("p", "请检查采集 workflow 和发布记录。", "empty"),
        );
      }
    } finally {
      $("refresh").disabled = false;
    }
  }
  ["relation", "repo", "owner", "state"].forEach(
    (id) =>
      ($(id).onchange = () => {
        if (id === "relation") focus = null;
        render();
      }),
  );
  $("search").oninput = render;
  $("refresh").onclick = load;
  $("reset").onclick = () => {
    focus = null;
    render();
  };
  $("zoom-in").onclick = () => {
    scale = Math.min(2, scale * 1.2);
    applyScale();
  };
  $("zoom-out").onclick = () => {
    scale = Math.max(0.25, scale / 1.2);
    applyScale();
  };
  $("zoom-fit").onclick = () => {
    if (drawing) {
      scale = Math.max(
        0.25,
        Math.min(1, $("graph").clientWidth / drawing.width),
      );
      applyScale();
    }
  };
  $("expand-all").onclick = () => {
    collapsed.clear();
    render();
  };
  $("collapse-all").onclick = () => {
    collapsed = new Set(
      data.edges.filter((e) => e.kind === "contains").map((e) => e.from),
    );
    render();
  };
  tooltip.onmouseenter = () => clearTimeout(summaryTimer);
  tooltip.onmouseleave = deferHideSummary;
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") hideSummary();
  });
  $("graph").addEventListener("scroll", hideSummary);
  window.addEventListener("resize", hideSummary);
  setInterval(updateHealth, 30000);
  load();
})();
