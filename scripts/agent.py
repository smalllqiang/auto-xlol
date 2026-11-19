#!/usr/bin/env python3
"""Run one AI planning cycle: brief -> model -> typed plan -> runner.

Default is a dry run: the model still plans, the runner still walks the steps,
but write tools only report what they *would* do.  Add ``--execute`` to let the
plan touch the account.  There is no allow-list on purpose (ADR-030): the game
is the only judge of what is legal, and every step is journaled either way.

The model endpoint is configured with ``NEWSTAR_AGENT_BASE_URL`` /
``NEWSTAR_AGENT_MODEL`` / ``NEWSTAR_AGENT_API_KEY`` (or the matching flags).
``state/STOP`` stops the cycle before it reads anything.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Sequence

from local_config import add_session_args, load_config, make_client

from autoplay import AgentConfig, load_directive, provider_from_env, run_agent_cycle
from autoplay.journal import append_jsonl
from autoplay.llm import OpenAICompatibleProvider


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--config", help="path to .env or passwd.txt (default: autodetect)")
    parser.add_argument("--base-url", help="override game base url")
    parser.add_argument("--username", help="override username")
    parser.add_argument("--password", help="override password")
    parser.add_argument("--universe", help="override universe name/id")
    parser.add_argument("--no-verify-ssl", action="store_true", help="skip TLS verification")
    parser.add_argument("--interval", type=float, default=0.3, help="seconds between requests")
    parser.add_argument("--execute", action="store_true", help="actually act (default: dry run)")
    parser.add_argument("--json", action="store_true", help="print the full cycle as JSON")
    parser.add_argument("--journal", default="state/journal.jsonl")
    parser.add_argument("--journal-max-bytes", type=int, default=0)
    parser.add_argument("--stop-file", default="state/STOP")
    parser.add_argument("--directive", default="state/directive.json")
    parser.add_argument("--max-rounds", type=int, default=6, help="model/tool round trips")
    parser.add_argument("--max-steps", type=int, default=8, help="steps allowed per cycle")
    parser.add_argument("--max-chars", type=int, default=6000, help="brief character budget")
    parser.add_argument("--agent-base-url", help="override NEWSTAR_AGENT_BASE_URL")
    parser.add_argument("--agent-model", help="override NEWSTAR_AGENT_MODEL")
    parser.add_argument("--agent-api-key", help="override NEWSTAR_AGENT_API_KEY")
    parser.add_argument("--agent-temperature", type=float, default=None)
    add_session_args(parser)
    return parser.parse_args(argv)


def _provider(args: argparse.Namespace):
    if args.agent_base_url or args.agent_model:
        if not (args.agent_base_url and args.agent_model):
            raise SystemExit("--agent-base-url 与 --agent-model 必须同时提供")
        return OpenAICompatibleProvider(
            args.agent_base_url,
            args.agent_model,
            api_key=args.agent_api_key or os.getenv("NEWSTAR_AGENT_API_KEY", ""),
            temperature=(
                args.agent_temperature
                if args.agent_temperature is not None
                else float(os.getenv("NEWSTAR_AGENT_TEMPERATURE", "0.2"))
            ),
        )
    return provider_from_env()


def render(result) -> str:
    decision = result.decision or {}
    report = result.report or {}
    lines = [
        f"阶段  {result.phase}",
        f"目标  {result.goal}",
        f"状态  {result.status}  {result.reason}",
    ]
    goal = decision.get("goal")
    if goal:
        lines.append(f"计划  {goal}")
    if decision.get("fallback"):
        lines.append("（模型不可用，已回退到确定性规划器）")
    steps = report.get("steps") or []
    if steps:
        lines.append("步骤:")
        for step in steps:
            spec = step.get("step") or {}
            outcome = step.get("result") or {}
            detail = outcome.get("status") or step.get("status")
            message = outcome.get("message") or step.get("message") or ""
            lines.append(
                f"  {step.get('index', 0) + 1}. {spec.get('tool')} "
                f"{json.dumps(spec.get('args') or {}, ensure_ascii=False)} -> {detail}"
            )
            if message:
                lines.append(f"     {message}")
    else:
        lines.append("步骤:（无）")
    lines.append(f"报告  {report.get('status')}  {report.get('reason') or ''}".rstrip())
    if result.usage:
        lines.append(f"用量  {json.dumps(result.usage, ensure_ascii=False)}")
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    config = load_config(args)
    if not config.verify_ssl:
        warnings.filterwarnings("ignore", message="Unverified HTTPS request")

    provider = _provider(args)
    if provider is None:
        print(
            "未配置模型：设置 NEWSTAR_AGENT_BASE_URL / NEWSTAR_AGENT_MODEL，"
            "或传 --agent-base-url/--agent-model",
            file=sys.stderr,
        )
        return 2

    directive = load_directive(args.directive)
    client = make_client(config)
    try:
        result = run_agent_cycle(
            client,
            provider,
            directive=directive,
            config=AgentConfig(
                dry_run=not args.execute,
                max_rounds=args.max_rounds,
                max_steps=args.max_steps,
                max_chars=args.max_chars,
                stop_file=args.stop_file,
            ),
        )
    finally:
        client.close()

    payload = result.as_dict()
    payload["kind"] = "agent"
    payload["at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    payload["dry_run"] = not args.execute
    for error in directive.errors:
        payload.setdefault("directive_errors", []).append(error)
    append_jsonl(Path(args.journal), payload, max_bytes=args.journal_max_bytes)

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(render(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
