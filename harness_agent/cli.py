from __future__ import annotations

import argparse
from pathlib import Path
import sys

from .config import load_config
from .workflow import Workflow


def add_build_args(command: argparse.ArgumentParser) -> None:
    command.add_argument("--root", required=True, help="Target project root")
    command.add_argument("--goal-file", default="goal.md", help="Goal Markdown file, relative to --root by default")
    command.add_argument("--config", default="configs/agents.example.json", help="Agent config JSON")
    command.add_argument("--skills-dir", default="skills", help="Skills directory")
    command.add_argument("--agents-md", default="AGENTS.md", help="Global agent instructions Markdown")


def read_goal(root: Path, goal_file: str) -> str:
    path = Path(goal_file)
    target = path if path.is_absolute() else root / path
    if not target.exists():
        raise FileNotFoundError(f"Goal file not found: {target}")
    goal = target.read_text(encoding="utf-8").strip()
    if not goal:
        raise ValueError(f"Goal file is empty: {target}")
    return goal


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="harness-agent")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Run plan, prompts, and execute without review pauses")
    add_build_args(run)

    plan = sub.add_parser("plan", help="Generate docs/requirements.md for human review")
    add_build_args(plan)

    prompts = sub.add_parser("prompts", help="Generate per-agent prompts for human review")
    add_build_args(prompts)

    execute = sub.add_parser("execute", help="Run enabled agents using reviewed requirements and prompts")
    add_build_args(execute)

    refine = sub.add_parser("refine", help="Run a local refinement workflow")
    refine.add_argument("--root", required=True, help="Target project root")
    refine.add_argument("--request", required=True, help="Change request")
    refine.add_argument("--files", default="", help="Comma-separated relative files allowed for this refinement")
    refine.add_argument("--config", default="configs/agents.example.json", help="Agent config JSON")
    refine.add_argument("--skills-dir", default="skills", help="Skills directory")
    refine.add_argument("--agents-md", default="AGENTS.md", help="Global agent instructions Markdown")

    args = parser.parse_args(argv)
    base = Path.cwd()
    config = load_config((base / args.config).resolve() if not Path(args.config).is_absolute() else Path(args.config))
    skills_dir = (base / args.skills_dir).resolve() if not Path(args.skills_dir).is_absolute() else Path(args.skills_dir)
    agents_md = (base / args.agents_md).resolve() if not Path(args.agents_md).is_absolute() else Path(args.agents_md)
    global_prompt = agents_md.read_text(encoding="utf-8") if agents_md.exists() else ""
    root = Path(args.root)
    workflow = Workflow(root, config, skills_dir, global_prompt)

    if args.command == "run":
        goal = read_goal(root, args.goal_file)
        results = workflow.run(goal)
    elif args.command == "plan":
        goal = read_goal(root, args.goal_file)
        results = workflow.plan(goal)
    elif args.command == "prompts":
        goal = read_goal(root, args.goal_file)
        results = workflow.generate_prompts(goal)
    elif args.command == "execute":
        goal = read_goal(root, args.goal_file)
        results = workflow.execute(goal)
    elif args.command == "refine":
        files = [item.strip() for item in args.files.split(",") if item.strip()]
        results = workflow.refine(args.request, files=files)
    else:
        parser.error("Unknown command")
        return

    for name, summary in results.items():
        print(f"[{name}] {summary}")


if __name__ == "__main__":
    main(sys.argv[1:])
