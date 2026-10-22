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
