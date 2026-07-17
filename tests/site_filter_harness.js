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
    id: "WDT-OPEN",
    title: "Open task",
    repo: repo,
    goal: "Exercise open filters.",
    priority: "P1",
    source: { kind: "text", reason: "test" },
    claim: null,
    context: [],
    acceptance_criteria: [],
    links: [],
    derived: { state: state, claim_holder: null, dependencies: [] },
    delivery: {
      status: "fresh",
      delivery_state: "text_only",
      assignees: [],
      prs: []
    }
  };
}

function archivedTask(repo, outcome) {
  return {
    id: "WDT-ARCHIVE",
    title: "Archived task",
    repo: repo,
    goal: "Exercise archive filters.",
    priority: "P2",
    source: { kind: "text", reason: "test" },
    claim: null,
    context: [],
    acceptance_criteria: [],
    links: [],
    derived: { state: outcome, claim_holder: null, dependencies: [] },
    delivery: {
      status: "fresh",
      delivery_state: "text_only",
      assignees: [],
      prs: []
    },
    completion: {
      outcome: outcome,
      result: "Complete.",
      completed_at: "2026-07-16T00:00:00Z",
      completed_by: { login: "alice" },
      acceptance_results: []
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
  assert.deepEqual(optionValues(elements["delivery-filter"]), ["", "text_only"]);

  archiveButton.listeners.click();
  assert.deepEqual(optionValues(elements["repo-filter"]), ["", "archive/repo"]);
  assert.deepEqual(optionValues(elements["state-filter"]), ["", "done"]);
  assert.deepEqual(optionValues(elements["delivery-filter"]), ["", "text_only"]);

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
}

main().catch(function (error) {
  console.error(error.stack || error);
  process.exitCode = 1;
});
