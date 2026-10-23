"use strict";
const assert = require("node:assert/strict");
const model = require("../site/github.js");
const nodes = [
  {
    id: "a",
    kind: "issue",
    repo: "acme/lib",
    number: 1,
    title: "Upstream",
    state: "closed",
    assignees: ["alice"],
  },
  {
    id: "b",
    kind: "issue",
    repo: "acme/app",
    number: 2,
    title: "Consumer",
    state: "open",
    assignees: ["bob"],
  },
  { id: "c", kind: "boundary", title: "Unavailable", state: "unknown" },
];
const data = {
  nodes,
  edges: [
    { from: "a", to: "b", kind: "blocks" },
    { from: "c", to: "a", kind: "blocks" },
    { from: "a", to: "b", kind: "contains" },
  ],
};
const view = model.selectGraph(data, { kind: "blocks", repo: "acme/app" });
assert.equal(view.primary.size, 1);
assert.deepEqual(
  new Set(view.nodes.map((n) => n.id)),
  new Set(["a", "b", "c"]),
);
assert.equal(view.edges.length, 2);
assert.equal(model.safeUrl("javascript:alert(1)"), null);
assert.equal(model.safeUrl("https://github.com.evil/acme/app/issues/1"), null);
assert.equal(
  model.safeUrl("https://github.com/acme/app/issues/1"),
  "https://github.com/acme/app/issues/1",
);
const positions = model.layout(nodes, view.edges);
assert.ok(positions.positions.get("c").x < positions.positions.get("a").x);
assert.ok(positions.positions.get("a").x < positions.positions.get("b").x);
const cycle = model.layout(nodes, [
  ...view.edges,
  { from: "b", to: "a", kind: "blocks" },
]);
assert.equal(cycle.unresolved.size, 2);
assert.ok(Number.isFinite(cycle.width));
assert.equal(
  model.health(
    {
      generated_at: "2026-01-01T00:00:00Z",
      stale_after_seconds: 3600,
      status: "complete",
    },
    Date.parse("2026-01-01T02:00:00Z"),
  ),
  "stale",
);
assert.equal(
  model.health(
    {
      generated_at: "2026-01-01T00:00:00Z",
      stale_after_seconds: 3600,
      status: "partial",
    },
    Date.parse("2026-01-01T00:01:00Z"),
  ),
  "partial",
);
console.log(
  "Business graph filters, boundaries, cycles, safe links and age checks passed.",
);

// Folding follows open paths, including shared children in a DAG.
const tree = {
  nodes: ["r", "a", "b", "c", "leaf", "pr"].map((id) => ({
    id,
    kind: id === "pr" ? "pr" : "issue",
  })),
  edges: [
    ["r", "a"],
    ["r", "b"],
    ["a", "c"],
    ["b", "c"],
    ["c", "leaf"],
  ].map(([from, to]) => ({ from, to, kind: "contains" })),
};
const all = model.selectGraph(tree, { kind: "contains" });
assert.deepEqual([...model.initialCollapsed(tree)].sort(), ["a", "b", "c"]);
assert.deepEqual(
  model.collapseGraph(all, model.initialCollapsed(tree)).nodes.map((n) => n.id),
  ["r", "a", "b"],
);
assert.deepEqual(
  model.collapseGraph(all, new Set(["a"])).nodes.map((n) => n.id),
  ["r", "a", "b", "c", "leaf"],
);
assert.deepEqual(
  model.collapseGraph(all, new Set(["r"])).nodes.map((n) => n.id),
  ["r"],
);
assert.equal(model.collapseGraph(all, new Set()).nodes.length, 5);
assert.deepEqual(
  model.collapseGraph(
    model.selectGraph(tree, { kind: "contains", search: "no-match" }),
    new Set(),
  ).nodes,
  [],
);
const cyclic = model.selectGraph(
  {
    ...tree,
    edges: [...tree.edges, { from: "leaf", to: "c", kind: "contains" }],
  },
  { kind: "contains" },
);
assert.ok(
  model.collapseGraph(cyclic, new Set(["r"])).nodes.some((n) => n.id === "c"),
);
const summaryRoot = {
  id: "r",
  kind: "issue",
  title: "<img src=x>",
  state: "open",
  assignees: ["alice"],
  prerequisites: "blocked",
  relations_complete: { contains: true, delivers: true },
};
const summary = model.nodeSummary(
  {
    nodes: [
      summaryRoot,
      { id: "a", state: "closed", state_reason: "completed" },
      { id: "b", state: "closed", state_reason: "not_planned" },
    ],
    edges: tree.edges,
  },
  summaryRoot,
);
assert.ok(summary.includes("<img src=x>"));
assert.ok(summary.includes("直接子项：1 / 2 已完成（已采集）"));
assert.ok(summary.includes("前置：前置未完成"));
assert.ok(
  model
    .nodeSummary({ nodes: [], edges: [] }, { kind: "boundary" })
    .includes("负责人：未指派 / 未知"),
);
console.log(
  "Collapsing trees, shared children, cycles and factual summaries passed.",
);

