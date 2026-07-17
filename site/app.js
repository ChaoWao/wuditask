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
      passed: "Passed",
      skipped: "Skipped",
      unstarted: "Unstarted",
      assigned: "Assigned",
      implementing: "Implementing",
      review: "Review",
      ready_to_merge: "Ready to merge",
      verification_needed: "Verification needed",
      text_only: "Text only",
      unavailable: "Unavailable"
    };
    return names[value] || value;
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

  function claimNode(owner) {
    var wrapper = element("span", "owner");
    if (!owner) {
      wrapper.appendChild(element("span", "owner-placeholder", "-"));
      wrapper.appendChild(element("span", "owner-name", "Unclaimed"));
      return wrapper;
    }
    var image = element("img");
    image.src = "https://github.com/" + encodeURIComponent(owner.login) + ".png?size=64";
    image.alt = "";
    image.loading = "lazy";
    image.referrerPolicy = "no-referrer";
    wrapper.appendChild(image);
    wrapper.appendChild(element("span", "owner-name", owner.login));
    return wrapper;
  }

  function statusNode(state) {
    return element("span", "state state-" + state, displayState(state));
  }

  function section(title) {
    var wrapper = element("section", "detail-section");
    wrapper.appendChild(element("h2", "", title));
    return wrapper;
  }

  function appendContexts(parent, values) {
    if (!values || !values.length) {
      parent.appendChild(element("p", "", "No additional context."));
      return;
    }
    var ul = element("ul", "detail-list");
    values.forEach(function (value) {
      ul.appendChild(element("li", "", value));
    });
    parent.appendChild(ul);
  }

  function appendCriteria(parent, criteria, results) {
    var resultMap = {};
    (results || []).forEach(function (result) {
      resultMap[result.criterion_id] = result;
    });
    criteria.forEach(function (criterion) {
      var item = element("div", "criterion");
      var head = element("div");
      head.appendChild(element("span", "criterion-id", criterion.id));
      head.appendChild(document.createTextNode("  " + criterion.description));
      item.appendChild(head);
      item.appendChild(
        element(
          "span",
          "verification",
          criterion.verification.type + ": " + criterion.verification.value
        )
      );
      if (resultMap[criterion.id]) {
        var result = resultMap[criterion.id];
        item.appendChild(
          element(
            "span",
            "evidence",
            displayState(result.status) + ": " + result.evidence
          )
        );
      }
      parent.appendChild(item);
    });
  }

  function appendDependencies(parent, dependencies) {
    if (!dependencies || !dependencies.length) {
      parent.appendChild(element("p", "", "No dependencies."));
      return;
    }
    dependencies.forEach(function (dependency) {
      var item = element("div", "dependency");
      var head = element("div", "dependency-head");
      var title = dependency.exists
        ? dependency.title + " (" + dependency.repo + ")"
        : dependency.id;
      head.appendChild(element("span", "dependency-title", title));
      head.appendChild(statusNode(dependency.complete ? "done" : "blocked"));
      item.appendChild(head);
      item.appendChild(element("div", "task-id", dependency.id));
      item.appendChild(element("div", "dependency-reason", dependency.reason));
      if (dependency.goal) {
        item.appendChild(element("div", "dependency-reason", "Goal: " + dependency.goal));
      }
      parent.appendChild(item);
    });
  }

  function appendLinks(parent, links) {
    var safeLinks = (links || []).filter(function (value) {
      try {
        var url = new URL(value);
        return url.protocol === "https:" || url.protocol === "http:";
      } catch (ignored) {
        return false;
      }
    });
    if (!safeLinks.length) {
      parent.appendChild(element("p", "", "No links."));
      return;
    }
    var wrapper = element("div", "links");
    safeLinks.forEach(function (value, index) {
      var link = element("a", "", "Link " + (index + 1));
      link.href = value;
      link.target = "_blank";
      link.rel = "noreferrer";
      wrapper.appendChild(link);
    });
    parent.appendChild(wrapper);
  }

  function appendSource(parent, task) {
    var source = task.source || {};
    var delivery = task.delivery || {};
    if (delivery.url) {
      var link = element("a", "", delivery.url);
      link.href = delivery.url;
      link.target = "_blank";
      link.rel = "noreferrer";
      parent.appendChild(link);
    } else {
      parent.appendChild(element("p", "", "Text source: " + (source.reason || "No reason recorded.")));
    }
    if (source.fallback_reason) {
      parent.appendChild(element("span", "verification", "Fallback: " + source.fallback_reason));
    }
  }

  function appendDelivery(parent, delivery) {
    if (!delivery) {
      parent.appendChild(element("p", "", "Delivery state unavailable."));
      return;
    }
    parent.appendChild(statusNode(delivery.delivery_state));
    if (delivery.assignees && delivery.assignees.length) {
      parent.appendChild(element("span", "verification", "Assignees: " + delivery.assignees.join(", ")));
    }
    (delivery.prs || []).forEach(function (pr) {
      var row = element("div", "delivery-pr");
      var link = element("a", "", pr.repo + "#" + pr.number);
      link.href = pr.url;
      link.target = "_blank";
      link.rel = "noreferrer";
      row.appendChild(link);
      row.appendChild(document.createTextNode(" · " + pr.state + (pr.author ? " · " + pr.author : "")));
      if (pr.checks) {
        row.appendChild(
          element(
            "span",
            "verification",
            "Checks: " + pr.checks.successful + "/" + pr.checks.total +
              " passed, " + pr.checks.pending + " pending, " + pr.checks.failed + " failed"
          )
        );
      }
      parent.appendChild(row);
    });
    if (delivery.error) {
      parent.appendChild(element("span", "evidence", delivery.error));
    }
    if (delivery.fetched_at) {
      parent.appendChild(element("span", "verification", "Fetched " + formattedDate(delivery.fetched_at)));
    }
  }

  function taskRow(task, archived) {
    var derived = archived ? null : task.derived;
    var state = archived ? task.completion.outcome : derived.state;
    var deliveryState = task.delivery ? task.delivery.delivery_state : "unavailable";
    var details = element("details", "task-row");
    var summary = element("summary", "task-summary");
    summary.appendChild(element("span", "priority priority-" + task.priority, task.priority));

    var name = element("span", "task-name");
    name.appendChild(element("span", "task-title", task.title));
    name.appendChild(element("span", "task-id", task.id));
    summary.appendChild(name);
    summary.appendChild(element("span", "repo-name", task.repo));
    summary.appendChild(claimNode(task.derived.claim_holder));
    summary.appendChild(statusNode(state));
    summary.appendChild(statusNode(deliveryState));
    details.appendChild(summary);

    var body = element("div", "task-detail");
    var grid = element("div", "detail-grid");
    var left = element("div");
    var right = element("div");

    var goal = section("Goal");
    goal.appendChild(element("p", "", task.goal));
    left.appendChild(goal);

    var context = section("Context");
    appendContexts(context, task.context);
    left.appendChild(context);

    var source = section("Canonical source");
    appendSource(source, task);
    left.appendChild(source);

    var delivery = section("GitHub delivery");
    appendDelivery(delivery, task.delivery);
    left.appendChild(delivery);

    if (archived) {
      var completion = section("Completion");
      completion.appendChild(element("p", "", task.completion.result));
      completion.appendChild(
        element(
          "span",
          "verification",
          formattedDate(task.completion.completed_at) +
            " by " +
            task.completion.completed_by.login
        )
      );
      left.appendChild(completion);
    }

    var acceptance = section("Acceptance");
    appendCriteria(
      acceptance,
      task.acceptance_criteria,
      archived ? task.completion.acceptance_results : []
    );
    right.appendChild(acceptance);

    var dependencies = section("Dependencies");
    appendDependencies(dependencies, task.derived.dependencies);
    right.appendChild(dependencies);

    var links = section("Links");
    appendLinks(links, task.links);
    right.appendChild(links);

    grid.appendChild(left);
    grid.appendChild(right);
    body.appendChild(grid);
    details.appendChild(body);
    return details;
  }

  function searchText(task, archived) {
    var values = [
      task.id,
      task.title,
      task.repo,
      task.goal,
      task.derived.claim_holder ? task.derived.claim_holder.login : "",
      (task.delivery.assignees || []).join(" "),
      task.delivery.delivery_state,
      (task.context || []).join(" "),
      (task.acceptance_criteria || [])
        .map(function (criterion) {
          return criterion.description + " " + criterion.verification.value;
        })
        .join(" ")
    ];
    if (archived) {
      values.push(task.completion.result);
    }
    return values.join(" ").toLowerCase();
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
        (!selectedDelivery || task.delivery.delivery_state === selectedDelivery)
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
    if (repositories.indexOf(selected) !== -1) {
      repoFilter.value = selected;
    }
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
    stateFilter.replaceChildren();
    values.forEach(function (entry) {
      var option = element("option", "", entry[1]);
      option.value = entry[0];
      stateFilter.appendChild(option);
    });
    if (present[selected]) {
      stateFilter.value = selected;
    }
  }

  function updateDeliveryOptions() {
    var selected = deliveryFilter.value;
    var present = {};
    viewTasks().forEach(function (task) {
      present[task.delivery.delivery_state] = true;
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
    if (present[selected]) {
      deliveryFilter.value = selected;
    }
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
