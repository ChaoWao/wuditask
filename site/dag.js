(function () {
  "use strict";

  var SVG_NS = "http://www.w3.org/2000/svg";
  var NODE_WIDTH = 236;
  var NODE_HEIGHT = 82;
  var LAYER_GAP = 112;
  var ROW_GAP = 34;
  var MARGIN_X = 44;
  var MARGIN_Y = 40;
  var snapshot = null;
  var graph = document.getElementById("dag-graph");
  var empty = document.getElementById("dag-empty-state");
  var error = document.getElementById("dag-error-state");
  var summary = document.getElementById("dag-summary");
  var legend = document.getElementById("dag-legend");
  var repoFilter = document.getElementById("dag-repo-filter");
  var refresh = document.getElementById("dag-refresh");

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

  function svgElement(tag) {
    return document.createElementNS(SVG_NS, tag);
  }

  function setAttributes(node, values) {
    Object.keys(values).forEach(function (name) {
      node.setAttribute(name, String(values[name]));
    });
    return node;
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

  function allTasks(data) {
    return (data.open_tasks || []).concat(data.archived_tasks || []);
  }

  function taskDependencies(task) {
    if (Array.isArray(task.dependencies)) {
      return task.dependencies;
    }
    if (task.derived && Array.isArray(task.derived.dependencies)) {
      return task.derived.dependencies.map(function (dependency) {
        return typeof dependency === "string" ? dependency : dependency.id;
      });
    }
    return [];
  }

  function taskStatus(task) {
    if (task.completion && task.completion.outcome) {
      return task.completion.outcome;
    }
    return task.derived && task.derived.state ? task.derived.state : "unknown";
  }

  function displayStatus(value) {
    return String(value || "unknown").replace(/_/g, " ");
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
    return task.id || "Unknown task";
  }

  function canonicalUrl(task) {
    var source = task.source || {};
    if (!source.repo || !source.number) {
      return null;
    }
    if (source.kind === "github_pull_request") {
      return "https://github.com/" + source.repo + "/pull/" + source.number;
    }
    if (source.kind === "github_issue" || source.kind === "github_issue_fallback") {
      return "https://github.com/" + source.repo + "/issues/" + source.number;
    }
    return null;
  }

  function uniqueSorted(values) {
    var present = {};
    values.forEach(function (value) {
      present[value] = true;
    });
    return Object.keys(present).sort();
  }

  function repositoryColors(tasks) {
    var repositories = uniqueSorted(
      tasks.map(function (task) {
        return task.repo;
      })
    );
    var colors = {};
    repositories.forEach(function (repo, index) {
      var hue = Math.round((196 + index * 137.508) % 360);
      colors[repo] = "hsl(" + hue + " 58% 38%)";
    });
    return colors;
  }

  function compareTasks(left, right) {
    return (
      String(left.repo).localeCompare(String(right.repo)) ||
      String(left.created_at || "").localeCompare(String(right.created_at || "")) ||
      String(left.id).localeCompare(String(right.id))
    );
  }

  function graphModel(tasks) {
    var byId = {};
    var edges = [];
    var seenEdges = {};
    tasks.forEach(function (task) {
      byId[task.id] = task;
    });
    tasks.forEach(function (task) {
      taskDependencies(task).forEach(function (dependencyId) {
        var key = dependencyId + "\u0000" + task.id;
        if (byId[dependencyId] && !seenEdges[key]) {
          edges.push({ from: dependencyId, to: task.id });
          seenEdges[key] = true;
        }
      });
    });
    edges.sort(function (left, right) {
      return left.from.localeCompare(right.from) || left.to.localeCompare(right.to);
    });
    return { tasks: tasks.slice().sort(compareTasks), byId: byId, edges: edges };
  }

  function stronglyConnectedComponents(model) {
    var outgoing = {};
    var indexById = {};
    var lowLink = {};
    var stack = [];
    var onStack = {};
    var nextIndex = 0;
    var components = [];

    model.tasks.forEach(function (task) {
      outgoing[task.id] = [];
    });
    model.edges.forEach(function (edge) {
      outgoing[edge.from].push(edge.to);
    });
    Object.keys(outgoing).forEach(function (id) {
      outgoing[id].sort();
    });

    function visit(id) {
      var target;
      indexById[id] = nextIndex;
      lowLink[id] = nextIndex;
      nextIndex += 1;
      stack.push(id);
      onStack[id] = true;

      outgoing[id].forEach(function (dependentId) {
        if (indexById[dependentId] === undefined) {
          visit(dependentId);
          lowLink[id] = Math.min(lowLink[id], lowLink[dependentId]);
        } else if (onStack[dependentId]) {
          lowLink[id] = Math.min(lowLink[id], indexById[dependentId]);
        }
      });

      if (lowLink[id] !== indexById[id]) {
        return;
      }
      target = [];
      while (stack.length) {
        var member = stack.pop();
        onStack[member] = false;
        target.push(member);
        if (member === id) {
          break;
        }
      }
      target.sort();
      components.push(target);
    }

    model.tasks.forEach(function (task) {
      if (indexById[task.id] === undefined) {
        visit(task.id);
      }
    });
    components.sort(function (left, right) {
      return left[0].localeCompare(right[0]);
    });
    return components;
  }

  function layout(model) {
    var components = stronglyConnectedComponents(model);
    var componentByTask = {};
    var outgoing = {};
    var indegree = {};
    var layerByComponent = {};
    var componentEdges = {};
    var queue = [];
    var layers = [];
    var positions = {};
    var cycleComponents = 0;

    components.forEach(function (members, componentIndex) {
      outgoing[componentIndex] = [];
      indegree[componentIndex] = 0;
      layerByComponent[componentIndex] = 0;
      members.forEach(function (id) {
        componentByTask[id] = componentIndex;
      });
      if (members.length > 1) {
        cycleComponents += 1;
      }
    });

    model.edges.forEach(function (edge) {
      var from = componentByTask[edge.from];
      var to = componentByTask[edge.to];
      var key = from + ":" + to;
      if (from === to) {
        if (edge.from === edge.to && components[from].length === 1) {
          cycleComponents += 1;
        }
      } else if (!componentEdges[key]) {
        outgoing[from].push(to);
        indegree[to] += 1;
        componentEdges[key] = true;
      }
    });
    Object.keys(outgoing).forEach(function (componentIndex) {
      outgoing[componentIndex].sort(function (left, right) {
        return components[left][0].localeCompare(components[right][0]);
      });
      if (indegree[componentIndex] === 0) {
        queue.push(Number(componentIndex));
      }
    });
    queue.sort(function (left, right) {
      return components[left][0].localeCompare(components[right][0]);
    });

    while (queue.length) {
      var current = queue.shift();
      outgoing[current].forEach(function (dependent) {
        layerByComponent[dependent] = Math.max(
          layerByComponent[dependent],
          layerByComponent[current] + 1
        );
        indegree[dependent] -= 1;
        if (indegree[dependent] === 0) {
          queue.push(dependent);
          queue.sort(function (left, right) {
            return components[left][0].localeCompare(components[right][0]);
          });
        }
      });
    }

    components.forEach(function (members, componentIndex) {
      var layerIndex = layerByComponent[componentIndex];
      if (!layers[layerIndex]) {
        layers[layerIndex] = [];
      }
      members.forEach(function (id) {
        layers[layerIndex].push(model.byId[id]);
      });
    });
    layers.forEach(function (tasks) {
      tasks.sort(compareTasks);
    });
    layers.forEach(function (tasks, layerIndex) {
      tasks.forEach(function (task, rowIndex) {
        positions[task.id] = {
          x: MARGIN_X + layerIndex * (NODE_WIDTH + LAYER_GAP),
          y: MARGIN_Y + rowIndex * (NODE_HEIGHT + ROW_GAP)
        };
      });
    });

    return {
      layers: layers,
      positions: positions,
      cycleComponents: cycleComponents
    };
  }

  function truncate(value, length) {
    value = String(value || "");
    return value.length > length ? value.slice(0, length - 1) + "…" : value;
  }

  function counted(value, noun) {
    var plurals = { dependency: "dependencies", repository: "repositories" };
    return value + " " + (value === 1 ? noun : plurals[noun] || noun + "s");
  }

  function edgePath(source, target) {
    var startX = source.x + NODE_WIDTH;
    var startY = source.y + NODE_HEIGHT / 2;
    var endX = target.x;
    var endY = target.y + NODE_HEIGHT / 2;
    if (source.x === target.x && source.y === target.y) {
      endX = target.x + NODE_WIDTH / 2;
      endY = target.y;
      return (
        "M " + startX + " " + startY +
        " C " + (startX + 48) + " " + (startY - 48) +
        ", " + (endX + 48) + " " + (endY - 32) +
        ", " + endX + " " + endY
      );
    }
    if (target.x > source.x) {
      var middle = startX + (endX - startX) / 2;
      return (
        "M " + startX + " " + startY +
        " C " + middle + " " + startY +
        ", " + middle + " " + endY +
        ", " + endX + " " + endY
      );
    }
    var loopX = Math.max(startX, target.x + NODE_WIDTH) + 48;
    endX = target.x + NODE_WIDTH;
    return (
      "M " + startX + " " + startY +
      " C " + loopX + " " + startY +
      ", " + loopX + " " + endY +
      ", " + endX + " " + endY
    );
  }

  function appendMarker(svg) {
    var definitions = svgElement("defs");
    var marker = setAttributes(svgElement("marker"), {
      id: "dag-arrow",
      viewBox: "0 0 10 10",
      refX: 9,
      refY: 5,
      markerWidth: 7,
      markerHeight: 7,
      orient: "auto-start-reverse"
    });
    marker.appendChild(
      setAttributes(svgElement("path"), {
        d: "M 0 0 L 10 5 L 0 10 z",
        fill: "#6c7975"
      })
    );
    definitions.appendChild(marker);
    svg.appendChild(definitions);
  }

  function appendEdge(svg, edge, positions, model) {
    var path = setAttributes(svgElement("path"), {
      d: edgePath(positions[edge.from], positions[edge.to]),
      fill: "none",
      stroke: "#6c7975",
      "stroke-width": 1.6,
      "marker-end": "url(#dag-arrow)",
      "data-edge": "true",
      "data-from": edge.from,
      "data-to": edge.to
    });
    path.setAttribute("class", "dag-edge");
    var title = svgElement("title");
    title.textContent = sourceLabel(model.byId[edge.from]) + " to " + sourceLabel(model.byId[edge.to]);
    path.appendChild(title);
    svg.appendChild(path);
  }

  function appendNode(svg, task, position, color) {
    var url = canonicalUrl(task);
    var wrapper = svgElement(url ? "a" : "g");
    var label = sourceLabel(task);
    var status = displayStatus(taskStatus(task));
    setAttributes(wrapper, {
      "class": "dag-node",
      "data-task-id": task.id,
      "data-task-repo": task.repo,
      "data-repo-color": color,
      "data-source-label": label,
      "aria-label": label + ", " + task.title + ", " + status
    });
    if (url) {
      setAttributes(wrapper, {
        href: url,
        target: "_blank",
        rel: "noreferrer"
      });
    } else {
      setAttributes(wrapper, { role: "group", tabindex: 0 });
    }

    var title = svgElement("title");
    title.textContent = label + " · " + task.title + " · " + task.repo + " · " + status;
    wrapper.appendChild(title);
    wrapper.appendChild(
      setAttributes(svgElement("rect"), {
        x: position.x,
        y: position.y,
        width: NODE_WIDTH,
        height: NODE_HEIGHT,
        rx: 7,
        fill: "#ffffff",
        stroke: color,
        "stroke-width": 1.6
      })
    );
    wrapper.appendChild(
      setAttributes(svgElement("rect"), {
        x: position.x,
        y: position.y,
        width: 6,
        height: NODE_HEIGHT,
        rx: 3,
        fill: color
      })
    );

    var sourceText = setAttributes(svgElement("text"), {
      x: position.x + 17,
      y: position.y + 24,
      fill: color,
      "font-size": label.length > 18 ? 10 : 14,
      "font-weight": 750
    });
    sourceText.textContent = label;
    wrapper.appendChild(sourceText);

    var statusText = setAttributes(svgElement("text"), {
      x: position.x + NODE_WIDTH - 12,
      y: position.y + 23,
      fill: "#617078",
      "font-size": 10,
      "font-weight": 650,
      "text-anchor": "end"
    });
    statusText.textContent = truncate(status, 17);
    wrapper.appendChild(statusText);

    var taskTitle = setAttributes(svgElement("text"), {
      x: position.x + 17,
      y: position.y + 52,
      fill: "#172126",
      "font-size": 13,
      "font-weight": 650
    });
    taskTitle.textContent = truncate(task.title, 31);
    wrapper.appendChild(taskTitle);

    var repoText = setAttributes(svgElement("text"), {
      x: position.x + 17,
      y: position.y + 70,
      fill: "#617078",
      "font-size": 10
    });
    repoText.textContent = truncate(task.repo, 38);
    wrapper.appendChild(repoText);
    svg.appendChild(wrapper);
  }

  function renderLegend(tasks, colors) {
    var repositories = uniqueSorted(
      tasks.map(function (task) {
        return task.repo;
      })
    );
    legend.replaceChildren();
    repositories.forEach(function (repo) {
      var item = element("span", "dag-legend-item");
      var swatch = element("span", "dag-legend-swatch");
      swatch.style.backgroundColor = colors[repo];
      swatch.setAttribute("aria-hidden", "true");
      item.appendChild(swatch);
      item.appendChild(element("span", "", repo));
      legend.appendChild(item);
    });
    legend.hidden = repositories.length === 0;
  }

  function render() {
    if (!snapshot) {
      return;
    }
    var selectedRepo = repoFilter.value;
    var completeTasks = allTasks(snapshot);
    var colors = repositoryColors(completeTasks);
    var tasks = completeTasks.filter(function (task) {
      return !selectedRepo || task.repo === selectedRepo;
    });
    var model = graphModel(tasks);
    var repositories = uniqueSorted(
      tasks.map(function (task) {
        return task.repo;
      })
    );

    graph.replaceChildren();
    error.hidden = true;
    renderLegend(tasks, colors);
    if (!tasks.length) {
      graph.hidden = true;
      empty.hidden = false;
      summary.textContent =
        counted(0, "task") + " · " +
        counted(0, "dependency") + " · " +
        counted(0, "repository");
      return;
    }

    var graphLayout = layout(model);
    var layerCount = Math.max(graphLayout.layers.length, 1);
    var rowCount = graphLayout.layers.reduce(function (maximum, layer) {
      return Math.max(maximum, layer.length);
    }, 1);
    var width =
      MARGIN_X * 2 + layerCount * NODE_WIDTH + (layerCount - 1) * LAYER_GAP +
      (graphLayout.cycleComponents ? 64 : 0);
    var height = MARGIN_Y * 2 + rowCount * NODE_HEIGHT + (rowCount - 1) * ROW_GAP;
    var svg = setAttributes(svgElement("svg"), {
      width: width,
      height: height,
      viewBox: "0 0 " + width + " " + height,
      role: "img",
      "aria-labelledby": "dag-svg-title dag-svg-description"
    });
    var svgTitle = setAttributes(svgElement("title"), { id: "dag-svg-title" });
    svgTitle.textContent = selectedRepo ? selectedRepo + " task dependencies" : "All task dependencies";
    svg.appendChild(svgTitle);
    var svgDescription = setAttributes(svgElement("desc"), { id: "dag-svg-description" });
    svgDescription.textContent =
      model.tasks.length + " tasks and " + model.edges.length +
      " dependency arrows. Arrows point from dependencies to dependent tasks.";
    svg.appendChild(svgDescription);
    appendMarker(svg);
    model.edges.forEach(function (edge) {
      appendEdge(svg, edge, graphLayout.positions, model);
    });
    model.tasks.forEach(function (task) {
      appendNode(svg, task, graphLayout.positions[task.id], colors[task.repo]);
    });

    graph.appendChild(svg);
    graph.hidden = false;
    empty.hidden = true;
    summary.textContent =
      counted(model.tasks.length, "task") + " · " +
      counted(model.edges.length, "dependency") + " · " +
      counted(repositories.length, "repository") +
      (graphLayout.cycleComponents
        ? " · " + counted(graphLayout.cycleComponents, "cycle")
        : "");
  }

  function updateRepositories() {
    var selected = repoFilter.value;
    var repositories = uniqueSorted(
      allTasks(snapshot).map(function (task) {
        return task.repo;
      })
    );
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

  function updateSnapshotMetadata() {
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
        updateRepositories();
        updateSnapshotMetadata();
        render();
      })
      .catch(function (reason) {
        if (!snapshot) {
          graph.replaceChildren();
          graph.hidden = true;
          empty.hidden = true;
          legend.replaceChildren();
          legend.hidden = true;
          summary.textContent = "No graph loaded";
          error.textContent = "Could not load task snapshot: " + reason.message;
          error.hidden = false;
        }
        document.getElementById("sync-status").textContent = "Refresh failed";
      })
      .then(function () {
        refresh.disabled = false;
      });
  }

  repoFilter.addEventListener("change", render);
  refresh.addEventListener("click", function () {
    load(false);
  });

  load(false);
  window.setInterval(function () {
    load(true);
  }, 60000);
})();
