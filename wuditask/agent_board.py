"""Render a self-contained, read-only snapshot of a local agent plan."""

from __future__ import annotations

import html
import json
from pathlib import Path
from urllib.parse import urlsplit


def _text(value: object) -> str:
    return html.escape(str(value if value is not None else "Not recorded"), quote=True)


def _json(value: object) -> str:
    # HTML's script parser recognizes closing tags even inside JSON strings.
    return (
        json.dumps(value, ensure_ascii=True)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )


def _link(value: object, label: str) -> str:
    url = str(value or "")
    try:
        parsed = urlsplit(url)
        safe = (
            parsed.scheme in {"https", "http"}
            and bool(parsed.netloc)
            and not any(ord(char) < 32 for char in url)
        )
    except ValueError:
        safe = False
    if not safe:
        return _text(label) + " (link unavailable)"
    return f'<a href="{_text(url)}" rel="noreferrer noopener">{_text(label)}</a>'


def _evidence_current(evidence: object, head: object) -> bool:
    if not isinstance(evidence, dict) or not head:
        return False
    return (
        evidence.get("head") == head
        and not evidence.get("stale")
        and evidence.get("status") in {"passed", "approved", "success", "accepted"}
    )


def _delivery_state(delivery: dict) -> str:
    status = delivery.get("status", "implementing")
    if status == "ready_for_merge" and (
        delivery.get("stale")
        or delivery.get("error")
        or not all(
            _evidence_current(delivery.get(field), delivery.get("head"))
            for field in ("review_evidence", "ci_evidence")
        )
    ):
        return "needs_revalidation"
    return status


def _label(status: object) -> str:
    return _text(str(status).replace("_", " "))


def _record(label: str, value: object) -> str:
    if isinstance(value, (dict, list)):
        value = json.dumps(value, indent=2, ensure_ascii=False)
    return f"<dt>{_text(label)}</dt><dd><pre>{_text(value)}</pre></dd>"


def _item_state(item: dict, attempt: dict) -> str:
    status = item.get("status", "planned")
    if status not in {"running", "awaiting_review"}:
        return status
    observation = attempt.get("observation", {}).get("state")
    if observation in {"exited", "not_started"} or attempt.get("status") == "awaiting_review":
        return "awaiting_review"
    if observation == "unknown" or attempt.get("status") == "launch_unknown":
        return "launch_unknown"
    return status


def _next_action(item: dict, attempt: dict | None = None) -> str:
    if item.get("next_action"):
        return str(item["next_action"])
    return {
        "planned": "Check dependencies and authorization before dispatch.",
        "ready": "Dispatch when the writer and required resources are available.",
        "running": "Collect the active attempt's result, then review its evidence.",
        "awaiting_review": "Review the result and acceptance evidence.",
        "launch_unknown": "Reconcile the recorded attempt before starting another worker.",
        "accepted": "Use the accepted artifact for authorized successors.",
        "needs_rework": "Address review findings in a new attempt.",
        "blocked": "Resolve the recorded blocker before resuming.",
        "cancelled": "No further execution; cancellation does not satisfy dependencies.",
    }.get(_item_state(item, attempt or {}), "Reconcile the recorded state before proceeding.")


def _merge_gate(delivery: dict, dependency: dict) -> str:
    status = delivery.get("status", "unknown")
    baseline = dependency.get("require_baseline", True)
    expected = dependency.get("merge_commit")
    observed = delivery.get("merge_evidence", {}).get("merge_commit")
    satisfied = (
        status in {"merged", "baseline_verified"}
        and (not baseline or status == "baseline_verified")
        and (not expected or expected == observed)
    )
    label = (
        f"Merge gate: {'satisfied' if satisfied else 'pending'}; requires "
        f"{'baseline verification' if baseline else 'verified merge'}; "
        f"observed {str(status).replace('_', ' ')}"
    )
    if expected:
        label += f"; required merge commit {expected}; observed merge commit {observed or 'not recorded'}"
    return _text(label)


def _graph(items: dict) -> tuple[list[list[str]], list[tuple[str, str]], bool]:
    """Layer the actual artifact edges; cycles retain all nodes in a fallback row."""
    edges = [
        (dep["ref"], key)
        for key, item in items.items()
        for dep in item.get("depends_on", [])
        if dep.get("kind") == "artifact" and dep.get("ref") in items
    ]
    remaining = set(items)
    layers = []
    while remaining:
        ready = [
            key
            for key in items
            if key in remaining
            and not any(
                target == key and source in remaining for source, target in edges
            )
        ]
        if not ready:
            layers.append([key for key in items if key in remaining])
            return layers, edges, True
        layers.append(ready)
        remaining.difference_update(ready)
    return layers, edges, False


