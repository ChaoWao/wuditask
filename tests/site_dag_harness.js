"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

class FakeElement {
  constructor(tagName, id) {
    this.tagName = String(tagName || "div").toUpperCase();
    this.id = id || "";
    this.children = [];
    this.listeners = {};
    this.attributes = {};
    this.style = {};
    this.value = "";
    this.textContent = "";
    this.hidden = false;
    this.disabled = false;
    this.className = "";
  }

  appendChild(child) {
    this.children.push(child);
    if (this.tagName === "SELECT" && this.children.length === 1) {
      this.value = child.value;
    }
    return child;
  }

  replaceChildren(...children) {
    this.children = children;
    if (this.tagName === "SELECT") {
      this.value = this.children.length ? this.children[0].value : "";
    }
  }

  addEventListener(name, callback) {
    this.listeners[name] = callback;
  }

  setAttribute(name, value) {
    this.attributes[name] = String(value);
    if (name === "id") {
      this.id = String(value);
    }
  }
}

function issueTask(id, repo, number, dependencies) {
  return {
    schema_version: 3,
    id: id,
    repo: repo,
    source: { kind: "github_issue", repo: repo, number: number },
    created_by: "creator",
    priority: "P1",
    dependencies: dependencies || [],
    active_agents: [],
    created_at: "2026-07-16T00:00:00Z",
    derived: { state: "ready", dependencies: [] },
    delivery: {
      status: "fresh",
      delivery_state: "unstarted",
      title: "Issue work " + number,
      body: "Canonical Issue body",
      url: "https://github.com/" + repo + "/issues/" + number,
      owners: [],
      prs: []
    }
  };
}

function snapshot() {
  const issue = issueTask("WDT-20260716T000000Z-AAAAAA", "acme/alpha", 12);
  const text = {
    schema_version: 3,
    id: "WDT-20260716T000001Z-BBBBBB",
    repo: "acme/alpha",
    source: { kind: "github_issue", repo: "acme/alpha", number: 13 },
    created_by: "creator",
    priority: "P2",
    dependencies: [issue.id],
    active_agents: [],
    created_at: "2026-07-16T00:00:01Z",
    derived: { state: "blocked", dependencies: [] },
    delivery: {
      status: "fresh",
      delivery_state: "unstarted",
      title: "Second Issue task",
      body: "Canonical Issue body",
      url: "https://github.com/acme/alpha/issues/13",
      owners: [],
      prs: []
    }
  };
  const pullRequest = {
    schema_version: 3,
    id: "WDT-20260716T000002Z-CCCCCC",
    repo: "acme/beta",
    source: { kind: "github_pull_request", repo: "acme/beta", number: 7 },
    created_by: "creator",
    priority: "P2",
    dependencies: [issue.id],
    active_agents: [],
    created_at: "2026-07-16T00:00:02Z",
    derived: { state: "blocked", dependencies: [] },
    delivery: {
      status: "fresh",
      delivery_state: "review",
      title: "Cross-repository delivery",
      body: "Canonical PR body",
      url: "https://github.com/acme/beta/pull/7",
      owners: ["author"],
      prs: []
    }
  };
  const fallback = {
    schema_version: 3,
    id: "WDT-20260716T000003Z-DDDDDD",
    repo: "acme/beta",
    source: {
      kind: "github_issue_fallback",
      repo: "acme/task-hub",
      number: 99,
      fallback_reason: "No owning repository"
    },
    created_by: "creator",
    priority: "P3",
    dependencies: [],
    active_agents: [],
    created_at: "2026-07-16T00:00:03Z",
    derived: { state: "done", dependencies: [] },
    delivery: {
      status: "fresh",
      delivery_state: "verification_needed",
      title: "Hub fallback item",
      body: "Fallback Issue body",
      url: "https://github.com/acme/task-hub/issues/99",
      owners: [],
      prs: []
    },
    completion: {
      outcome: "done",
      completed_at: "2026-07-16T00:01:00Z",
      completed_by: "creator",
      result: "Done",
      evidence: ["Verified"],
      participants: []
    }
  };
  const cycleFirst = {
    schema_version: 3,
    id: "WDT-20260716T000004Z-EEEEEE",
    repo: "acme/beta",
    source: { kind: "github_issue", repo: "acme/beta", number: 43 },
    created_by: "creator",
    priority: "P3",
    dependencies: ["WDT-20260716T000005Z-FFFFFF"],
    active_agents: [],
    created_at: "2026-07-16T00:00:04Z",
    derived: { state: "blocked", dependencies: [] },
    delivery: {
      status: "fresh",
      delivery_state: "unstarted",
      title: "Cycle first",
      body: "Cycle fixture",
      url: "https://github.com/acme/beta/issues/43",
      owners: [],
      prs: []
    }
  };
  const cycleSecond = {
    schema_version: 3,
    id: "WDT-20260716T000005Z-FFFFFF",
    repo: "acme/beta",
    source: { kind: "github_issue", repo: "acme/beta", number: 44 },
    created_by: "creator",
    priority: "P3",
    dependencies: [cycleFirst.id],
    active_agents: [],
    created_at: "2026-07-16T00:00:05Z",
    derived: { state: "blocked", dependencies: [] },
    delivery: {
      status: "fresh",
      delivery_state: "unstarted",
      title: "Cycle second",
      body: "Cycle fixture",
      url: "https://github.com/acme/beta/issues/44",
      owners: [],
      prs: []
    }
  };
  return {
    generated_at: "2026-07-16T01:00:00Z",
    hub_repo: "acme/task-hub",
    repositories: ["acme/alpha", "acme/beta"],
    open_tasks: [issue, text, pullRequest, cycleFirst, cycleSecond],
    archived_tasks: [fallback]
  };
}

