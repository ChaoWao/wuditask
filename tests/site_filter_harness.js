"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

class FakeClassList {
  constructor() {
    this.values = new Set();
  }

  toggle(name, enabled) {
    if (enabled) {
      this.values.add(name);
    } else {
      this.values.delete(name);
    }
  }
}

class FakeElement {
  constructor(tagName, id) {
    this.tagName = String(tagName || "div").toUpperCase();
    this.id = id || "";
    this.children = [];
    this.listeners = {};
    this.dataset = {};
    this.classList = new FakeClassList();
    this.attributes = {};
    this.value = "";
    this.textContent = "";
    this.hidden = false;
    this.disabled = false;
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
    this.attributes[name] = value;
  }
}

function openTask(repo, state) {
  return {
    schema_version: 3,
    id: "WDT-OPEN",
    repo: repo,
    priority: "P1",
    source: { kind: "github_issue", repo: repo, number: 17 },
    created_by: "creator",
    created_at: "2026-07-16T00:00:00Z",
    dependencies: ["WDT-DEPENDENCY"],
    active_agents: [{ login: "runner" }],
    derived: {
      state: state,
      dependencies: [
        {
          id: "WDT-DEPENDENCY",
          exists: true,
          location: "archive",
          complete: true,
          reason: "archived as done with evidence",
          repo: "shared/base",
          source: { kind: "github_issue", repo: "shared/base", number: 3 },
          active_agents: [],
          outcome: "done",
          evidence: ["Dependency CI passed"]
        }
      ]
    },
    delivery: {
      status: "fresh",
      delivery_state: "review",
      title: "Canonical open task",
      body: "Acceptance lives in the canonical Issue.",
      url: "https://github.com/" + repo + "/issues/17",
      owners: ["alice", "bob"],
      prs: [
        {
          repo: repo,
          number: 18,
          url: "https://github.com/" + repo + "/pull/18",
          state: "OPEN",
          author: "alice",
          title: "Linked implementation",
          review_decision: "REVIEW_REQUIRED",
          checks: { successful: 2, total: 3, pending: 1, failed: 0 }
        }
      ]
    }
  };
}

function archivedTask(repo, outcome) {
  return {
    schema_version: 3,
    id: "WDT-ARCHIVE",
    repo: repo,
    priority: "P2",
    source: { kind: "github_pull_request", repo: repo, number: 21 },
    created_by: "creator",
    created_at: "2026-07-15T00:00:00Z",
    dependencies: [],
    active_agents: [],
    derived: { state: outcome, dependencies: [] },
    delivery: {
      status: "fresh",
      delivery_state: "ready_to_merge",
      title: "Canonical archived task",
      body: "The pull request is the task itself.",
      url: "https://github.com/" + repo + "/pull/21",
      owners: ["carol"],
      prs: []
    },
    completion: {
      outcome: outcome,
      result: "Complete.",
      completed_at: "2026-07-16T00:00:00Z",
      completed_by: "carol",
      evidence: ["CI run 123 passed", "Reviewed by maintainer"],
      participants: [
        { login: "carol" },
        { login: "dave" }
      ]
    }
  };
}

function snapshot(openRepo, openState) {
  return {
    generated_at: "2026-07-16T00:00:00Z",
    hub_repo: "acme/tasks",
    counts: {
      open: 1,
      ready: openState === "ready" ? 1 : 0,
      in_progress: openState === "in_progress" ? 1 : 0,
      blocked: openState === "blocked" ? 1 : 0,
      archived: 1
    },
    repositories: [openRepo, "archive/repo"].sort(),
    open_tasks: [openTask(openRepo, openState)],
    archived_tasks: [archivedTask("archive/repo", "done")]
  };
}