_STYLE = """
:root{color-scheme:light;--ink:#182839;--muted:#536476;--line:#cbd5df;--paper:#fff}
*{box-sizing:border-box}body{margin:0;background:#f2f5f8;color:var(--ink);font:16px/1.5 system-ui,sans-serif}
main{max-width:1440px;margin:auto;padding:24px}h1{font-size:2rem;margin:0 0 8px}h2{font-size:1.3rem}
h3{font-size:1.1rem}section{margin:24px 0}a{color:#075da5}button{font:inherit;padding:6px 12px;cursor:pointer}
a:focus-visible,button:focus-visible,summary:focus-visible{outline:3px solid #c65a00;outline-offset:3px}
.muted{color:var(--muted)}.overview{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:16px}
.panel,.node,.delivery{background:var(--paper);border:1px solid var(--line);border-radius:10px;padding:16px}
.status{display:inline-block;border:1px solid var(--line);border-radius:20px;padding:2px 9px;font-size:.85rem;margin:5px 0}
.warning{border-left:4px solid #a64f00;padding:8px 12px;background:#fff3e3}.toolbar{display:flex;gap:8px;flex-wrap:wrap}
.graph-scroll{overflow:auto;padding:12px 0}.graph{display:flex;position:relative;gap:64px;align-items:flex-start;min-height:100px}
.layer{display:flex;flex-direction:column;gap:24px;flex:0 0 280px;z-index:1}.node{width:280px;overflow-wrap:anywhere}
.node summary{cursor:pointer;list-style-position:outside}.node summary strong{display:block}.node p{margin:8px 0}
.graph svg{position:absolute;inset:0;pointer-events:none;overflow:visible}.graph path{fill:none;stroke:#71869b;stroke-width:2}
.node[data-status="blocked"]{border-color:#a64f00}.node[data-status="accepted"]{border-color:#48856b}
.gates{padding-left:18px;font-size:.9rem}dt{font-weight:650;margin-top:10px}dd{margin:2px 0 10px}
pre{font:inherit;white-space:pre-wrap;overflow-wrap:anywhere;margin:0}table{border-collapse:collapse;width:100%}
th,td{text-align:left;vertical-align:top;padding:10px;border-bottom:1px solid var(--line)}caption{text-align:left;font-weight:650}
.delivery{margin:12px 0}.empty{color:var(--muted)}.table-scroll{overflow:auto}small{font-size:.85rem}
@media(max-width:600px){main{padding:16px}h1{font-size:1.6rem}.layer,.node{width:250px;flex-basis:250px}}
"""
_SCRIPT = """
'use strict';
const graph = document.getElementById('graph');
const edges = JSON.parse(document.getElementById('graph-edges').textContent);
const svg = document.getElementById('edges');
function drawEdges() {
  svg.replaceChildren();
  const origin = graph.getBoundingClientRect();
  svg.setAttribute('width', graph.scrollWidth);
  svg.setAttribute('height', graph.scrollHeight);
  for (const [source, target] of edges) {
    const a = document.getElementById(source).getBoundingClientRect();
    const b = document.getElementById(target).getBoundingClientRect();
    const x1 = a.right - origin.left, y1 = a.top + a.height / 2 - origin.top;
    const x2 = b.left - origin.left, y2 = b.top + b.height / 2 - origin.top;
    const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    const mid = (x1 + x2) / 2;
    path.setAttribute('d', `M ${x1} ${y1} C ${mid} ${y1}, ${mid} ${y2}, ${x2} ${y2}`);
    svg.appendChild(path);
  }
}
document.getElementById('expand').addEventListener('click', () => {
  document.querySelectorAll('.node').forEach(node => { node.open = true; });
});
document.getElementById('collapse').addEventListener('click', () => {
  document.querySelectorAll('.node').forEach(node => { node.open = false; });
});
document.getElementById('collapse-accepted').addEventListener('click', () => {
  document.querySelectorAll('.node[data-status="accepted"]').forEach(node => { node.open = false; });
});
document.querySelectorAll('.node').forEach(node => node.addEventListener('toggle', drawEdges));
window.addEventListener('resize', drawEdges);
if (typeof ResizeObserver !== 'undefined') new ResizeObserver(drawEdges).observe(graph);
drawEdges();
"""


