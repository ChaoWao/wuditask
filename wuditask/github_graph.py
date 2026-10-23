"""Read GitHub's business graph without creating or consulting Hub tasks."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import json
from pathlib import Path
import re
import subprocess
from typing import Any

from .errors import WudiTaskError
from .github_summary import body_summary
from .util import utc_now

_URL = re.compile(
    r"https://github\.com/([\w.-]+/[\w.-]+)/(issues|pull)/([1-9][0-9]*)/?$"
)


@dataclass(frozen=True)
class Reference:
    repo: str
    kind: str
    number: int

    @classmethod
    def parse(cls, url: str) -> Reference:
        match = _URL.fullmatch(url) if isinstance(url, str) else None
        if not match:
            raise ValueError(
                "Expected an https://github.com/owner/repo/issues/N or pull/N URL"
            )
        return cls(
            match[1].lower(), "issue" if match[2] == "issues" else "pr", int(match[3])
        )

    @property
    def url(self) -> str:
        return f"https://github.com/{self.repo}/{'issues' if self.kind == 'issue' else 'pull'}/{self.number}"

    @property
    def endpoint(self) -> str:
        return f"repos/{self.repo}/{'issues' if self.kind == 'issue' else 'pulls'}/{self.number}"


@dataclass(frozen=True)
class GraphScope:
    roots: tuple[Reference, ...]
    max_nodes: int = 500
    max_pages: int = 20
    stale_after_seconds: int = 10800
    repositories: tuple[str, ...] = ()

    @classmethod
    def read(cls, path: Path) -> GraphScope:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(value, dict) or set(value) - {
                "roots",
                "repositories",
                "max_nodes",
                "max_pages",
                "stale_after_seconds",
            }:
                raise ValueError("Unknown scope field")
            roots = value["roots"]
            if not isinstance(roots, list) or not roots or len(roots) > 100:
                raise ValueError("roots must contain 1 to 100 GitHub URLs")
            repositories = value.get("repositories", [])
            if (
                not isinstance(repositories, list)
                or len(repositories) > 20
                or any(
                    not isinstance(repo, str)
                    or not re.fullmatch(r"[\w.-]+/[\w.-]+", repo)
                    for repo in repositories
                )
            ):
                raise ValueError("repositories must contain up to 20 owner/repo names")
            options = {
                "repositories": tuple(dict.fromkeys(r.lower() for r in repositories))
            }
            for key, default, ceiling in [
                ("max_nodes", 500, 5000),
                ("max_pages", 20, 100),
                ("stale_after_seconds", 10800, 604800),
            ]:
                n = value.get(key, default)
                if type(n) is not int or not 1 <= n <= ceiling:
                    raise ValueError(
                        f"{key} must be an integer between 1 and {ceiling}"
                    )
                options[key] = n
            return cls(
                tuple(dict.fromkeys(Reference.parse(url) for url in roots)), **options
            )
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise WudiTaskError(
                "invalid_github_scope",
                "Invalid GitHub graph scope.",
                details={"reason": str(exc)},
            ) from exc


class ReadError(Exception):
    """Only a safe category crosses the publication boundary, never CLI stderr."""


class GitHubReader:
    def __init__(self, *, max_pages: int = 20, runner=None):
        self.max_pages = max_pages
        self.runner = runner or subprocess.run

    def _request(self, arguments: list[str]) -> Any:
        try:
            response = self.runner(
                ["gh", "api", *arguments],
                capture_output=True,
                text=True,
                timeout=45,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ReadError("transport_error") from exc
        if response.returncode:
            # Error messages can contain private URLs or credentials supplied by a wrapper.
            raise ReadError("github_unavailable")
        try:
            result = json.loads(response.stdout)
        except (ValueError, TypeError) as exc:
            raise ReadError("invalid_response") from exc
        if isinstance(result, dict) and result.get("errors"):
            raise ReadError("graphql_incomplete")
        return result

    def get(self, endpoint: str) -> dict:
        result = self._request([endpoint, "--method", "GET"])
        if not isinstance(result, dict):
            raise ReadError("invalid_response")
        return result

    def pages(self, endpoint: str, *, field: str | None = None) -> list:
        items = []
        for page in range(1, self.max_pages + 1):
            separator = "&" if "?" in endpoint else "?"
            result = self._request(
                [f"{endpoint}{separator}per_page=100&page={page}", "--method", "GET"]
            )
            batch = result.get(field) if field and isinstance(result, dict) else result
            if not isinstance(batch, list):
                raise ReadError("invalid_response")
            items.extend(batch)
            if len(batch) < 100:
                return items
        raise ReadError("page_limit")

    def closing_prs(self, ref: Reference) -> list[str]:
        query = """query($owner:String!,$repo:String!,$number:Int!,$cursor:String) {
          repository(owner:$owner,name:$repo) { issue(number:$number) {
            closedByPullRequestsReferences(first:100,after:$cursor,includeClosedPrs:true) {
              nodes { url } pageInfo { hasNextPage endCursor }
            }
          } }
        }"""
        owner, repo = ref.repo.split("/")
        cursor = None
        results = []
        for _ in range(self.max_pages):
            arguments = [
                "graphql",
                "-f",
                f"query={query}",
                "-f",
                f"owner={owner}",
                "-f",
                f"repo={repo}",
                "-F",
                f"number={ref.number}",
            ]
            if cursor:
                arguments.extend(["-f", f"cursor={cursor}"])
            value = self._request(arguments)
            try:
                connection = value["data"]["repository"]["issue"][
                    "closedByPullRequestsReferences"
                ]
                results.extend(item["url"] for item in connection["nodes"])
                info = connection["pageInfo"]
                if info["hasNextPage"] is False:
                    return results
                following = info["endCursor"]
                if not following or following == cursor:
                    raise ReadError("invalid_cursor")
                cursor = following
            except (KeyError, TypeError) as exc:
                raise ReadError("invalid_response") from exc
        raise ReadError("page_limit")

    def closing_issues(self, ref: Reference) -> list[str]:
        query = """query($owner:String!,$repo:String!,$number:Int!,$cursor:String) {
          repository(owner:$owner,name:$repo) { pullRequest(number:$number) {
            closingIssuesReferences(first:100,after:$cursor) {
              nodes { url } pageInfo { hasNextPage endCursor }
            }
          } }
        }"""
        owner, repo = ref.repo.split("/")
        cursor, results = None, []
        for _ in range(self.max_pages):
            arguments = [
                "graphql",
                "-f",
                f"query={query}",
                "-f",
                f"owner={owner}",
                "-f",
                f"repo={repo}",
                "-F",
                f"number={ref.number}",
            ]
            if cursor:
                arguments.extend(["-f", f"cursor={cursor}"])
            value = self._request(arguments)
            try:
                connection = value["data"]["repository"]["pullRequest"][
                    "closingIssuesReferences"
                ]
                results.extend(item["url"] for item in connection["nodes"])
                info = connection["pageInfo"]
                if info["hasNextPage"] is False:
                    return results
                following = info["endCursor"]
                if not following or following == cursor:
                    raise ReadError("invalid_cursor")
                cursor = following
            except (KeyError, TypeError) as exc:
                raise ReadError("invalid_response") from exc
        raise ReadError("page_limit")

    def review_decision(self, ref: Reference) -> str:
        query = """query($owner:String!,$repo:String!,$number:Int!) {
          repository(owner:$owner,name:$repo) { pullRequest(number:$number) { reviewDecision } }
        }"""
        owner, repo = ref.repo.split("/")
        value = self._request(
            [
                "graphql",
                "-f",
                f"query={query}",
                "-f",
                f"owner={owner}",
                "-f",
                f"repo={repo}",
                "-F",
                f"number={ref.number}",
            ]
        )
        try:
            return (
                value["data"]["repository"]["pullRequest"]["reviewDecision"]
                or "UNRECORDED"
            )
        except (KeyError, TypeError) as exc:
            raise ReadError("invalid_response") from exc


def _checks(checks: list, statuses: list) -> dict:
    # Check-run API defaults to filter=latest; commit statuses can contain old attempts.
    latest = {}
    for item in statuses:
        context = item["context"]
        if context not in latest or item["id"] > latest[context]["id"]:
            latest[context] = item
    counts = dict(total=0, successful=0, skipped=0, pending=0, failed=0)
    for item in checks:
        state = (
            item.get("conclusion") if item.get("status") == "completed" else "pending"
        )
        key = (
            "successful"
            if state == "success"
            else "skipped"
            if state in {"skipped", "neutral"}
            else "pending"
            if state in {None, "pending"}
            else "failed"
        )
        counts[key] += 1
    for item in latest.values():
        state = item["state"]
        counts[
            "successful"
            if state == "success"
            else "pending"
            if state == "pending"
            else "failed"
        ] += 1
    counts["total"] = sum(
        counts[key] for key in ("successful", "skipped", "pending", "failed")
    )
    return counts


def explicit_issue_links(body: str, repo: str) -> list[Reference]:
    """Only a standalone 'Part of' declaration is a body association, never a blocker."""
    body = re.sub(r"<!--.*?(?:-->|$)", "", body or "", flags=re.S)
    result, fence, hidden = [], None, 0
    for raw in body.splitlines():
        line = raw.strip()
        marker = re.match(r"^(`{3,}|~{3,})", line)
        if marker:
            run = marker[1]
            if fence is None:
                fence = run
            elif run[0] == fence[0] and len(run) >= len(fence):
                fence = None
            continue
        if fence:
            continue
        opens = len(re.findall(r"<(?:details|script|style)\b", line, re.I))
        closes = len(re.findall(r"</(?:details|script|style)\s*>", line, re.I))
        if hidden or opens:
            hidden = max(0, hidden + opens - closes)
            continue
        match = re.fullmatch(
            r"(?:[-*] )?Part of:?\s+(#[1-9][0-9]*|https://github\.com/[\w.-]+/[\w.-]+/issues/[1-9][0-9]*)[.]?",
            line,
            re.I,
        )
        if match:
            token = match[1]
            result.append(
                Reference(repo, "issue", int(token[1:]))
                if token.startswith("#")
                else Reference.parse(token)
            )
    return list(dict.fromkeys(result))


def collect_graph(scope: GraphScope, reader: GitHubReader | None = None) -> dict:
    """Expand roots through child Issues and prerequisites; PRs are delivery evidence.

    Public output is conservative: repository visibility must be independently
    proven public before any of its object metadata enters the snapshot.
    """
    reader = reader or GitHubReader(max_pages=scope.max_pages)
    started = utc_now()
    pending = deque(scope.roots)
    scheduled = set(scope.roots)
    resolved: dict[Reference, str] = {}
    nodes: dict[str, dict] = {}
    pending_edges = []
    diagnostics = []
    visibility = {}
    truncated = False

    def boundary(ref, reason):
        key = f"boundary:{len(nodes)}"
        nodes[key] = {
            "id": key,
            "kind": "boundary",
            "title": "Unavailable or restricted reference",
            "state": "unknown",
            "prerequisites": "unknown",
            "reason": reason,
        }
        resolved[ref] = key
        return key

    def enqueue(ref):
        nonlocal truncated
        if ref in scheduled:
            return
        scheduled.add(ref)
        if len(scheduled) > scope.max_nodes:
            truncated = True
            # Do not materialize unbounded boundary nodes; the source relation is marked incomplete.
            return
        pending.append(ref)

    for repo in scope.repositories:
        try:
            repository = reader.get(f"repos/{repo}")
            visibility[repo] = (
                repository.get("private") is False
                and repository.get("visibility", "public") == "public"
            )
            if not visibility[repo]:
                raise ReadError("restricted_repository")
            for item in reader.pages(f"repos/{repo}/issues?state=open"):
                ref = Reference.parse(item["html_url"])
                if ref.repo != repo:
                    raise ReadError("invalid_inventory")
                enqueue(ref)
        except (ReadError, KeyError, TypeError, ValueError) as exc:
            diagnostics.append(
                {
                    "code": str(exc)
                    if isinstance(exc, ReadError)
                    else "invalid_inventory",
                    "field": "repository_inventory",
                }
            )

    while pending:
        ref = pending.popleft()
        if len(nodes) >= scope.max_nodes:
            truncated = True
            break
        try:
            if ref.repo not in visibility:
                repository = reader.get(f"repos/{ref.repo}")
                visibility[ref.repo] = (
                    repository.get("private") is False
                    and repository.get("visibility", "public") == "public"
                )
            if not visibility[ref.repo]:
                boundary(ref, "restricted")
                continue
            raw = reader.get(ref.endpoint)
            canonical = Reference.parse(raw["html_url"])
            if canonical.repo != ref.repo:
                if canonical.repo not in visibility:
                    repository = reader.get(f"repos/{canonical.repo}")
                    visibility[canonical.repo] = (
                        repository.get("private") is False
                        and repository.get("visibility", "public") == "public"
                    )
                if not visibility[canonical.repo]:
                    boundary(ref, "restricted")
                    continue
            if (
                canonical.kind != ref.kind
                or not isinstance(raw["node_id"], str)
                or not raw["node_id"]
            ):
                raise ReadError("identity_ambiguous")
            key = f"github.com:{ref.kind}:{raw['node_id']}"
            resolved[ref] = key
            resolved[canonical] = key
            if key in nodes:
                continue
            assignees = sorted({item["login"] for item in raw["assignees"]})
            state = raw["state"]
            if state not in {"open", "closed"}:
                raise ReadError("invalid_state")
            node = {
                "id": key,
                "kind": ref.kind,
                "repo": canonical.repo,
                "number": canonical.number,
                "url": canonical.url,
                "title": raw["title"],
                "summary": body_summary(raw.get("body")),
                "state": state,
                "state_reason": raw.get("state_reason"),
                "assignees": assignees,
                "owner_resolution": "not_applicable"
                if ref.kind == "pr"
                else "unique"
                if len(assignees) == 1
                else "unassigned"
                if not assignees
                else "conflict",
                "updated_at": raw["updated_at"],
                "observed_at": utc_now(),
                "relations_complete": {},
                "errors": [],
            }
            if ref.kind == "pr":
                node["author"] = (raw.get("user") or {}).get("login")
            nodes[key] = node
            if ref.kind == "issue":
                relation_reads = [
                    (
                        "contains",
                        lambda: [
                            item["html_url"]
                            for item in reader.pages(canonical.endpoint + "/sub_issues")
                        ],
                    ),
                    (
                        "blocks",
                        lambda: [
                            item["html_url"]
                            for item in reader.pages(
                                canonical.endpoint + "/dependencies/blocked_by"
                            )
                        ],
                    ),
                    ("delivers", lambda: reader.closing_prs(canonical)),
                ]
                for kind, read in relation_reads:
                    try:
                        refs = [Reference.parse(url) for url in read()]
                        if any(
                            item.kind != ("pr" if kind == "delivers" else "issue")
                            for item in refs
                        ):
                            raise ReadError("invalid_relation")
                        node["relations_complete"][kind] = True
                        for target in refs:
                            enqueue(target)
                            if target not in resolved and target not in pending:
                                node["relations_complete"][kind] = False
                            pending_edges.append((canonical, target, kind))
                    except (ReadError, KeyError, TypeError, ValueError) as exc:
                        node["relations_complete"][kind] = False
                        node["errors"].append(
                            {
                                "field": kind,
                                "code": str(exc)
                                if isinstance(exc, ReadError)
                                else "invalid_response",
                            }
                        )
            else:
                try:
                    targets = [
                        Reference.parse(url) for url in reader.closing_issues(canonical)
                    ]
                    if any(t.kind != "issue" for t in targets):
                        raise ReadError("invalid_relation")
                    node["relations_complete"]["delivers"] = True
                    for target in targets:
                        enqueue(target)
                        pending_edges.append((target, canonical, "delivers"))
                except (ReadError, KeyError, TypeError, ValueError) as exc:
                    node["relations_complete"]["delivers"] = False
                    node["errors"].append(
                        {
                            "field": "delivers",
                            "code": str(exc)
                            if isinstance(exc, ReadError)
                            else "invalid_response",
                        }
                    )
                for target in explicit_issue_links(raw.get("body"), canonical.repo):
                    enqueue(target)
                    pending_edges.append((target, canonical, "relates"))
                node.update(
                    head_sha=raw["head"]["sha"],
                    merged_at=raw.get("merged_at"),
                    draft=raw.get("draft", False),
                )
                if node["merged_at"]:
                    node["state"] = "merged"
                try:
                    sha = node["head_sha"]
                    node["checks"] = _checks(
                        reader.pages(
                            f"repos/{canonical.repo}/commits/{sha}/check-runs?filter=latest",
                            field="check_runs",
                        ),
                        reader.pages(f"repos/{canonical.repo}/commits/{sha}/statuses"),
                    )
                    node["review_decision"] = reader.review_decision(canonical)
                except (ReadError, KeyError, TypeError, ValueError) as exc:
                    node["errors"].append(
                        {
                            "field": "checks_reviews",
                            "code": str(exc)
                            if isinstance(exc, ReadError)
                            else "invalid_response",
                        }
                    )
                    node["checks"] = None
                    node["review_decision"] = "UNKNOWN"
            # A changed head or Issue during observation invalidates this object's derived evidence.
            try:
                after = reader.get(canonical.endpoint)
                if (
                    after.get("node_id") != raw["node_id"]
                    or after.get("updated_at") != raw["updated_at"]
                    or (
                        ref.kind == "pr"
                        and after.get("head", {}).get("sha") != node["head_sha"]
                    )
                ):
                    raise ReadError("changed_during_collection")
            except ReadError as exc:
                node["errors"].append({"field": "observation", "code": str(exc)})
                if ref.kind == "pr":
                    node["checks"] = None
                    node["review_decision"] = "UNKNOWN"
        except (ReadError, KeyError, TypeError, ValueError) as exc:
            # Failed reads publish no raw response, URL, title or body.
            reason = str(exc) if isinstance(exc, ReadError) else "invalid_response"
            old_key = resolved.pop(ref, None)
            if old_key in nodes:
                nodes.pop(old_key)
            boundary(ref, reason)

    edges = []
    seen = set()
    for source, target, kind in pending_edges:
        if source not in resolved or target not in resolved:
            continue
        a, b = resolved[source], resolved[target]
        if a not in nodes or b not in nodes:
            continue
        if kind != "contains":
            a, b = b, a
        identity = (a, b, kind)
        if identity not in seen:
            seen.add(identity)
            edges.append(
                {
                    "from": a,
                    "to": b,
                    "kind": kind,
                    "source": nodes[
                        resolved[target if kind in {"delivers", "relates"} else source]
                    ].get("url"),
                    "observed_at": utc_now(),
                }
            )
    delivered = {(e["from"], e["to"]) for e in edges if e["kind"] == "delivers"}
    edges = [
        e
        for e in edges
        if e["kind"] != "relates" or (e["from"], e["to"]) not in delivered
    ]
    if truncated:
        diagnostics.append(
            {
                "code": "node_limit",
                "message": "Scope truncated; omitted references cannot be treated as satisfied.",
            }
        )
    derive_prerequisites(nodes, edges)
    for node in nodes.values():
        for error in node.get("errors", []):
            diagnostics.append({"node": node["id"], **error})
        if node["kind"] == "boundary":
            diagnostics.append({"node": node["id"], "code": node["reason"]})
    cycles = dependency_cycles(nodes, edges)
    if cycles:
        diagnostics.append({"code": "dependency_cycle", "nodes": sorted(cycles)})
    complete = not diagnostics
    roots = [resolved[ref] for ref in scope.roots if ref in resolved]
    available_roots = [key for key in roots if nodes[key]["kind"] != "boundary"]
    return {
        "format": "github-business-graph",
        "generated_at": utc_now(),
        "observation_started_at": started,
        "status": "complete" if complete else "partial",
        "audience": "public",
        "stale_after_seconds": scope.stale_after_seconds,
        "roots": roots,
        "available_roots": len(available_roots),
        "nodes": list(nodes.values()),
        "edges": edges,
        "diagnostics": diagnostics,
        "execution": "not_connected",
    }


def dependency_cycles(nodes: dict, edges: list) -> set[str]:
    """Find strongly connected components without recursion on long chains."""
    outgoing = {key: [] for key in nodes}
    incoming = {key: [] for key in nodes}
    for edge in edges:
        if edge["kind"] == "blocks":
            outgoing[edge["from"]].append(edge["to"])
            incoming[edge["to"]].append(edge["from"])
    visited, order = set(), []
    for root in nodes:
        if root in visited:
            continue
        visited.add(root)
        stack = [(root, iter(outgoing[root]))]
        while stack:
            key, children = stack[-1]
            child = next(children, None)
            if child is None:
                order.append(key)
                stack.pop()
            elif child not in visited:
                visited.add(child)
                stack.append((child, iter(outgoing[child])))
    assigned, cycles = set(), set()
    for root in reversed(order):
        if root in assigned:
            continue
        component, queue = set(), [root]
        assigned.add(root)
        while queue:
            key = queue.pop()
            component.add(key)
            for parent in incoming[key]:
                if parent not in assigned:
                    assigned.add(parent)
                    queue.append(parent)
        if len(component) > 1 or root in outgoing[root]:
            cycles.update(component)
    return cycles


def derive_prerequisites(nodes: dict, edges: list) -> None:
    incoming = {key: [] for key in nodes}
    successors = {key: [] for key in nodes}
    for edge in edges:
        if edge["kind"] == "blocks":
            incoming[edge["to"]].append(edge["from"])
            successors[edge["from"]].append(edge["to"])
    invalid = dependency_cycles(nodes, edges)
    queue = deque(invalid)
    while queue:
        for child in successors[queue.popleft()]:
            if child not in invalid:
                invalid.add(child)
                queue.append(child)
    for key, node in nodes.items():
        if node["kind"] != "issue":
            node["prerequisites"] = (
                "unknown" if node["kind"] == "boundary" else "not_applicable"
            )
            continue
        parents = [nodes[item] for item in incoming[key]]
        if key in invalid:
            result = "invalid"
        elif not node["relations_complete"].get("blocks") or any(
            e["field"] == "observation" for e in node["errors"]
        ):
            result = "unknown"
        elif not parents:
            result = "unrecorded"
        elif any(
            p["kind"] == "boundary"
            or p.get("errors")
            or p.get("state") not in {"open", "closed"}
            for p in parents
        ):
            result = "unknown"
        elif any(p["state"] == "open" for p in parents):
            result = "blocked"
        elif any(p.get("state_reason") != "completed" for p in parents):
            result = "needs_assessment"
        else:
            result = "satisfied"
        node["prerequisites"] = result
