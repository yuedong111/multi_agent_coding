from __future__ import annotations

import argparse
from pathlib import Path
import sys

from .config import load_config
from .workflow import Workflow


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="harness-agent")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Run the full multi-agent build workflow")
    run.add_argument("--root", required=True, help="Target project root")
    run.add_argument("--goal", required=True, help="Project goal")
    run.add_argument("--config", default="configs/agents.example.json", help="Agent config JSON")
    run.add_argument("--skills-dir", default="skills", help="Skills directory")
    run.add_argument("--agents-md", default="AGENTS.md", help="Global agent instructions Markdown")

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
    workflow = Workflow(Path(args.root), config, skills_dir, global_prompt)

    if args.command == "run":
        results = workflow.run(args.goal)
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