def render_board(plan: dict, output: Path) -> None:
    """Write an offline HTML snapshot; no network reads or plan mutations occur."""
    items = plan.get("items", {})
    deliveries = plan.get("deliveries", {})
    decisions = plan.get("decisions", {})
    attempts = plan.get("attempts", {})
    node_ids = {key: f"item-{index}" for index, key in enumerate(items)}
    delivery_ids = {key: f"delivery-{index}" for index, key in enumerate(deliveries)}
    decision_ids = {key: f"decision-{index}" for index, key in enumerate(decisions)}
    states = {key: _delivery_state(value) for key, value in deliveries.items()}
    chunks = [
        '<!doctype html><html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; '
        "script-src 'unsafe-inline'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'\">",
        "<title>Local agent plan</title><style>",
        _STYLE,
        "</style></head><body><main>",
        f'<header><p class="muted">WudiTask · Local agent plan</p><h1>{_text(plan.get("goal", "No goal recorded"))}</h1>',
        f"<p>Revision {_text(plan.get('revision', 'unknown'))} · Plan observed: {_text(plan.get('updated_at', 'unknown'))}</p>",
        '<p class="muted">Read-only local snapshot. It does not refresh or verify remote state. '
        "Process status is not evidence of progress.</p></header>",
        "<details><summary>Scope and acceptance agreement</summary><dl>",
        _record("Agreement", plan.get("agreement", {})),
        _record("Task reference", plan.get("task_ref")),
        "</dl></details>",
        '<section aria-labelledby="delivery-title"><h2 id="delivery-title">Current deliveries</h2>',
    ]
    if not deliveries:
        chunks.append(
            '<p class="empty">No deliveries recorded. The plan is not complete.</p>'
        )
    for key, delivery in deliveries.items():
        status = states[key]
        chunks.append(
            f'<article class="delivery" id="{delivery_ids[key]}"><h3>{_text(delivery.get("title", "Untitled delivery"))}</h3>'
            f'<span class="status">{_label(status)}</span>'
        )
        if status == "needs_revalidation":
            chunks.append(
                '<p class="warning">Ready withdrawn: review or CI evidence is missing, stale, or does not match the current head.</p>'
            )
        if delivery.get("stale") or delivery.get("error"):
            chunks.append(
                f'<p class="warning">Remote evidence may be stale. {_text(delivery.get("error", "Recheck remote state."))}</p>'
            )
        chunks.append(
            f"<p>{_link(delivery.get('pr_url'), 'Open pull request')}</p><dl>"
            + _record("Current head", delivery.get("head"))
            + "</dl>"
        )
        chunks.append("<details><summary>Review, CI and merge evidence</summary><dl>")
        for field in ("review_evidence", "ci_evidence", "merge_evidence"):
            chunks.append(
                _record(field.replace("_", " ").capitalize(), delivery.get(field))
            )
        chunks.append("</dl></details></article>")
    chunks.append(
        '</section><div class="overview"><section class="panel" aria-labelledby="decisions-title">'
        '<h2 id="decisions-title">Pending user decisions</h2>'
    )
    pending = {
        key: value
        for key, value in decisions.items()
        if value.get("status") == "pending"
    }
    if not pending:
        chunks.append('<p class="empty">No unanswered decisions recorded.</p>')
    for key, decision in pending.items():
        chunks.append(
            f'<article id="{decision_ids[key]}"><h3>{_text(decision.get("question", "Question not recorded"))}</h3>'
            f"<p>Proposal revision: {_text(decision.get('proposal_revision'))}</p>"
            "<details><summary>Decision evidence</summary><dl>"
            + _record("Evidence", decision.get("evidence"))
            + "</dl></details></article>"
        )
    chunks.append(
        '</section><section class="panel" aria-labelledby="ready-title"><h2 id="ready-title">PRs ready for user merge</h2>'
    )
    ready = [key for key in deliveries if states[key] == "ready_for_merge"]
    chunks.append(
        "<ul>"
        + "".join(
            f'<li><a href="#{delivery_ids[key]}">{_text(deliveries[key].get("title", "Untitled delivery"))}</a></li>'
            for key in ready
        )
        + "</ul>"
        if ready
        else '<p class="empty">No PR has recorded current-head readiness.</p>'
    )
    chunks.append(
        '</section></div><section aria-labelledby="graph-title"><h2 id="graph-title">Work dependencies</h2>'
        '<p class="muted">Artifact dependencies run left to right. Decisions and verified merges are separate gates. '
        "Expand a node for its next action; hover for its summary.</p>"
        '<div class="toolbar"><button id="expand" type="button">Expand all</button>'
        '<button id="collapse" type="button">Collapse all</button>'
        '<button id="collapse-accepted" type="button">Collapse accepted</button></div>'
    )
    layers, edges, cyclic = _graph(items)
    if cyclic:
        chunks.append(
            '<p class="warning">Dependency cycle detected. Resolve the cycle before dispatch; the table preserves all dependencies.</p>'
        )
    if not items:
        chunks.append(
            '<p class="empty">No work items recorded. No execution or completion is implied.</p>'
        )
    chunks.append(
        '<div class="graph-scroll"><div class="graph" id="graph"><svg id="edges" aria-hidden="true"></svg>'
    )
    rows = []
    for layer in layers:
        chunks.append('<div class="layer">')
        for key in layer:
            item = items[key]
            attempt = attempts.get(item.get("active_attempt"), {})
            status = _item_state(item, attempt)
            title = item.get("title", "Untitled work item")
            summary = item.get("summary") or _next_action(item, attempt)
            gates = []
            for dep in item.get("depends_on", []):
                ref, kind = dep.get("ref"), dep.get("kind")
                if kind == "artifact" and ref in items:
                    predecessor = items[ref]
                    predecessor_state = _item_state(predecessor, attempts.get(predecessor.get("active_attempt"), {}))
                    gate = f'<a href="#{node_ids[ref]}">{_text(predecessor.get("title", "Untitled work item"))}</a> (artifact: {_label(predecessor_state)})'
                elif kind == "decision" and ref in decisions:
                    decision = decisions[ref]
                    gate = f"Decision: {_text(decision.get('question', 'Question not recorded'))} ({_label(decision.get('status', 'unknown'))})"
                elif kind == "merged" and ref in deliveries:
                    gate = f'<a href="#{delivery_ids[ref]}">{_text(deliveries[ref].get("title", "Untitled delivery"))}</a> ({_merge_gate(deliveries[ref], dep)})'
                else:
                    gate = f"{_label(kind)}: unresolved dependency {_text(ref)}"
                gates.append(gate)
            chunks.append(
                f'<details class="node" id="{node_ids[key]}" data-status="{_text(status)}">'
                f'<summary title="{_text(summary)}"><strong>{_text(title)}</strong><span class="status">{_label(status)}</span></summary>'
                f"<p>{_text(summary)}</p><p><strong>Next:</strong> {_text(_next_action(item, attempt))}</p>"
            )
            if item.get("block_reason"):
                chunks.append(
                    f'<p class="warning">Blocker: {_text(item["block_reason"])}</p>'
                )
            if gates:
                chunks.append(
                    '<ul class="gates">'
                    + "".join(f"<li>{gate}</li>" for gate in gates)
                    + "</ul>"
                )
            chunks.append(
                "<dl>"
                + _record("Write scope", item.get("write_scope", []))
                + _record("Resources", item.get("resources", []))
            )
            if attempt:
                chunks.append(_record("Active attempt", attempt))
            chunks.append("</dl></details>")
            rows.append(
                f'<tr><th scope="row"><a href="#{node_ids[key]}">{_text(title)}</a></th><td>{_label(status)}</td>'
                f"<td>{'<br>'.join(gates) or 'No prerequisites'}</td><td>{_text(_next_action(item, attempt))}</td></tr>"
            )
        chunks.append("</div>")
    chunks.append(
        '</div></div><details><summary>Dependency table (accessible fallback)</summary><div class="table-scroll">'
        '<table><caption>All work items and their prerequisites</caption><thead><tr><th scope="col">Work item</th>'
        '<th scope="col">State</th><th scope="col">Prerequisites</th><th scope="col">Next action</th></tr></thead><tbody>'
        + "".join(rows)
        + "</tbody></table></div></details></section>"
    )
    chunks.append(
        '<section aria-labelledby="active-title"><h2 id="active-title">Active work and blockers</h2>'
    )
    active = [
        (key, value)
        for key, value in items.items()
        if value.get("status")
        in {"running", "awaiting_review", "blocked", "needs_rework"}
    ]
    if not active:
        chunks.append(
            '<p class="empty">No active work or blockers recorded. Check deliveries and pending decisions before concluding the plan is complete.</p>'
        )
    for key, item in active:
        attempt = attempts.get(item.get("active_attempt"), {})
        chunks.append(
            f'<article class="panel"><h3><a href="#{node_ids[key]}">{_text(item.get("title", "Untitled work item"))}</a></h3>'
            f"<p>{_label(_item_state(item, attempt))} · Executor: {_text(attempt.get('backend', 'not recorded'))}</p>"
            f"<p>{_text(item.get('summary') or 'No progress summary recorded.')}</p>"
            f"<p>Next: {_text(_next_action(item, attempt))}</p>"
        )
        if item.get("block_reason"):
            chunks.append(
                f'<p class="warning">Blocker: {_text(item["block_reason"])}</p>'
            )
        chunks.append("</article>")
    chunks.append(
        '</section></main><script type="application/json" id="graph-edges">'
        + _json([[node_ids[source], node_ids[target]] for source, target in edges])
        + "</script><script>"
        + _SCRIPT
        + "</script></body></html>"
    )
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("".join(chunks), encoding="utf-8")
