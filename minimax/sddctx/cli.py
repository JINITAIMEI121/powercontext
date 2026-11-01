"""Command line entry point of the SDD context module."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from .db import SeekdbStore
from .ingest import ingest
from .retrieve import build_context, render_markdown
from .state import load_state
from . import agent as agent_module

DEFAULT_PROJECT = Path(__file__).resolve().parent.parent / "demo_project"
DEFAULT_DB = Path(__file__).resolve().parent.parent / ".seekdb"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sddctx", description="seekdb-backed SDD context module")
    parser.add_argument("--project", default=str(DEFAULT_PROJECT), help="business project root")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="embedded seekdb data dir")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="drop local state and recreate the embedded database")
    p_ingest = sub.add_parser("ingest", help="import specs, code and tests into seekdb")
    p_ingest.add_argument("--stats", action="store_true")

    p_ctx = sub.add_parser("context", help="build the development context for a task")
    p_ctx.add_argument("task")
    p_ctx.add_argument("--task-id", default=None)
    p_ctx.add_argument("--json", action="store_true")

    p_run = sub.add_parser("run", help="run one development task end to end")
    p_run.add_argument("task")
    p_run.add_argument("--task-id", default="order-date-range")
    p_run.add_argument("--provider", choices=["minimax", "mock"], default="minimax")
    p_run.add_argument("--dry-run", action="store_true", help="do not apply model edits")

    p_status = sub.add_parser("status", help="read persisted task state")
    p_status.add_argument("--task-id", default="order-date-range")

    p_follow = sub.add_parser("follow-up", help="continue a task reusing persisted state")
    p_follow.add_argument("task")
    p_follow.add_argument("--task-id", default="order-date-range")
    p_follow.add_argument("--provider", choices=["minimax", "mock"], default="minimax")

    args = parser.parse_args(argv)
    project = Path(args.project).resolve()

    if args.command == "init":
        target = Path(args.db)
        if target.exists():
            shutil.rmtree(target)
        SeekdbStore(target).close()
        print(f"initialised embedded seekdb at {target}")
        return 0

    store = SeekdbStore(Path(args.db))
    try:
        if args.command == "ingest":
            stats = ingest(store, project)
            print(json.dumps(stats, ensure_ascii=False) if args.stats else
                  "imported " + ", ".join(f"{k}={v}" for k, v in stats.items()))
            return 0
        if args.command == "context":
            context = build_context(store, args.task, task_id=args.task_id)
            print(json.dumps(context, ensure_ascii=False, indent=2) if args.json
                  else render_markdown(context))
            return 0
        if args.command == "run":
            result = agent_module.run_task(store, project, args.task, args.task_id,
                                           args.provider, apply=not args.dry_run)
            summary = {k: v for k, v in result.items() if k not in {"before", "after"}}
            summary["before"] = {k: v["passed"] for k, v in result["before"].items()}
            if result.get("after"):
                summary["after"] = {k: v["passed"] for k, v in result["after"].items()}
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 0
        if args.command == "status":
            state = load_state(store, args.task_id)
            print(json.dumps(state, ensure_ascii=False, indent=2) if state else "no state")
            return 0
        if args.command == "follow-up":
            result = agent_module.refresh_state(store, project, args.task, args.task_id,
                                                args.provider)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
    finally:
        store.close()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