function optionValues(control) {
  return control.children.map(function (option) {
    return option.value;
  });
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

function renderedText(root) {
  return descendants(root)
    .map(function (node) {
      return node.textContent || "";
    })
    .join(" ");
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
    "task-list",
    "empty-state",
    "error-state",
    "search",
    "repo-filter",
    "state-filter",
    "delivery-filter",
    "refresh",
    "count-open",
    "count-ready",
    "count-progress",
    "count-blocked",
    "count-archived",
    "generated-at",
    "sync-status",
    "hub-link"
  ];
  const elements = {};
  ids.forEach(function (id) {
    const tagName = id.endsWith("-filter") ? "select" : "div";
    elements[id] = new FakeElement(tagName, id);
  });
  const openButton = new FakeElement("button");
  openButton.dataset.view = "open";
  const archiveButton = new FakeElement("button");
  archiveButton.dataset.view = "archive";
  const viewButtons = [openButton, archiveButton];
  const document = {
    getElementById: function (id) {
      return elements[id];
    },
    querySelectorAll: function (selector) {
      return selector === "[data-view]" ? viewButtons : [];
    },
    createElement: function (tagName) {
      return new FakeElement(tagName);
    },
    createTextNode: function (text) {
      const node = new FakeElement("#text");
      node.textContent = text;
      return node;
    }
  };
  let currentSnapshot = snapshot("open/repo", "ready");
  const context = {
    Date: Date,
    Error: Error,
    Intl: Intl,
    Number: Number,
    Promise: Promise,
    URL: URL,
    document: document,
    encodeURIComponent: encodeURIComponent,
    fetch: function () {
      return Promise.resolve({
        ok: true,
        json: function () {
          return Promise.resolve(currentSnapshot);
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

  assert.deepEqual(optionValues(elements["repo-filter"]), ["", "open/repo"]);
  assert.deepEqual(optionValues(elements["state-filter"]), ["", "ready"]);
  assert.deepEqual(optionValues(elements["delivery-filter"]), ["", "review"]);
  const openSummary = elements["task-list"].children[0].children[0];
  assert.equal(openSummary.children.length, 7);
  assert.match(renderedText(openSummary.children[3]), /alice, bob/);
  assert.match(renderedText(openSummary.children[4]), /runner/);
  assert.match(renderedText(elements["task-list"]), /Canonical open task/);
  assert.match(renderedText(elements["task-list"]), /alice, bob/);
  assert.match(renderedText(elements["task-list"]), /runner/);
  assert.match(renderedText(elements["task-list"]), /Acceptance lives in the canonical Issue/);
  assert.match(renderedText(elements["task-list"]), /WDT-DEPENDENCY/);
  assert.match(renderedText(elements["task-list"]), /archived as done with evidence/);
  assert.doesNotMatch(renderedText(elements["task-list"]), /WDX-111/);
  assert.ok(
    descendants(elements["task-list"]).some(function (node) {
      return node.href === "https://github.com/open/repo/issues/17";
    })
  );
  elements.search.value = "alice";
  elements.search.listeners.input();
  assert.equal(elements["task-list"].children.length, 1);
  elements.search.value = "acceptance lives";
  elements.search.listeners.input();
  assert.equal(elements["task-list"].children.length, 1);
  elements.search.value = "not present";
  elements.search.listeners.input();
  assert.equal(elements["task-list"].children.length, 0);
  elements.search.value = "";
  elements.search.listeners.input();

  archiveButton.listeners.click();
  const archivedSummary = elements["task-list"].children[0].children[0];
  assert.match(renderedText(archivedSummary.children[3]), /carol/);
  assert.match(renderedText(archivedSummary.children[4]), /Finished/);
  assert.deepEqual(optionValues(elements["repo-filter"]), ["", "archive/repo"]);
  assert.deepEqual(optionValues(elements["state-filter"]), ["", "done"]);
  assert.deepEqual(optionValues(elements["delivery-filter"]), ["", "ready_to_merge"]);
  assert.match(renderedText(elements["task-list"]), /CI run 123 passed/);
  assert.match(renderedText(elements["task-list"]), /carol, dave/);
  assert.doesNotMatch(renderedText(elements["task-list"]), /WDX-222/);
  elements.search.value = "reviewed by maintainer";
  elements.search.listeners.input();
  assert.equal(elements["task-list"].children.length, 1);
  elements.search.value = "dave";
  elements.search.listeners.input();
  assert.equal(elements["task-list"].children.length, 1);
  elements.search.value = "";
  elements.search.listeners.input();

  openButton.listeners.click();
  elements["repo-filter"].value = "open/repo";
  elements["state-filter"].value = "ready";
  currentSnapshot = snapshot("new/repo", "blocked");
  elements.refresh.listeners.click();
  await settle();

  assert.deepEqual(optionValues(elements["repo-filter"]), ["", "new/repo"]);
  assert.equal(elements["repo-filter"].value, "");
  assert.deepEqual(optionValues(elements["state-filter"]), ["", "blocked"]);
  assert.equal(elements["state-filter"].value, "");

  currentSnapshot.open_tasks[0].delivery.owners = [];
  elements.refresh.listeners.click();
  await settle();
  assert.match(
    renderedText(elements["task-list"].children[0].children[0].children[3]),
    /Unassigned/
  );

  currentSnapshot = snapshot("unknown/repo", "ready");
  currentSnapshot.open_tasks[0].delivery.status = "unavailable";
  currentSnapshot.open_tasks[0].delivery.delivery_state = "unavailable";
  currentSnapshot.open_tasks[0].delivery.owners = null;
  currentSnapshot.open_tasks[0].delivery.error = "GitHub API unavailable";
  elements.refresh.listeners.click();
  await settle();
  assert.match(
    renderedText(elements["task-list"].children[0].children[0].children[3]),
    /Unknown/
  );
  assert.match(renderedText(elements["task-list"]), /Unknown/);
  assert.doesNotMatch(renderedText(elements["task-list"]), /Unassigned/);
}

main().catch(function (error) {
  console.error(error.stack || error);
  process.exitCode = 1;
});
