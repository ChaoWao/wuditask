(function () {
  "use strict";

  var snapshot = null;
  var view = "open";
  var list = document.getElementById("task-list");
  var empty = document.getElementById("empty-state");
  var error = document.getElementById("error-state");
  var search = document.getElementById("search");
  var repoFilter = document.getElementById("repo-filter");
  var stateFilter = document.getElementById("state-filter");
  var deliveryFilter = document.getElementById("delivery-filter");
  var refresh = document.getElementById("refresh");

  function element(tag, className, text) {
    var node = document.createElement(tag);
    if (className) {
      node.className = className;
    }
    if (text !== undefined && text !== null) {
      node.textContent = String(text);
    }
    return node;
  }

  function displayState(value) {
    var names = {
      ready: "Ready",
      in_progress: "In progress",
      blocked: "Blocked",
      done: "Done",
      failed: "Failed",
      cancelled: "Cancelled",
      unstarted: "Unstarted",
      assigned: "Assigned",
      implementing: "Implementing",
      review: "Review",
      ready_to_merge: "Ready to merge",
      verification_needed: "Verification needed",
      unavailable: "Unavailable"
    };
    return names[value] || String(value || "Unknown").replace(/_/g, " ");
  }

  function formattedDate(value) {
    var date = new Date(value);
    if (Number.isNaN(date.getTime())) {
      return value || "";
    }
    return new Intl.DateTimeFormat(undefined, {
      dateStyle: "medium",
      timeStyle: "short"
    }).format(date);
  }

  function uniqueLogins(values) {
    var seen = {};
    var result = [];
    (values || []).forEach(function (value) {
      var login = typeof value === "string" ? value : value && value.login;
      if (!login || seen[login.toLowerCase()]) {
        return;
      }
      seen[login.toLowerCase()] = true;
      result.push(login);
    });
    return result;
  }

  function identityNode(logins, emptyLabel) {
    var names = uniqueLogins(logins);
    var wrapper = element("span", "owner");
    if (!names.length) {
      wrapper.appendChild(element("span", "owner-placeholder", "-"));
      wrapper.appendChild(element("span", "owner-name", emptyLabel));
      return wrapper;
    }
    var image = element("img");
    image.src = "https://github.com/" + encodeURIComponent(names[0]) + ".png?size=64";
    image.alt = "";
    image.loading = "lazy";
    image.referrerPolicy = "no-referrer";
    wrapper.appendChild(image);
    wrapper.appendChild(element("span", "owner-name", names.join(", ")));
    return wrapper;
  }

  function activeLogins(task) {
    return uniqueLogins(task.active_agents);
  }

  function owners(task) {
    return uniqueLogins(task.delivery && task.delivery.owners);
  }

  function statusNode(state) {
    state = state || "unavailable";
    return element("span", "state state-" + state, displayState(state));
  }

  function section(title) {
    var wrapper = element("section", "detail-section");
    wrapper.appendChild(element("h2", "", title));
    return wrapper;
  }

  function safeUrl(value) {
    try {
      var parsed = new URL(value);
      return parsed.protocol === "https:" || parsed.protocol === "http:" ? value : null;
    } catch (ignored) {
      return null;
    }
  }

  function sourceLabel(task) {
    var source = task.source || {};
    if (source.kind === "github_pull_request" && source.number) {
      return "PR #" + source.number;
    }
    if (
      (source.kind === "github_issue" || source.kind === "github_issue_fallback") &&
      source.number
    ) {
      return "Issue #" + source.number;
    }
    return task.id;
  }

  function taskTitle(task) {
    return (task.delivery && task.delivery.title) || sourceLabel(task);
  }

  function deliveryState(task) {
    return (task.delivery && task.delivery.delivery_state) || "unavailable";
  }

  function appendCanonicalSource(parent, task) {
    var delivery = task.delivery || {};
    var url = safeUrl(delivery.url);
    var heading = sourceLabel(task) + " · " + taskTitle(task);
    if (url) {
      var link = element("a", "", heading);
      link.href = url;
      link.target = "_blank";
      link.rel = "noreferrer";
      parent.appendChild(link);
    } else {
      parent.appendChild(element("p", "", heading));
    }
    parent.appendChild(
      element(
        "p",
        "",
        delivery.body || "Canonical source body is unavailable in this snapshot."
      )
    );
    if (task.source && task.source.fallback_reason) {
      parent.appendChild(
        element("span", "verification", "Hub fallback: " + task.source.fallback_reason)
      );
    }
  }

  function appendOwnership(parent, task) {
    var ownerNames = owners(task);
    var agentNames = activeLogins(task);
    var ownerEmptyLabel =
      task.delivery && task.delivery.status === "fresh" ? "Unassigned" : "Unknown";
    parent.appendChild(element("span", "verification", "Owners"));
    parent.appendChild(identityNode(ownerNames, ownerEmptyLabel));
    parent.appendChild(element("span", "verification", "Active agents"));
    parent.appendChild(identityNode(agentNames, "No active agent"));
  }

  function appendChecks(parent, checks) {
    if (!checks || typeof checks !== "object") {
      return;
    }
    var parts = [];
    if (typeof checks.successful === "number" && typeof checks.total === "number") {
      parts.push(checks.successful + "/" + checks.total + " passed");
    }
    if (checks.pending) {
      parts.push(checks.pending + " pending");
    }
    if (checks.failed) {
      parts.push(checks.failed + " failed");
    }
    if (parts.length) {
      parent.appendChild(element("span", "verification", "Checks: " + parts.join(", ")));
    }
  }

  function appendDelivery(parent, delivery) {
    if (!delivery) {
      parent.appendChild(statusNode("unavailable"));
      return;
    }
    parent.appendChild(statusNode(delivery.delivery_state));
    (delivery.prs || []).forEach(function (pr) {
      var row = element("div", "delivery-pr");
      var label = (pr.repo || "PR") + (pr.number ? "#" + pr.number : "");
      var url = safeUrl(pr.url);
      if (url) {
        var link = element("a", "", label);
        link.href = url;
        link.target = "_blank";
        link.rel = "noreferrer";
        row.appendChild(link);
      } else {
        row.appendChild(element("span", "", label));
      }
      var facts = [];
      if (pr.state) {
        facts.push(pr.state);
      }
      if (pr.is_draft) {
        facts.push("draft");
      }
      if (pr.author) {
        facts.push("by " + pr.author);
      }
      if (facts.length) {
        row.appendChild(document.createTextNode(" · " + facts.join(" · ")));
      }
      if (pr.title) {
        row.appendChild(element("span", "verification", pr.title));
      }
      if (pr.review_decision) {
        row.appendChild(
          element("span", "verification", "Review: " + displayState(pr.review_decision))
        );
      }
      if (pr.merge_state_status) {
        row.appendChild(
          element("span", "verification", "Merge: " + displayState(pr.merge_state_status))
        );
      }
      appendChecks(row, pr.checks);
      parent.appendChild(row);
    });
    if (delivery.error) {
      parent.appendChild(element("span", "evidence", delivery.error));
    }
    if (delivery.fetched_at) {
      parent.appendChild(
        element("span", "verification", "Fetched " + formattedDate(delivery.fetched_at))
      );
    }
  }

  function dependenciesFor(task) {
    var expanded = task.derived && task.derived.dependencies;
    if (Array.isArray(expanded) && (expanded.length || !(task.dependencies || []).length)) {
      return expanded;
    }
    return (task.dependencies || []).map(function (id) {
      return { id: id, exists: false, complete: false, reason: "Not expanded" };
    });
  }

  function appendDependencies(parent, dependencies) {
    if (!dependencies.length) {
      parent.appendChild(element("p", "", "No dependencies."));
      return;
    }
    dependencies.forEach(function (dependency) {
      var item = element("div", "dependency");
      var head = element("div", "dependency-head");
      var label = dependency.delivery_title || dependency.title || dependency.id;
      if (dependency.repo) {
        label += " (" + dependency.repo + ")";
      }
      head.appendChild(element("span", "dependency-title", label));
      head.appendChild(statusNode(dependency.complete ? "done" : "blocked"));
      item.appendChild(head);
      item.appendChild(element("div", "task-id", dependency.id));
      if (dependency.reason) {
        item.appendChild(element("div", "dependency-reason", dependency.reason));
      }
      parent.appendChild(item);
    });
  }

  function appendCompletion(parent, completion) {
    parent.appendChild(element("p", "", completion.result));
    parent.appendChild(
      element(
        "span",
        "verification",
        formattedDate(completion.completed_at) + " by " + completion.completed_by
      )
    );
    var participantNames = uniqueLogins(completion.participants);
    parent.appendChild(
      element(
        "span",
        "verification",
        "Participants: " + (participantNames.length ? participantNames.join(", ") : "None")
      )
    );
    if (completion.evidence && completion.evidence.length) {
      var evidence = element("ul", "detail-list");
      completion.evidence.forEach(function (value) {
        evidence.appendChild(element("li", "", value));
      });
      parent.appendChild(evidence);
    } else {
      parent.appendChild(element("span", "evidence", "No completion evidence recorded."));
    }
  }

  function appendCoordination(parent, task) {
    parent.appendChild(element("p", "", "Execution repository: " + task.repo));
    parent.appendChild(
      element("span", "verification", "Created by " + task.created_by + " · " + formattedDate(task.created_at))
    );
  }

  function taskRow(task, archived) {
    var state = archived
      ? task.completion.outcome
      : (task.derived && task.derived.state) || "unavailable";
    var details = element("details", "task-row");
    var summary = element("summary", "task-summary");
    summary.appendChild(element("span", "priority priority-" + task.priority, task.priority));

    var name = element("span", "task-name");
    name.appendChild(element("span", "task-title", taskTitle(task)));
    name.appendChild(element("span", "task-id", task.id));
    summary.appendChild(name);
    summary.appendChild(element("span", "repo-name", task.repo));
    summary.appendChild(identityNode(activeLogins(task), archived ? "Finished" : "Idle"));
    summary.appendChild(statusNode(state));
    summary.appendChild(statusNode(deliveryState(task)));
    details.appendChild(summary);

    var body = element("div", "task-detail");
    var grid = element("div", "detail-grid");
    var left = element("div");
    var right = element("div");

    var canonical = section("Canonical source");
    appendCanonicalSource(canonical, task);
    left.appendChild(canonical);

    var responsibility = section("Responsibility and execution");
    appendOwnership(responsibility, task);
    left.appendChild(responsibility);

    var delivery = section("GitHub delivery");
    appendDelivery(delivery, task.delivery);
    left.appendChild(delivery);

    if (archived) {
      var completion = section("Archive result and evidence");
      appendCompletion(completion, task.completion);
      left.appendChild(completion);
    }

    var dependencies = section("Dependencies");
    appendDependencies(dependencies, dependenciesFor(task));
    right.appendChild(dependencies);

    var coordination = section("Hub coordination");
    appendCoordination(coordination, task);
    right.appendChild(coordination);

    grid.appendChild(left);
    grid.appendChild(right);
    body.appendChild(grid);
    details.appendChild(body);
    return details;
  }

  function searchText(task, archived) {
    var delivery = task.delivery || {};
    var source = task.source || {};
    var values = [
      task.id,
      task.repo,
      task.created_by,
      source.repo,
      source.number,
      sourceLabel(task),
      taskTitle(task),
      delivery.body,
      delivery.delivery_state,
      owners(task).join(" "),
      activeLogins(task).join(" ")
    ];
    (delivery.prs || []).forEach(function (pr) {
      values.push(
        pr.repo,
        pr.number,
        pr.author,
        pr.title,
        pr.body,
        pr.state,
        pr.review_decision,
        pr.merge_state_status,
        uniqueLogins(pr.assignees).join(" ")
      );
    });
    dependenciesFor(task).forEach(function (dependency) {
      values.push(dependency.id, dependency.repo, dependency.title, dependency.reason);
    });
    if (archived) {
      values.push(
        task.completion.result,
        task.completion.completed_by,
        (task.completion.evidence || []).join(" "),
        uniqueLogins(task.completion.participants).join(" ")
      );
    }
    return values
      .filter(function (value) {
        return value !== undefined && value !== null;
      })
      .join(" ")
      .toLowerCase();
  }

  function render() {
    if (!snapshot) {
      return;
    }
    var archived = view === "archive";
    var tasks = archived ? snapshot.archived_tasks : snapshot.open_tasks;
    var query = search.value.trim().toLowerCase();
    var repo = repoFilter.value;
    var selectedState = stateFilter.value;
    var selectedDelivery = deliveryFilter.value;
    var filtered = tasks.filter(function (task) {
      var taskState = archived ? task.completion.outcome : task.derived.state;
      return (
        (!query || searchText(task, archived).indexOf(query) !== -1) &&
        (!repo || task.repo === repo) &&
        (!selectedState || taskState === selectedState) &&
        (!selectedDelivery || deliveryState(task) === selectedDelivery)
      );
    });

    list.replaceChildren();
    filtered.forEach(function (task) {
      list.appendChild(taskRow(task, archived));
    });
    empty.hidden = filtered.length !== 0;
    error.hidden = true;
  }

  function updateSummary() {
    var counts = snapshot.counts;
    document.getElementById("count-open").textContent = counts.open;
    document.getElementById("count-ready").textContent = counts.ready;
    document.getElementById("count-progress").textContent = counts.in_progress;
    document.getElementById("count-blocked").textContent = counts.blocked;
    document.getElementById("count-archived").textContent = counts.archived;
    document.getElementById("generated-at").textContent =
      "Generated " + formattedDate(snapshot.generated_at);
    document.getElementById("sync-status").textContent =
      "Updated " + formattedDate(snapshot.generated_at);
    var hubLink = document.getElementById("hub-link");
    if (snapshot.hub_repo) {
      hubLink.href = "https://github.com/" + snapshot.hub_repo;
      hubLink.hidden = false;
    } else {
      hubLink.hidden = true;
    }
  }

  function viewTasks() {
    if (!snapshot) {
      return [];
    }
    return view === "archive" ? snapshot.archived_tasks : snapshot.open_tasks;
  }

  function updateRepositories() {
    var selected = repoFilter.value;
    var repositories = [];
    viewTasks().forEach(function (task) {
      if (repositories.indexOf(task.repo) === -1) {
        repositories.push(task.repo);
      }
    });
    repositories.sort();
    repoFilter.replaceChildren();
    var all = element("option", "", "All repositories");
    all.value = "";
    repoFilter.appendChild(all);
    repositories.forEach(function (repo) {
      var option = element("option", "", repo);
      option.value = repo;
      repoFilter.appendChild(option);
    });
    repoFilter.value = repositories.indexOf(selected) !== -1 ? selected : "";
  }

  function updateStateOptions() {
    var selected = stateFilter.value;
    var archived = view === "archive";
    var order = archived
      ? ["done", "failed", "cancelled"]
      : ["ready", "in_progress", "blocked"];
    var present = {};
    viewTasks().forEach(function (task) {
      var state = archived ? task.completion.outcome : task.derived.state;
      present[state] = true;
    });
    var values = [["", archived ? "All outcomes" : "All states"]];
    order.forEach(function (state) {
      if (present[state]) {
        values.push([state, displayState(state)]);
      }
    });
    Object.keys(present).sort().forEach(function (state) {
      if (order.indexOf(state) === -1) {
        values.push([state, displayState(state)]);
      }
    });
    stateFilter.replaceChildren();
    values.forEach(function (entry) {
      var option = element("option", "", entry[1]);
      option.value = entry[0];
      stateFilter.appendChild(option);
    });
    stateFilter.value = present[selected] ? selected : "";
  }

  function updateDeliveryOptions() {
    var selected = deliveryFilter.value;
    var present = {};
    viewTasks().forEach(function (task) {
      present[deliveryState(task)] = true;
    });
    deliveryFilter.replaceChildren();
    var all = element("option", "", "All delivery states");
    all.value = "";
    deliveryFilter.appendChild(all);
    Object.keys(present).sort().forEach(function (state) {
      var option = element("option", "", displayState(state));
      option.value = state;
      deliveryFilter.appendChild(option);
    });
    deliveryFilter.value = present[selected] ? selected : "";
  }

  function updateFilters() {
    updateRepositories();
    updateStateOptions();
    updateDeliveryOptions();
  }

  function load(silent) {
    refresh.disabled = true;
    if (!silent) {
      document.getElementById("sync-status").textContent = "Loading";
    }
    fetch("snapshot.json?ts=" + Date.now(), { cache: "no-store" })
      .then(function (response) {
        if (!response.ok) {
          throw new Error("Snapshot request returned " + response.status);
        }
        return response.json();
      })
      .then(function (data) {
        if (!data || !Array.isArray(data.open_tasks) || !Array.isArray(data.archived_tasks)) {
          throw new Error("Snapshot does not contain task lists");
        }
        snapshot = data;
        updateSummary();
        updateFilters();
        render();
      })
      .catch(function (reason) {
        if (!snapshot) {
          list.replaceChildren();
          empty.hidden = true;
          error.textContent = "Could not load task snapshot: " + reason.message;
          error.hidden = false;
        }
        document.getElementById("sync-status").textContent = "Refresh failed";
      })
      .finally(function () {
        refresh.disabled = false;
      });
  }

  document.querySelectorAll("[data-view]").forEach(function (button) {
    button.addEventListener("click", function () {
      view = button.dataset.view;
      document.querySelectorAll("[data-view]").forEach(function (candidate) {
        var active = candidate === button;
        candidate.classList.toggle("is-active", active);
        candidate.setAttribute("aria-selected", active ? "true" : "false");
      });
      updateFilters();
      render();
    });
  });

  [search, repoFilter, stateFilter, deliveryFilter].forEach(function (control) {
    control.addEventListener(control === search ? "input" : "change", render);
  });
  refresh.addEventListener("click", function () {
    load(false);
  });

  updateFilters();
  load(false);
  window.setInterval(function () {
    load(true);
  }, 60000);
})();