function descendants(root) {
  const result = [];
  function visit(node) {
    result.push(node);
    node.children.forEach(visit);
  }
  root.children.forEach(visit);
  return result;
}

function renderedNodes(graph) {
  return descendants(graph).filter(function (node) {
    return node.attributes["data-task-id"];
  });
}

function renderedEdges(graph) {
  return descendants(graph).filter(function (node) {
    return node.attributes["data-edge"] === "true";
  });
}

function edgeKeys(graph) {
  return renderedEdges(graph).map(function (edge) {
    return edge.attributes["data-from"] + "->" + edge.attributes["data-to"];
  });
}

async function settle() {
  await Promise.resolve();
  await Promise.resolve();
  await new Promise(function (resolve) {
    setImmediate(resolve);
  });
}

async function main() {
  const ids = [
    "dag-graph",
    "dag-empty-state",
    "dag-error-state",
    "dag-summary",
    "dag-legend",
    "dag-repo-filter",
    "dag-refresh",
    "sync-status",
    "generated-at",
    "hub-link"
  ];
  const elements = {};
  ids.forEach(function (id) {
    elements[id] = new FakeElement(id === "dag-repo-filter" ? "select" : "div", id);
  });
  const document = {
    getElementById: function (id) {
      return elements[id];
    },
    createElement: function (tagName) {
      return new FakeElement(tagName);
    },
    createElementNS: function (namespace, tagName) {
      assert.equal(namespace, "http://www.w3.org/2000/svg");
      return new FakeElement(tagName);
    }
  };
  const context = {
    Array: Array,
    Date: Date,
    Error: Error,
    Intl: Intl,
    Math: Math,
    Number: Number,
    Object: Object,
    Promise: Promise,
    String: String,
    document: document,
    fetch: function () {
      return Promise.resolve({
        ok: true,
        json: function () {
          return Promise.resolve(snapshot());
        }
      });
    },
    window: {
      setInterval: function () {
        return 0;
      }
    }
  };

  const appPath = process.argv[2];
  vm.runInNewContext(fs.readFileSync(appPath, "utf8"), context, {
    filename: appPath
  });
  await settle();

  const issueId = "WDT-20260716T000000Z-AAAAAA";
  const textId = "WDT-20260716T000001Z-BBBBBB";
  const prId = "WDT-20260716T000002Z-CCCCCC";
  assert.deepEqual(
    elements["dag-repo-filter"].children.map(function (option) {
      return option.value;
    }),
    ["", "acme/alpha", "acme/beta"]
  );
  assert.equal(renderedNodes(elements["dag-graph"]).length, 6);
  assert.equal(renderedEdges(elements["dag-graph"]).length, 4);
  assert.ok(edgeKeys(elements["dag-graph"]).includes(issueId + "->" + prId));

  const labels = {};
  const colors = {};
  renderedNodes(elements["dag-graph"]).forEach(function (node) {
    labels[node.attributes["data-task-id"]] = node.attributes["data-source-label"];
    colors[node.attributes["data-task-repo"]] = node.attributes["data-repo-color"];
  });
  assert.equal(labels[issueId], "Issue #12");
  assert.equal(labels[prId], "PR #7");
  assert.equal(labels[textId], "Issue #13");
  assert.equal(labels["WDT-20260716T000003Z-DDDDDD"], "Issue #99");
  assert.match(
    renderedNodes(elements["dag-graph"]).find(function (node) {
      return node.attributes["data-task-id"] === prId;
    }).attributes["aria-label"],
    /Cross-repository delivery/
  );
  assert.equal(
    renderedNodes(elements["dag-graph"]).find(function (node) {
      return node.attributes["data-task-id"] === prId;
    }).attributes.href,
    "https://github.com/acme/beta/pull/7"
  );
  assert.notEqual(colors["acme/alpha"], colors["acme/beta"]);
  assert.match(
    elements["dag-summary"].textContent,
    /^6 tasks · 4 dependencies · 2 repositories · 1 cycle/
  );

  elements["dag-repo-filter"].value = "acme/alpha";
  elements["dag-repo-filter"].listeners.change();
  assert.deepEqual(
    renderedNodes(elements["dag-graph"]).map(function (node) {
      return node.attributes["data-task-id"];
    }).sort(),
    [issueId, textId].sort()
  );
  assert.deepEqual(edgeKeys(elements["dag-graph"]), [issueId + "->" + textId]);
  assert.ok(!edgeKeys(elements["dag-graph"]).includes(issueId + "->" + prId));
  assert.match(elements["dag-summary"].textContent, /^2 tasks · 1 dependency · 1 repository/);
}

main().catch(function (error) {
  console.error(error.stack || error);
  process.exitCode = 1;
});