const activeData = {
  nodes: [
    { id: "done", kind: "issue", state: "closed" },
    { id: "open", kind: "issue", state: "open" },
    {
      id: "cancelled",
      kind: "issue",
      state: "closed",
      state_reason: "not_planned",
    },
    { id: "merged", kind: "pr", state: "merged" },
    { id: "unknown", kind: "boundary", state: "unknown" },
  ],
  edges: [
    { from: "done", to: "open", kind: "contains" },
    { from: "done", to: "open", kind: "blocks" },
    { from: "unknown", to: "open", kind: "blocks" },
    { from: "merged", to: "open", kind: "delivers" },
  ],
};
for (const kind of ["contains", "blocks"]) {
  const active = model.selectGraph(activeData, { kind, state: "active" });
  assert.deepEqual(
    active.nodes.map((n) => n.id),
    ["open", "unknown"],
  );
  assert.ok(active.edges.every((e) => e.from === "unknown" && e.to === "open"));
  assert.ok(
    model
      .collapseGraph(active, new Set(["done"]))
      .nodes.some((n) => n.id === "open"),
  );
}
assert.equal(
  model.selectGraph(activeData, { kind: "blocks", state: "" }).nodes.length,
  3,
);
console.log(
  "Active view hides terminal boundaries, retains open descendants and unknowns, and permits history.",
);

// Relationship views omit unrelated tasks and pairs removed by the active filter.
const unrelated = { id: "unrelated", kind: "issue", state: "open" };
const sparse = { ...activeData, nodes: [...activeData.nodes, unrelated] };
assert.deepEqual(
  model
    .selectGraph(sparse, { kind: "blocks", state: "active" })
    .nodes.map((n) => n.id),
  ["open", "unknown"],
);
assert.deepEqual(
  model.selectGraph(sparse, { kind: "delivers", state: "active" }).nodes,
  [],
);
assert.equal(
  model.selectGraph(sparse, { kind: "delivers", state: "" }).edges.length,
  1,
);
assert.ok(
  model
    .selectGraph(sparse, { kind: "contains", state: "active" })
    .nodes.some((n) => n.id === "unrelated"),
);

const delivery = {
  nodes: [
    { id: "pr", kind: "pr", state: "open", assignees: ["author"] },
    { id: "issue", kind: "issue", state: "open", assignees: ["owner"] },
  ],
  edges: [{ from: "pr", to: "issue", kind: "delivers" }],
};
const authorView = model.selectGraph(delivery, {
  kind: "delivers",
  owner: "author",
  state: "active",
});
assert.equal(authorView.nodes.length, 2);
assert.equal(authorView.edges.length, 1);
assert.deepEqual([...authorView.primary], ["pr"]);
