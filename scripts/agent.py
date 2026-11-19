#!/usr/bin/env python3
"""Run the AI planning loop: brief -> model -> typed plan -> runner.

Default is one dry-run cycle: the model still plans, the runner still walks the
steps, but write tools only report what they *would* do.  Add ``--execute`` to
let the plan touch the account, and ``--every N`` to keep running.

There is no allow-list on purpose (ADR-030): the game is the only judge of what
is legal, and every step is journaled either way.  ``state/STOP`` stops the loop
at the next cycle; a real run also takes ``state/agent.lock`` so two writers can
never share the account.

The model endpoint is configured with ``NEWSTAR_AGENT_*`` in ``.env``; use
``scripts/config.py --validate`` to check the merged config without connecting.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Sequence, Tuple

from local_config import add_session_args, load_config, make_client

from autoplay import AgentConfig, load_directive, run_agent_cycle
from autoplay.daemon import backoff_delay
from autoplay.journal import append_jsonl
from autoplay.llm import OpenAICompatibleProvider
from autoplay.memory import decision_history, last_brief, next_cycle, read_agent_entries
from autoplay.runtime import acquire_lock, alive, read_lock, release_lock, write_status


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
    parser.add_argument(
        "--history", type=int, default=8, help="recent agent cycles to include in the brief"
    )
    parser.add_argument(
        "--every",
        type=float,
        default=0.0,
        metavar="SECONDS",
        help="keep running with this cycle interval (0 = run once and exit)",
    )
    parser.add_argument("--max-cycles", type=int, default=0, help="stop after N cycles (0 = never)")
    parser.add_argument("--max-failures", type=int, default=10, help="exit after N consecutive errors")
    parser.add_argument("--backoff-base", type=float, default=30.0)
    parser.add_argument("--backoff-max", type=float, default=900.0)
    parser.add_argument("--lock-file", default="state/agent.lock")
    parser.add_argument("--daemon-lock", default="state/daemon.lock")
    parser.add_argument("--status-file", default="state/agent.json")
    parser.add_argument("--agent-base-url", help="override NEWSTAR_AGENT_BASE_URL")
    parser.add_argument("--agent-model", help="override NEWSTAR_AGENT_MODEL")
    parser.add_argument("--agent-api-key", help="override NEWSTAR_AGENT_API_KEY")
    parser.add_argument("--agent-temperature", type=float, default=None)
    parser.add_argument("--agent-timeout", type=float, default=None, help="模型请求超时（秒）")
    add_session_args(parser)
    return parser.parse_args(argv)


def _provider(config):
    """Build the provider from the merged config (file + env + flags)."""

    settings = config.agent
    if not settings.configured:
        return None
    return OpenAICompatibleProvider(
        settings.base_url,
        settings.model,
        api_key=settings.api_key,
        temperature=settings.temperature,
        timeout=settings.timeout,
    )


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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def run_cycle(args, provider, directive, config) -> Tuple[dict, object]:
    """One observe -> decide -> run cycle, journaled.  Returns (payload, result)."""

    journal = Path(args.journal)
    # Previous cycles give the model a short memory: what it tried, and what the
    # account looked like then (the delta baseline).
    entries = read_agent_entries(journal, limit=args.history)
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
            history=decision_history(entries),
            previous=last_brief(entries),
        )
    finally:
        client.close()

    payload = result.as_dict()
    payload["kind"] = "agent"
    payload["cycle"] = next_cycle(entries)
    payload["at"] = _now()
    payload["dry_run"] = not args.execute
    if directive.errors:
        payload["directive_errors"] = list(directive.errors)
    append_jsonl(journal, payload, max_bytes=args.journal_max_bytes)
    return payload, result


def _write_heartbeat(args, *, cycles: int, failures: int, status: str, reason: str, phase: str, next_at: str) -> None:
    try:
        write_status(
            Path(args.status_file),
            {
                "pid": os.getpid(),
                "updated_at": _now(),
                "cycles": cycles,
                "failures": failures,
                "status": status,
                "reason": reason,
                "phase": phase,
                "interval": args.every,
                "dry_run": not args.execute,
                "next_at": next_at,
            },
        )
    except OSError:
        # A full disk must not stop the loop; the journal is the real record.
        pass


def _guard_writers(args) -> Optional[int]:
    """Refuse to write while another writer owns the account.  Returns an fd or None."""

    if not args.execute:
        return None
    daemon_pid = read_lock(Path(args.daemon_lock))
    if daemon_pid and alive(daemon_pid):
        print(
            f"确定性守护正在运行（pid {daemon_pid}）；同一账号不能并发写，"
            "请先停掉 run_forever",
            file=sys.stderr,
        )
        raise SystemExit(3)
    try:
        return acquire_lock(Path(args.lock_file))
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(3) from None


def _busy_code(exc: SystemExit) -> int:
    return int(exc.code) if isinstance(exc.code, int) else 1


def run_loop(args, provider, config) -> int:
    """Run cycles until STOP, SIGTERM, ``--max-cycles`` or too many failures."""

    try:
        lock_fd = _guard_writers(args)
    except SystemExit as exc:
        return _busy_code(exc)
    lock_path = Path(args.lock_file)

    stop = {"requested": False}

    def handle_signal(signum, frame):  # noqa: ANN001, ARG001
        stop["requested"] = True

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    cycles = 0
    failures = 0
    exit_code = 0
    _write_heartbeat(
        args, cycles=0, failures=0, status="starting", reason="", phase="", next_at=""
    )
    try:
        while not stop["requested"]:
            if args.max_cycles and cycles >= args.max_cycles:
                break
            directive = load_directive(args.directive)
            started = time.monotonic()
            status = "ok"
            reason = ""
            phase = ""
            delay = max(0.0, float(args.every))
            try:
                payload, result = run_cycle(args, provider, directive, config)
                status = result.status
                reason = result.reason
                phase = result.phase
                failures = 0
                if args.json:
                    print(json.dumps(payload, ensure_ascii=False, indent=2))
                else:
                    print(render(result), flush=True)
                if result.status == "stopped":
                    exit_code = 0
                    break
            except SystemExit:
                raise
            except Exception as exc:  # noqa: BLE001 - the loop must survive a bad cycle
                failures += 1
                status = "error"
                reason = f"{type(exc).__name__}: {exc}"
                delay = backoff_delay(failures, args.backoff_base, args.backoff_max)
                print(f"[cycle {cycles + 1}] {reason}", file=sys.stderr, flush=True)
                if failures >= max(1, args.max_failures):
                    exit_code = 1
                    break
            cycles += 1
            next_at = datetime.fromtimestamp(
                time.time() + max(0.0, started + delay - time.monotonic()), timezone.utc
            ).isoformat(timespec="seconds")
            _write_heartbeat(
                args,
                cycles=cycles,
                failures=failures,
                status=status,
                reason=reason,
                phase=phase,
                next_at=next_at,
            )
            deadline = started + delay
            while not stop["requested"] and time.monotonic() < deadline:
                if Path(args.stop_file).exists():
                    stop["requested"] = True
                    break
                time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))
    finally:
        _write_heartbeat(
            args,
            cycles=cycles,
            failures=failures,
            status="stopped",
            reason="",
            phase="",
            next_at="",
        )
        if lock_fd is not None:
            release_lock(lock_fd, lock_path)
    return exit_code


def run_once(args, provider, config) -> int:
    """One cycle, still guarded so it cannot race a running loop."""

    try:
        lock_fd = _guard_writers(args)
    except SystemExit as exc:
        return _busy_code(exc)
    try:
        directive = load_directive(args.directive)
        payload, result = run_cycle(args, provider, directive, config)
    finally:
        if lock_fd is not None:
            release_lock(lock_fd, Path(args.lock_file))
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(render(result))
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    config = load_config(args)
    if not config.verify_ssl:
        warnings.filterwarnings("ignore", message="Unverified HTTPS request")

    provider = _provider(config)
    if provider is None:
        print(
            "未配置模型：在 .env 里填 NEWSTAR_AGENT_BASE_URL / NEWSTAR_AGENT_MODEL"
            "（或传 --agent-base-url/--agent-model），可用 scripts/config.py --validate 检查",
            file=sys.stderr,
        )
        return 2

    if args.every > 0:
        return run_loop(args, provider, config)
    return run_once(args, provider, config)


if __name__ == "__main__":
    sys.exit(main())
