"""Agent-facing local workflow commands behind the unified WudiTask skill."""

from __future__ import annotations

import time
from pathlib import Path

from .agent_plan import PlanStore
from .agent_board import render_board
from .coordination import require
from .util import read_json


def add_parser(commands):
    parser = commands.add_parser(
        "work", help="Plan, delegate, review and resume one task locally."
    )
    sub = parser.add_subparsers(dest="work_command", required=True)
    for name in (
        "init",
        "show",
        "poll",
        "approve",
        "decision",
        "item",
        "prepare",
        "launch",
        "observe",
        "report",
        "review",
        "delivery",
        "board",
        "reconcile",
        "watch",
        "adopt",
        "reuse",
    ):
        p = sub.add_parser(name)
        p.add_argument("--plan", type=Path, required=True)
        if name in {
            "approve",
            "decision",
            "item",
            "prepare",
            "observe",
            "review",
            "delivery",
            "reuse",
        }:
            p.add_argument("--revision", type=int, required=True)
        if name in {"init", "adopt"}:
            binding = p.add_mutually_exclusive_group(required=True)
            binding.add_argument("--session", type=Path)
            binding.add_argument("--local-reason")
        if name in {"init", "item", "delivery"}:
            p.add_argument("--spec", type=Path, required=True)
        if name in {"decision", "item", "delivery"}:
            p.add_argument("--id", required=True)
        if name in {"prepare", "launch", "observe", "report", "review"}:
            p.add_argument("--attempt", required=True)
        if name in {"approve", "decision", "observe", "review", "adopt", "reuse"}:
            p.add_argument("--reference", required=True)
        if name == "decision":
            p.add_argument(
                "--status", choices=("pending", "approved", "rejected"), required=True
            )
        if name == "reuse":
            p.add_argument("--item", required=True)
        if name == "prepare":
            p.add_argument("--item", required=True)
            p.add_argument(
                "--backend", choices=("codex-native", "claude", "codex"), required=True
            )
            p.add_argument("--workspace", type=Path, required=True)
            p.add_argument("--prompt", type=Path, required=True)
            p.add_argument("--preflight", type=Path, required=True)
            p.add_argument(
                "--continue-attempt",
                help="Continue the exited original author after manager review.",
            )
        if name == "launch":
            p.add_argument(
                "--launcher",
                type=Path,
                help="Verified executable wrapper; provider credentials stay outside the plan.",
            )
            p.add_argument(
                "--wake-command",
                type=Path,
                help="JSON argv for a verified host wake bridge; omitted means manual resume.",
            )
        if name == "observe":
            p.add_argument(
                "--state",
                choices=("running", "exited", "not_started", "unknown"),
                required=True,
            )
            p.add_argument("--handle")
        if name == "report":
            p.add_argument("--result", type=Path, required=True)
        if name == "review":
            p.add_argument(
                "--verdict",
                choices=("accepted", "needs_rework", "blocked", "cancelled"),
                required=True,
            )
        if name == "board":
            p.add_argument("--output", type=Path)
        if name == "watch":
            p.add_argument("--interval", type=float, default=10)
            p.add_argument("--iterations", type=int, default=6)
        if name == "adopt":
            p.add_argument("--source", type=Path, required=True)


def run(args, tool_root=None):
    require(
        not args.local and args.hub is None and not args.actor,
        "work_scope_invalid",
        "Work uses its recorded S2 journal or explicit local maintenance binding.",
    )
    store = PlanStore(args.plan)
    command = args.work_command
    if command == "init":
        result = store.init(
            read_json(args.spec), session=args.session, local_reason=args.local_reason
        )
    elif command == "adopt":
        result = store.adopt(
            args.source,
            args.reference,
            session=args.session,
            local_reason=args.local_reason,
        )
    elif command == "reuse":
        result = store.reuse(args.item, args.reference, args.revision)
    elif command == "show":
        result = store.load()
    elif command == "poll":
        result = store.poll()
    elif command == "approve":
        result = store.approve(args.reference, args.revision)
    elif command == "decision":
        result = store.decision(args.id, args.status, args.reference, args.revision)
    elif command == "item":
        result = store.edit_item(args.id, read_json(args.spec), args.revision)
    elif command == "prepare":
        result = store.prepare(
            args.item,
            args.attempt,
            args.backend,
            args.workspace,
            args.prompt.read_text(),
            read_json(args.preflight),
            args.revision,
            args.continue_attempt,
        )
    elif command == "launch":
        result = store.launch(
            args.attempt,
            launcher=str(args.launcher) if args.launcher else None,
            wake_command=read_json(args.wake_command) if args.wake_command else None,
        )
    elif command == "observe":
        result = store.observe(
            args.attempt, args.state, args.reference, args.revision, args.handle
        )
    elif command == "report":
        result = store.report(args.attempt, read_json(args.result))
    elif command == "review":
        result = store.review(args.attempt, args.verdict, args.reference, args.revision)
    elif command == "delivery":
        result = store.delivery(args.id, read_json(args.spec), args.revision)
    elif command == "reconcile":
        result = store.reconcile()
    elif command == "watch":
        require(
            0 < args.interval <= 60 and 0 < args.iterations <= 360,
            "invalid_watch_bound",
            "Use interval (0,60] seconds and 1–360 iterations.",
        )
        for index in range(args.iterations):
            result = store.reconcile()
            if (
                result["pending_events"]
                or result["ready_items"]
                or result["plan"]["summary_state"] != "active"
            ):
                break
            if index + 1 < args.iterations:
                time.sleep(args.interval)
        result["manual_resume_required"] = True
        result["watch_note"] = (
            "Foreground observer only; no host thread scheduler was installed."
        )
    elif command == "board":
        result = {"output": str((args.output or store.root / "board.html").resolve())}
    else:
        raise AssertionError(command)
    # Workers may publish results, but only the manager renders authoritative state.
    if command != "report":
        render_board(
            store.load(),
            args.output
            if command == "board" and args.output
            else store.root / "board.html",
        )
    return {"ok": True, "work": result}
