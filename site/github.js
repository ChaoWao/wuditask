(function () {
  "use strict";
  const names = {
    contains: "子 Issue",
    relates: "正文关联（不表示关闭或阻塞）",
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
  function workGraph(data, options = {}) {
    const byId = new Map(data.nodes.map((n) => [n.id, n]));
    const associations = data.edges.filter(
      (e) =>
        ["delivers", "relates"].includes(e.kind) &&
        byId.has(e.from) &&
        byId.has(e.to),
    );
    const attached = new Set(associations.map((e) => e.from));
    const hierarchy = data.edges.filter((e) => e.kind === "contains");
    const main = closure(data.roots || [], hierarchy, "down");
    for (const e of associations) if (main.has(e.to)) main.add(e.from);
    const stateMatch = (n) =>
      !options.state ||
      (options.state === "active" ? !isTerminal(n) : n.state === options.state);
    const textMatch = (n) =>
      !options.search ||
      (label(n) + " " + n.title + " " + (n.assignees || []).join(" "))
        .toLowerCase()
        .includes(options.search.toLowerCase().replace(/^#/, ""));
    const associated = (id) =>
      associations.filter((e) => e.to === id).map((e) => byId.get(e.from));
    const units = data.nodes.filter((n) => !attached.has(n.id));
    const inScope = (n) =>
      options.scope === "other" ? !main.has(n.id) : main.has(n.id);
    const primary = new Set(
      units
        .filter(
          (n) =>
            inScope(n) &&
            (!options.repo || n.repo === options.repo) &&
            (!options.owner ||
              (options.owner === "__unassigned"
                ? !(n.assignees || []).length
                : (n.assignees || []).includes(options.owner))) &&
            [n, ...associated(n.id)].some(stateMatch) &&
            [n, ...associated(n.id).filter(stateMatch)].some(textMatch),
        )
        .map((n) => n.id),
    );
    const ids =
      options.scope === "other"
        ? new Set(primary)
        : closure(primary, hierarchy, "up");
    for (const id of ids) {
      const n = byId.get(id);
      if (!n || !inScope(n) || (!primary.has(id) && !stateMatch(n)))
        ids.delete(id);
    }
    const visibleUnits = data.nodes.filter((n) => ids.has(n.id));
    const edges = hierarchy.filter((e) => ids.has(e.from) && ids.has(e.to));
    for (const e of associations)
      if (ids.has(e.to) && stateMatch(byId.get(e.from))) {
        ids.add(e.from);
        edges.push({ ...e, from: e.to, to: e.from });
      }
    return {
      nodes: data.nodes.filter((n) => ids.has(n.id)),
      edges,
      primary,
      units: visibleUnits,
      associations: associations.filter(
        (e) => ids.has(e.to) && ids.has(e.from),
      ),
      counts: {
        main: units.filter(
          (n) => main.has(n.id) && [n, ...associated(n.id)].some(stateMatch),
        ).length,
        other: units.filter(
          (n) => !main.has(n.id) && [n, ...associated(n.id)].some(stateMatch),
        ).length,
      },
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
    workGraph,
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
  const $ = (id) => document.getElementById(id);
  let data = null,
    selected = null,
    expandedOwners = new Set(),
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
    // Rendering replaces the tree; keep keyboard navigation at the toggle.
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
    const linked = data.edges.filter((e) => e.from === n.id || e.to === n.id);
    if (linked.length) {
      source.append(el("h3", "关联任务"));
      const ul = el("ul");
      linked.forEach((e) => {
        const other = data.nodes.find(
          (x) => x.id === (e.from === n.id ? e.to : e.from),
        );
        const li = el("li"),
          b = el("button", `${names[e.kind] || e.kind} · ${label(other)}`);
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
  function workCard(n, edge) {
    const card = el(
      "div",
      undefined,
      "work-card" +
        (n.kind === "pr" ? " pr" : "") +
        (selected === n.id ? " selected" : "") +
        (edge?.kind === "relates" ? " related" : ""),
    );
    card.dataset.node = n.id;
    const button = el("button", n.title, "work-pick");
    button.onclick = () => choose(n.id);
    bindSummary(button, n);
    const meta = el(
      "small",
      label(n) + " · " + ((n.assignees || []).join(", ") || "未指派"),
    );
    card.append(button, meta);
    if (isTerminal(n)) card.append(el("small", businessState(n)));
    if (edge && edge.kind !== "contains")
      card.append(
        el(
          "small",
          edge.kind === "relates"
            ? "正文关联 · 非关闭 / 阻塞关系"
            : "正式关联 PR",
        ),
      );
    if (
      ["blocked", "unknown", "invalid", "needs_assessment"].includes(
        n.prerequisites,
      )
    )
      card.append(
        el("small", prerequisiteLabels[n.prerequisites], "summary-warning"),
      );
    return card;
  }
  function render() {
    if (!data) return;
    hideSummary();
    const options = {
      scope: document.querySelector(".scope-tab.active").dataset.scope,
      repo: $("repo").value,
      owner: $("owner").value,
      state: $("state").value,
      search: $("search").value,
    };
    const view = workGraph(data, options),
      byId = new Map(view.nodes.map((n) => [n.id, n]));
    const filtering = Boolean(
      options.repo ||
      options.owner ||
      options.search ||
      (options.state && options.state !== "active"),
    );
    const canvas = $("graph");
    canvas.replaceChildren();
    for (const scope of ["main", "other"])
      document.querySelector(
        `[data-scope="${scope}"] .scope-count`,
      ).textContent = view.counts[scope];
    $("caption").textContent =
      `${view.units.length} 项工作 · ${new Set(view.associations.map((e) => e.from)).size} 个关联 PR`;
    $("expand-all").disabled = $("collapse-all").disabled = filtering;
    const cyclic = layout(view.nodes, view.edges).unresolved;
    if (cyclic.size) $("caption").textContent += " · 层级关系存在环";
    const childEdges = (id) => view.edges.filter((e) => e.from === id);
    function tree(id, edge, path = new Set()) {
      if (path.has(id))
        return el("p", "层级关系成环，请在 GitHub 核定。", "summary-warning");
      const n = byId.get(id),
        section = el("div", undefined, "work-branch"),
        card = workCard(n, edge),
        children = childEdges(id);
      section.append(card);
      if (children.length) {
        const toggle = el(
          "button",
          collapsed.has(id) && !filtering ? "＋" : "−",
          "work-toggle",
        );
        toggle.dataset.toggle = id;
        toggle.dataset.source = "graph";
        toggle.setAttribute("aria-label", `展开或收起 ${n.title}`);
        toggle.setAttribute(
          "aria-expanded",
          String(!collapsed.has(id) || filtering),
        );
        toggle.disabled = filtering;
        toggle.onclick = () => toggleNode(id, "graph");
        card.prepend(toggle);
        if (!collapsed.has(id) || filtering || cyclic.has(id)) {
          const sub = el("div", undefined, "work-children");
          for (const e of children)
            sub.append(tree(e.to, e, new Set([...path, id])));
          section.append(sub);
        }
      }
      return section;
    }
    if (options.scope === "main") {
      const incoming = new Set(view.edges.map((e) => e.to)),
        roots = view.nodes.filter((n) => !incoming.has(n.id));
      const reachable = closure(
        roots.map((n) => n.id),
        view.edges,
        "down",
      );
      for (const n of roots) canvas.append(tree(n.id));
      for (const n of view.nodes) {
        if (reachable.has(n.id)) continue;
        canvas.append(tree(n.id));
        for (const id of closure([n.id], view.edges, "down")) reachable.add(id);
      }
    } else {
      const groups = new Map();
      for (const n of view.units) {
        const owner = (n.assignees || []).join(", ") || "未指派";
        if (!groups.has(owner)) groups.set(owner, []);
        groups.get(owner).push(n);
      }
      for (const [owner, nodes] of [...groups].sort((a, b) =>
        a[0].localeCompare(b[0]),
      )) {
        const group = el("details", undefined, "owner-group");
        group.open = filtering || expandedOwners.has(owner);
        group.ontoggle = () => {
          group.open ? expandedOwners.add(owner) : expandedOwners.delete(owner);
        };
        group.append(el("summary", `${owner} · ${nodes.length} 项工作`));
        for (const n of nodes) {
          const unit = el("div", undefined, "work-unit");
          unit.dataset.unit = n.id;
          unit.append(workCard(n));
          const sub = el("div", undefined, "work-children");
          for (const e of view.associations.filter((e) => e.to === n.id))
            sub.append(workCard(byId.get(e.from), e));
          if (sub.childNodes.length) unit.append(sub);
          group.append(unit);
        }
        canvas.append(group);
      }
    }
    if (!view.nodes.length) canvas.append(el("p", "没有匹配的工作。", "empty"));
    const rows = $("rows");
    rows.replaceChildren();
    for (const n of view.units) {
      const tr = el("tr"),
        td = el("td"),
        button = el("button", n.title);
      tr.dataset.unit = n.id;
      button.onclick = () => choose(n.id);
      bindSummary(button, n);
      td.append(button, el("small", label(n)));
      const sub = el("div", undefined, "list-prs");
      for (const e of view.associations.filter((e) => e.to === n.id)) {
        const pr = byId.get(e.from);
        sub.append(workCard(pr, e));
      }
      if (sub.childNodes.length) td.append(sub);
      tr.append(
        td,
        el("td", (n.assignees || []).join(", ") || "未指派"),
        el("td", businessState(n)),
        el(
          "td",
          ["blocked", "unknown", "invalid", "needs_assessment"].includes(
            n.prerequisites,
          )
            ? prerequisiteLabels[n.prerequisites]
            : "",
        ),
      );
      rows.append(tr);
    }
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
      $("owner").add(new Option("未指派", "__unassigned"));
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
  ["repo", "owner", "state"].forEach((id) => ($(id).onchange = render));
  document.querySelectorAll(".scope-tab").forEach(
    (b) =>
      (b.onclick = () => {
        document.querySelectorAll(".scope-tab").forEach((x) => {
          x.classList.toggle("active", x === b);
          x.setAttribute("aria-pressed", String(x === b));
        });
        render();
      }),
  );
  $("search").oninput = render;
  $("refresh").onclick = load;
  $("expand-all").onclick = () => {
    collapsed.clear();
    data.nodes.forEach((n) =>
      expandedOwners.add((n.assignees || []).join(", ") || "未指派"),
    );
    render();
  };
  $("collapse-all").onclick = () => {
    collapsed = initialCollapsed(data);
    expandedOwners.clear();
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
