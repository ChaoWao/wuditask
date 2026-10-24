"""GitHub-backed personal coordination commands and durable local sessions."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlsplit
import re

from .coordination import ExecutionRepository, require, text_field, validate_ticket
from .errors import WudiTaskError
from .gitops import GitCoordinator, _file_lock, _try_file_lock
from .model import Identity
from .util import atomic_write_json, new_run_id, read_json


def add_parser(commands: Any) -> None:
    parser = commands.add_parser(
        "coord", help="Coordinate personal execution directly from GitHub tasks."
    )
    sub = parser.add_subparsers(dest="coord_command", required=True)
    setup = sub.add_parser(
        "setup",
        help="Create/use a separate execution branch and install the unified skill.",
    )
    setup.add_argument("--remote", required=True)
    setup.add_argument("--branch", default="personal-execution")
    takeover = sub.add_parser(
        "takeover",
        help="Explicitly replace an observed run; no process-stop proof required.",
    )
    takeover.add_argument("url")
    takeover.add_argument("--anchor")
    takeover.add_argument("--session", type=Path, required=True)
    takeover.add_argument("--expected-run-id", required=True)
    takeover.add_argument(
        "--reason", required=True, help="The user's explicit takeover request."
    )
    configure = sub.add_parser(
        "configure", help="Bind this environment to one authoritative execution remote."
    )
    configure.add_argument("--remote", required=True)
    configure.add_argument("--branch", default="main")
    sub.add_parser(
        "inventory", help="Read migration inventory without changing any task."
    )
    init = sub.add_parser(
        "init",
        help="Initialize execution storage or retire the legacy writer protocol.",
    )
    init.add_argument(
        "--old-writers-stopped",
        required=True,
        help="Evidence/reference confirming all legacy writers have stopped.",
    )
    acquire = sub.add_parser(
        "acquire", help="Acquire or retry one durable local session."
    )
    acquire.add_argument("url")
    acquire.add_argument(
        "--anchor", help="Explicit Issue coordination anchor for an associated PR."
    )
    acquire.add_argument("--session", type=Path, required=True)
    inspect = sub.add_parser(
        "inspect", help="Read this user's complete execution history for a task."
    )
    inspect.add_argument("url")
    inspect.add_argument("--anchor")
    for name in ("check", "quiesce", "release", "guard", "checkpoint", "finish"):
        child = sub.add_parser(name)
        child.add_argument("--session", type=Path, required=True)
        if name in {"checkpoint", "finish"}:
            child.add_argument("--summary", required=True)
        if name == "finish":
            child.add_argument(
                "--outcome", choices=("paused", "completed", "cancelled"), required=True
            )
        if name == "release":
            child.add_argument("--receipt", type=Path, required=True)
        if name == "guard":
            child.add_argument(
                "program",
                nargs=argparse.REMAINDER,
                help="One host command after --; held under the session lock.",
            )
    recover = sub.add_parser(
        "recover",
        help="Retire an inaccessible old run after explicit user-authorized stop verification.",
    )
    recover.add_argument("url")
    recover.add_argument("--anchor")
    recover.add_argument("--run-id", required=True)
    recover.add_argument("--receipt", type=Path, required=True)


def github_api(endpoint: str, *arguments: str) -> dict[str, Any]:
    try:
        process = subprocess.run(
            ["gh", "api", "--hostname", "github.com", endpoint, *arguments],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise WudiTaskError(
            "github_unavailable", "Install and authenticate GitHub CLI."
        ) from exc
    require(
        process.returncode == 0,
        "github_unavailable",
        "GitHub identity/task lookup failed; no execution was started.",
    )
    try:
        result = json.loads(process.stdout)
    except ValueError as exc:
        raise WudiTaskError(
            "github_invalid_response", "GitHub returned invalid JSON."
        ) from exc
    require(
        isinstance(result, dict) and not result.get("errors"),
        "github_invalid_response",
        "GitHub returned an incomplete response.",
    )
    return result


def authenticated_actor() -> dict[str, str]:
    raw = github_api("user")
    require(
        type(raw.get("id")) is int and raw["id"] > 0,
        "github_invalid_identity",
        "GitHub stable user ID is missing.",
    )
    return {
        "actor_id": str(raw["id"]),
        "login": text_field(raw.get("login"), "GitHub login"),
    }


def resolve_task(url: str, anchor: str | None = None) -> dict[str, str]:
    parsed = urlsplit(url)
    match = re.fullmatch(
        r"/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/(issues|pull)/([1-9][0-9]*)/?", parsed.path
    )
    require(
        parsed.scheme == "https"
        and parsed.netloc == "github.com"
        and match is not None,
        "invalid_task_url",
        "Use a github.com Issue or PR URL.",
    )
    assert match is not None
    repo, kind, number = match.groups()
    endpoint = "pulls" if kind == "pull" else "issues"
    raw = github_api(f"repos/{repo}/{endpoint}/{number}")
    # /issues/N can also return a PR; both URL spellings must acquire the same node.
    if kind == "issues" and "pull_request" in raw:
        return resolve_task(f"https://github.com/{repo}/pull/{number}", anchor)
    node_id = text_field(raw.get("node_id"), "GitHub task node ID")
    canonical = text_field(raw.get("html_url"), "GitHub canonical URL")
    if kind == "pull":
        data = github_api(
            "graphql",
            "-f",
            "query=query($id:ID!){node(id:$id){... on PullRequest{closingIssuesReferences(first:100){nodes{id url} pageInfo{hasNextPage}}}}}",
            "-f",
            f"id={node_id}",
        )
        links = (data.get("data") or {}).get("node")
        require(
            isinstance(links, dict)
            and isinstance(links.get("closingIssuesReferences"), dict),
            "github_invalid_response",
            "Cannot determine PR coordination anchor.",
        )
        refs = links["closingIssuesReferences"]
        require(
            not refs.get("pageInfo", {}).get("hasNextPage"),
            "ambiguous_task_anchor",
            "Too many linked Issues; start from the agreed Issue URL.",
        )
        if refs.get("nodes"):
            require(
                anchor is not None,
                "task_anchor_required",
                "PR closes Issue(s); supply --anchor with the agreed Issue URL, or acquire that Issue directly.",
            )
            selected = resolve_task(anchor)
            require(
                selected["task_id"] in {item["id"] for item in refs["nodes"]},
                "invalid_task_anchor",
                "Anchor must be one of this PR's closing Issues.",
            )
            return selected
    require(
        anchor is None,
        "invalid_task_anchor",
        "An anchor is only used for a PR with linked closing Issues.",
    )
    return {"task_id": node_id, "url": canonical}


def journal_write(path: Path, value: dict[str, Any]) -> None:
    atomic_write_json(path, value)
    # Rename durability matters before a remote claim is submitted.
    if os.name == "posix":
        descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def personal_root() -> Path:
    return Path.home() / ".wuditask" / "personal"


def coordinator(config: dict[str, str]) -> GitCoordinator:
    return GitCoordinator(
        remote=config["remote"],
        branch=config["branch"],
        repository_factory=ExecutionRepository,
        write_paths=("data", "hub.json", "coordination.json"),
    )


def _load_config(actor: dict[str, str]) -> dict[str, str]:
    config = read_json(personal_root() / "config.json")
    require(
        isinstance(config, dict) and config.get("actor_id") == actor["actor_id"],
        "coordination_account_mismatch",
        "Configure this environment for the authenticated account before continuing.",
    )
    for key in ("remote", "branch", "environment"):
        text_field(config.get(key), key)
    return config


def _configure(args: argparse.Namespace, actor: dict[str, str]) -> dict[str, Any]:
    remote_url = urlsplit(args.remote)
    require(
        not (
            remote_url.scheme in {"http", "https"}
            and (remote_url.username or remote_url.password)
        ),
        "credential_in_remote",
        "Keep credentials in SSH or the Git credential helper, not the remote URL.",
    )
    root = personal_root()
    root.mkdir(parents=True, mode=0o700, exist_ok=True)
    with _file_lock(root / "config.lock"):
        candidate = {
            "actor_id": actor["actor_id"],
            "remote": args.remote,
            "branch": args.branch,
        }
        path = root / "config.json"
        if path.exists():
            current = read_json(path)
            require(
                all(current.get(k) == v for k, v in candidate.items()),
                "coordination_already_configured",
                "Changing the authority can bypass existing claims. Finish/recover all sessions and perform an explicit migration first.",
            )
            return {"configured": True, "changed": False}
        # Verify access before storing the sole authority. It must already have a commit.
        with coordinator(candidate).snapshot():
            pass
        candidate["environment"] = uuid.uuid4().hex
        journal_write(path, candidate)
        return {"configured": True, "changed": True}


@contextmanager
def session_lock(ticket: dict[str, Any]) -> Iterator[None]:
    session = ticket.get("session", "")
    require(
        isinstance(session, str) and bool(re.fullmatch(r"[a-f0-9]{32}", session)),
        "invalid_session",
        "Invalid local session identity.",
    )
    with _try_file_lock(personal_root() / "sessions" / f"{session}.lock") as acquired:
        require(
            acquired,
            "local_session_busy",
            "Another local process is operating or running this session.",
        )
        yield


def _load_session(
    path: Path, actor: dict[str, str], config: dict[str, str]
) -> dict[str, Any]:
    ticket = read_json(path)
    validate_ticket(ticket)
    require(
        ticket["actor_id"] == actor["actor_id"]
        and ticket["environment"] == config["environment"]
        and ticket.get("remote") == config["remote"]
        and ticket.get("branch") == config["branch"]
        and ticket.get("journal_path") == str(path),
        "execution_session_mismatch",
        "Session belongs to another account, authority or environment; use inspect and handoff.",
    )
    return ticket


def _write(
    coord: GitCoordinator, actor: dict[str, str], action: str, operation: Any
) -> dict[str, Any]:
    return coord.write(
        operation,
        Identity(actor["login"]),
        lambda result: f"Coordinate personal execution: {action}",
    )


def _check(coord: GitCoordinator, ticket: dict[str, Any]) -> dict[str, Any]:
    # A commit in remote ancestry proves historical acceptance, not a current right.
    with coord.snapshot() as repo:
        return repo.assert_current(ticket)


def run(args: argparse.Namespace, tool_root: Path | None = None) -> dict[str, Any]:
    require(
        not args.local
        and args.hub is None
        and not args.actor
        and not os.environ.get("WUDITASK_ACTOR"),
        "coordination_authenticated_remote_required",
        "Personal coordination uses authenticated GitHub identity and its configured remote.",
    )
    actor = authenticated_actor()
    if args.coord_command == "setup":
        from .execution_setup import setup

        return setup(args, actor, tool_root or Path(__file__).resolve().parents[1])
    if args.coord_command == "configure":
        return _configure(args, actor)
    config = _load_config(actor)
    coord = coordinator(config)
    action = args.coord_command
    if action == "inventory":
        with coord.snapshot() as repo:
            return repo.inventory()
    if action == "init":
        return _write(
            coord,
            actor,
            action,
            lambda repo: repo.initialize(actor, args.old_writers_stopped),
        )
    if action in {"inspect", "recover"}:
        task = resolve_task(args.url, args.anchor)
        if action == "inspect":
            with coord.snapshot() as repo:
                return repo.inspect(actor["actor_id"], task["task_id"])
        receipt = read_json(args.receipt)
        ticket = {**actor, **task, "run_id": args.run_id}
        return _write(
            coord,
            actor,
            action,
            lambda repo: repo.release(ticket, receipt, recovery=True),
        )
    path = args.session.expanduser().resolve()
    if action in {"acquire", "takeover"}:
        task = resolve_task(args.url, args.anchor)
        request = {"action": action}
        if action == "takeover":
            request.update(expected_run_id=args.expected_run_id, reason=args.reason)
        with _file_lock(path.with_suffix(path.suffix + ".lock")):
            if path.exists():
                ticket = _load_session(path, actor, config)
                require(
                    ticket["task_id"] == task["task_id"],
                    "session_task_mismatch",
                    "Use a new session path for a different task.",
                )
                require(
                    ticket.get("request", {"action": "acquire"}) == request,
                    "session_request_mismatch",
                    "Retry the same request, or use a new session file.",
                )
            else:
                ticket = {
                    **actor,
                    **task,
                    **config,
                    "session": uuid.uuid4().hex,
                    "run_id": new_run_id(),
                    "guard": "not_started",
                    "journal_path": str(path),
                    "request": request,
                }
                # This journal survives a lost response; retries must reuse it.
                journal_write(path, ticket)
        with session_lock(ticket):

            def operation(repo: ExecutionRepository) -> dict[str, Any]:
                if action == "takeover":
                    return repo.takeover(ticket, args.expected_run_id, args.reason)
                return repo.acquire(ticket)

            result = _write(coord, actor, action, operation)
            result["run"] = _check(coord, ticket)
            result["session_file"] = str(path)
            return result
    ticket = _load_session(path, actor, config)
    with session_lock(ticket):
        # Reload after locking to avoid a second caller using stale journal state.
        ticket = _load_session(path, actor, config)
        if action == "check":
            return {"run": _check(coord, ticket)}
        if action == "checkpoint":
            return _write(
                coord, actor, action, lambda repo: repo.checkpoint(ticket, args.summary)
            )
        if action == "finish":
            return _write(
                coord,
                actor,
                action,
                lambda repo: repo.finish(ticket, args.summary, args.outcome),
            )
        if action == "quiesce":
            return _write(coord, actor, action, lambda repo: repo.quiesce(ticket))
        if action == "release":
            receipt = read_json(args.receipt)
            return _write(
                coord, actor, action, lambda repo: repo.release(ticket, receipt)
            )
        if action == "guard":
            return _guard(args.program, coord, ticket, path)
    raise WudiTaskError("unknown_coordination_command", action)


def _guard(
    program: list[str], coord: GitCoordinator, ticket: dict[str, Any], path: Path
) -> dict[str, Any]:
    if program[:1] == ["--"]:
        program = program[1:]
    require(
        bool(program), "guard_command_required", "Supply the host command after --."
    )
    require(
        ticket.get("guard") == "not_started",
        "session_already_started",
        "This session has already attempted a host launch. Finish this execution or explicitly take over into a new session; do not start a second host under this journal.",
    )
    _check(coord, ticket)
    ticket["guard"] = "started"
    journal_write(path, ticket)
    # The held session lock prevents another cooperating caller from launching or
    # finishing this session. This does not fence remote descendants or takeover.
    try:
        process = subprocess.run(program, check=False)
    except OSError as exc:
        ticket["guard"] = "launch_failed"
        journal_write(path, ticket)
        raise WudiTaskError(
            "host_launch_failed",
            "Host command failed to launch; inspect the checkpoint before finishing.",
        ) from exc
    ticket.update(guard="exited", returncode=process.returncode)
    journal_write(path, ticket)
    if process.returncode:
        raise WudiTaskError(
            "host_command_failed",
            "Host command failed; execution remains occupied until finish or explicit takeover.",
            details={
                "run_id": ticket["run_id"],
                "returncode": process.returncode,
                "released": False,
            },
            exit_code=4,
        )
    return {
        "run_id": ticket["run_id"],
        "returncode": process.returncode,
        "released": False,
        "next": "Save a checkpoint and finish, or use optional strict release. A host exit alone does not end the execution record.",
    }
