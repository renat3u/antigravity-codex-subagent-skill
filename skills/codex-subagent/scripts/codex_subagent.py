#!/usr/bin/env python3
"""
Lightweight Codex CLI subagent wrapper for Google Antigravity (Gemini -> Codex delegation).

Features:
- Auto-discovers Codex CLI binary across PATH, common CLI locations, and macOS ChatGPT.app.
- Resolves shorthand model & reasoning-effort aliases (English & Chinese).
- Isolates headless runs from heavy desktop plugins (--ignore-user-config by default).
- Smart session persistence: keeps session_id on write tasks (for `resume`), uses
  --ephemeral on read-only tasks unless overridden.
- Saves noisy JSONL event traces & stderr to a temp log directory and prints ONLY
  a compact metadata header + final report to stdout to save Gemini context quota.

Zero third-party dependencies. Compatible with Python 3.9+.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_PRIMARY_MODEL = "gpt-6.2-sol"

MODEL_ALIASES: Dict[str, str] = {
    # Current generation (6.2 / 6.1)
    "sol": "gpt-6.2-sol",
    "6.2-sol": "gpt-6.2-sol",
    "gpt6.2-sol": "gpt-6.2-sol",
    "astra": "gpt-6.2-astra",
    "6.2-astra": "gpt-6.2-astra",
    "gpt6.2-astra": "gpt-6.2-astra",
    "terra": "gpt-6.1-terra",
    "6.1-terra": "gpt-6.1-terra",
    "gpt6.1-terra": "gpt-6.1-terra",
    "reserve": "gpt-reserve",
    "mini": "gpt-reserve",
    "fast": "gpt-reserve",
    # Previous / legacy generations
    "6.1-sol": "gpt-6.1-sol",
    "gpt6.1-sol": "gpt-6.1-sol",
    "6-astra": "gpt-6-astra",
    "6-sol": "gpt-6-sol",
    "6-luna": "gpt-6-luna",
    "luna": "gpt-6-luna",
    "5.6-sol": "gpt-5.6-sol",
    "5.6-terra": "gpt-5.6-terra",
    "5.6-luna": "gpt-5.6-luna",
}

EFFORT_ALIASES: Dict[str, str] = {
    "low": "low",
    "l": "low",
    "light": "low",
    "低": "low",
    "medium": "medium",
    "med": "medium",
    "m": "medium",
    "normal": "medium",
    "中": "medium",
    "high": "high",
    "h": "high",
    "高": "high",
    "xhigh": "xhigh",
    "xh": "xhigh",
    "extra": "xhigh",
    "超高": "xhigh",
    "极高": "xhigh",
    "max": "max",
    "最大": "max",
    "ultra": "ultra",
    "u": "ultra",
    "拉满": "ultra",
    "满血": "ultra",
    "深度": "ultra",
}

MODE_ALIASES: Dict[str, str] = {
    "read": "read-only",
    "ro": "read-only",
    "read-only": "read-only",
    "analyze": "read-only",
    "write": "workspace-write",
    "rw": "workspace-write",
    "edit": "workspace-write",
    "code": "workspace-write",
    "workspace-write": "workspace-write",
    "full": "danger-full-access",
    "danger": "danger-full-access",
    "danger-full-access": "danger-full-access",
    "bypass": "bypass-all",
    "bypass-all": "bypass-all",
}

HANDOFF_CONTRACT = """
---
[Subagent Handoff Contract]
Execute this task autonomously (inspect files, apply edits if requested, and run relevant checks/tests yourself).
Keep your final response compact and high-signal so the parent orchestrator does not waste context tokens:
- STATUS: DONE | PARTIAL | BLOCKED
- SUMMARY: Concise bullet points of findings, root causes, or implemented changes
- CHANGED_FILES: Each created/modified/deleted file path + 1-line description (or "None")
- VERIFICATION: Commands/tests executed and their results (or why skipped)
- RISKS_OR_OPEN_ITEMS: Blockers, sandbox denials, edge cases, or follow-ups (or "None")
Do NOT dump full source files or large raw diffs into the final response unless explicitly asked.
""".strip()

SUBCOMMANDS = {"run", "resume", "fork", "review", "models", "doctor"}


def get_codex_home() -> Path:
    env_home = os.environ.get("CODEX_HOME")
    return Path(env_home).expanduser() if env_home else (Path.home() / ".codex")


def candidate_codex_paths() -> List[Path]:
    home = Path.home()
    return [
        home / ".local" / "bin" / "codex",
        Path("/opt/homebrew/bin/codex"),
        Path("/usr/local/bin/codex"),
        home / ".npm-global" / "bin" / "codex",
        home / ".bun" / "bin" / "codex",
        Path("/Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex"),
        Path("/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex"),
        home / "Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex",
        home / "Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex",
    ]


def find_codex_bin() -> str:
    env_bin = os.environ.get("CODEX_BIN")
    if env_bin:
        expanded = Path(env_bin).expanduser()
        if expanded.is_file() and os.access(expanded, os.X_OK):
            return str(expanded)
    which_bin = shutil.which("codex")
    if which_bin:
        return which_bin
    for path in candidate_codex_paths():
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    raise FileNotFoundError(
        "Codex CLI binary not found. Install `@openai/codex`, install ChatGPT Desktop on macOS, "
        "or set the CODEX_BIN environment variable."
    )


def default_model_from_env_or_cache() -> str:
    env_model = os.environ.get("CODEX_DEFAULT_MODEL")
    if env_model and env_model.strip():
        return env_model.strip()
    cache_path = get_codex_home() / "models_cache.json"
    if cache_path.exists():
        try:
            data = json.loads(cache_path.read_text(encoding="utf-8"))
            slugs = [
                m.get("slug")
                for m in (data.get("models") or [])
                if isinstance(m, dict) and m.get("slug")
            ]
            if DEFAULT_PRIMARY_MODEL in slugs:
                return DEFAULT_PRIMARY_MODEL
            visible = [
                m.get("slug")
                for m in (data.get("models") or [])
                if isinstance(m, dict) and m.get("slug") and m.get("visibility") == "list"
            ]
            if visible:
                return str(visible[0])
        except (OSError, json.JSONDecodeError):
            pass
    return DEFAULT_PRIMARY_MODEL


def resolve_model(raw: Optional[str]) -> str:
    if not raw or raw.strip().lower() == "default":
        return default_model_from_env_or_cache()
    key = raw.strip().lower()
    return MODEL_ALIASES.get(key, raw.strip())


def resolve_effort(raw: Optional[str], sandbox_mode: str) -> str:
    if not raw or raw.strip().lower() == "auto":
        return "medium" if sandbox_mode == "read-only" else "high"
    key = raw.strip().lower()
    if key in EFFORT_ALIASES:
        return EFFORT_ALIASES[key]
    return raw.strip()


def resolve_mode(raw: Optional[str]) -> str:
    if not raw:
        return "read-only"
    key = raw.strip().lower()
    if key not in MODE_ALIASES:
        raise ValueError(
            f"Unknown mode '{raw}'. Choose from: read, write, full, bypass."
        )
    return MODE_ALIASES[key]


def should_be_ephemeral(
    sandbox_mode: str, force_ephemeral: bool, force_keep: bool
) -> bool:
    if force_ephemeral:
        return True
    if force_keep:
        return False
    return sandbox_mode == "read-only"


def read_prompt_input(
    prompt_text: Optional[str], prompt_file: Optional[str], allow_empty: bool = False
) -> str:
    chunks: List[str] = []
    if prompt_file:
        chunks.append(Path(prompt_file).read_text(encoding="utf-8"))
    if prompt_text:
        chunks.append(prompt_text)
    if not chunks and not sys.stdin.isatty():
        stdin_data = sys.stdin.read()
        if stdin_data.strip():
            chunks.append(stdin_data)
    combined = "\n\n".join(c.strip() for c in chunks if c and c.strip())
    if not combined and not allow_empty:
        raise ValueError(
            "No prompt provided. Pass --prompt '...', --prompt-file <path>, or pipe via stdin."
        )
    return combined


def make_run_dir(log_root: Optional[str] = None) -> Path:
    base = (
        Path(log_root).expanduser()
        if log_root
        else (Path(tempfile.gettempdir()) / "codex-subagent")
    )
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    short_id = uuid.uuid4().hex[:8]
    run_dir = base / f"{stamp}-{short_id}"
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def parse_jsonl_events(events_path: Path) -> Dict[str, Any]:
    stats: Dict[str, Any] = {
        "thread_id": None,
        "commands_total": 0,
        "commands_failed": 0,
        "failed_commands": [],
        "usage": {},
        "errors": [],
        "last_agent_message": None,
    }
    if not events_path.exists():
        return stats

    for raw_line in events_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line.startswith("{"):
            continue
        try:
            evt = json.loads(line)
        except json.JSONDecodeError:
            continue

        etype = evt.get("type")
        if etype == "thread.started" and evt.get("thread_id"):
            stats["thread_id"] = evt["thread_id"]
        elif etype == "item.completed":
            item = evt.get("item") or {}
            itype = item.get("type")
            if itype == "command_execution":
                stats["commands_total"] += 1
                exit_code = item.get("exit_code")
                if exit_code not in (0, None):
                    stats["commands_failed"] += 1
                    snippet = (item.get("aggregated_output") or "").strip()[-300:]
                    stats["failed_commands"].append(
                        f"cmd={item.get('command')!r} exit={exit_code} out={snippet!r}"
                    )
            elif itype == "agent_message" and item.get("text"):
                stats["last_agent_message"] = item["text"]
        elif etype == "turn.completed" and isinstance(evt.get("usage"), dict):
            stats["usage"] = evt["usage"]
        elif etype in ("turn.failed", "error"):
            err_obj = evt.get("error")
            if isinstance(err_obj, dict):
                stats["errors"].append(err_obj.get("message") or json.dumps(err_obj))
            elif evt.get("message"):
                stats["errors"].append(str(evt["message"]))
            else:
                stats["errors"].append(line)
    return stats


def execute_codex(
    cmd: List[str],
    stdin_payload: Optional[str],
    cwd: Optional[str],
    run_dir: Path,
    meta_summary: Dict[str, Any],
) -> int:
    events_file = run_dir / "events.jsonl"
    stderr_file = run_dir / "stderr.log"
    last_msg_file = run_dir / "last_message.md"
    meta_file = run_dir / "meta.json"

    start = time.monotonic()
    with events_file.open("w", encoding="utf-8") as f_out, stderr_file.open(
        "w", encoding="utf-8"
    ) as f_err:
        proc = subprocess.run(
            cmd,
            input=stdin_payload,
            stdout=f_out,
            stderr=f_err,
            cwd=cwd or None,
            text=True,
        )
    elapsed = round(time.monotonic() - start, 2)

    parsed = parse_jsonl_events(events_file)
    final_msg = ""
    if last_msg_file.exists():
        final_msg = last_msg_file.read_text(encoding="utf-8", errors="replace").strip()
    if not final_msg and parsed.get("last_agent_message"):
        final_msg = str(parsed["last_agent_message"]).strip()

    meta_record = {
        **meta_summary,
        "cmd": cmd,
        "exit_code": proc.returncode,
        "duration_sec": elapsed,
        "session_id": parsed.get("thread_id"),
        "commands_total": parsed["commands_total"],
        "commands_failed": parsed["commands_failed"],
        "usage": parsed["usage"],
        "errors": parsed["errors"],
    }
    meta_file.write_text(
        json.dumps(meta_record, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    session_display = (
        f"{parsed['thread_id']} (ephemeral - not resumable)"
        if meta_summary.get("ephemeral") and parsed.get("thread_id")
        else (parsed.get("thread_id") or "none")
    )
    usage = parsed.get("usage") or {}
    usage_str = (
        f"in={usage.get('input_tokens', 0)} "
        f"(cached={usage.get('cached_input_tokens', 0)}), "
        f"out={usage.get('output_tokens', 0)}, "
        f"reasoning={usage.get('reasoning_output_tokens', 0)}"
        if usage
        else "n/a"
    )

    print("=== CODEX SUBAGENT RESULT ===")
    print(f"exit_code: {proc.returncode}")
    print(
        f"action: {meta_summary.get('action')} | model: {meta_summary.get('model')} "
        f"| effort: {meta_summary.get('effort')} | mode: {meta_summary.get('mode')}"
    )
    print(f"session_id: {session_display}")
    print(
        f"duration_sec: {elapsed} | commands_run: {parsed['commands_total']} "
        f"(failed: {parsed['commands_failed']})"
    )
    print(f"codex_tokens: {usage_str}")
    print(f"trace_log: {events_file}")

    if parsed["errors"] or proc.returncode != 0:
        print("--- ERRORS / DIAGNOSTICS ---")
        for err in parsed["errors"]:
            print(f"error_event: {err}")
        if stderr_file.exists():
            err_tail = stderr_file.read_text(
                encoding="utf-8", errors="replace"
            ).strip()
            if err_tail:
                tail_lines = "\n".join(err_tail.splitlines()[-25:])
                print(f"stderr_tail:\n{tail_lines}")
        for fc in parsed["failed_commands"][-3:]:
            print(f"failed_cmd: {fc}")

    print("--- FINAL REPORT ---")
    print(final_msg if final_msg else "(No final agent message produced. Check trace_log.)")
    return proc.returncode


def cmd_models(_args: argparse.Namespace) -> int:
    codex_bin = find_codex_bin()
    print(f"codex_bin: {codex_bin}")
    cache_path = get_codex_home() / "models_cache.json"
    if not cache_path.exists():
        print(f"{cache_path} not found.")
        return 0
    data = json.loads(cache_path.read_text(encoding="utf-8"))
    print(f"client_version: {data.get('client_version')}")
    print(f"default_selected_model: {default_model_from_env_or_cache()}")
    print("available_models:")
    for m in data.get("models", []):
        slug = m.get("slug")
        efforts = [
            lvl.get("effort")
            for lvl in (m.get("supported_reasoning_levels") or [])
            if isinstance(lvl, dict) and lvl.get("effort")
        ]
        vis = m.get("visibility", "list")
        desc = m.get("description", "")
        print(f"  - {slug} [{vis}] (efforts: {','.join(efforts)}): {desc}")
    return 0


def cmd_doctor(_args: argparse.Namespace) -> int:
    print("=== CODEX SUBAGENT DOCTOR ===")
    print(f"python_version: {sys.version.split()[0]}")
    codex_home = get_codex_home()
    print(f"codex_home: {codex_home} (exists={codex_home.exists()})")
    try:
        codex_bin = find_codex_bin()
        print(f"codex_bin: {codex_bin}")
        ver = subprocess.run(
            [codex_bin, "--version"], capture_output=True, text=True, timeout=10
        )
        print(f"codex_version: {ver.stdout.strip() or ver.stderr.strip()}")
    except Exception as exc:
        print(f"codex_bin: MISSING ({exc})")
        return 1

    auth_exists = (codex_home / "auth.json").exists() or bool(
        os.environ.get("OPENAI_API_KEY")
    )
    print(f"auth_configured: {auth_exists}")
    print(f"resolved_default_model: {default_model_from_env_or_cache()}")
    print("status: READY" if auth_exists else "status: NEEDS_LOGIN (run `codex login`)")
    return 0 if auth_exists else 1


def add_common_exec_flags(p: argparse.ArgumentParser) -> None:
    p.add_argument("-m", "--model", default="default", help="Model slug or alias (sol, astra, terra, reserve, ...)")
    p.add_argument("-e", "--effort", default="auto", help="Reasoning effort: auto|low|medium|high|xhigh|max|ultra")
    p.add_argument("-C", "--cwd", default=None, help="Working directory root for Codex")
    p.add_argument("--prompt", default=None, help="Prompt string (or pipe via stdin)")
    p.add_argument("--prompt-file", default=None, help="Read prompt text from file")
    p.add_argument("--raw-prompt", action="store_true", help="Do not append the compact handoff format instructions")
    p.add_argument("--output-schema", default=None, help="Optional JSON Schema file path for structured final output")
    p.add_argument("--use-user-config", action="store_true", help="Load ~/.codex/config.toml instead of --ignore-user-config")
    p.add_argument("--worktree", action="store_true", help="Run inside an isolated managed Git worktree")
    p.add_argument("-c", "--config", action="append", default=[], help="Extra -c key=value config overrides")
    p.add_argument("--log-dir", default=None, help="Override base directory for trace logs")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Codex CLI subagent runner for Google Antigravity"
    )
    sub = parser.add_subparsers(dest="action", required=True)

    # run
    p_run = sub.add_parser("run", help="Start a new non-interactive Codex subagent task")
    add_common_exec_flags(p_run)
    p_run.add_argument(
        "-s", "--mode", default="read",
        help="Sandbox permission mode: read (read-only) | write (workspace-write) | full (danger-full-access) | bypass"
    )
    p_run.add_argument("--add-dir", action="append", default=[], help="Additional writable directory")
    p_run.add_argument("-i", "--image", action="append", default=[], help="Attach image file(s)")
    p_run.add_argument("--search", action="store_true", help="Enable Codex native web_search")
    sess_grp = p_run.add_mutually_exclusive_group()
    sess_grp.add_argument("--ephemeral", action="store_true", help="Force ephemeral session (no disk history)")
    sess_grp.add_argument("--keep-session", action="store_true", help="Force saving session_id even in read mode")

    # resume
    p_res = sub.add_parser("resume", help="Resume an existing Codex session by session_id (or --last)")
    add_common_exec_flags(p_res)
    p_res.add_argument("session_id", nargs="?", default=None, help="UUID session_id from previous run")
    p_res.add_argument("--last", action="store_true", help="Resume most recent session")
    p_res.add_argument("-s", "--mode", default="write", help="Mode hint for effort/bypass")
    p_res.add_argument("-i", "--image", action="append", default=[], help="Attach image file(s)")

    # fork
    p_fork = sub.add_parser("fork", help="Fork an existing Codex session into a new branch session")
    add_common_exec_flags(p_fork)
    p_fork.add_argument("session_id", help="UUID session_id to fork")
    p_fork.add_argument("-s", "--mode", default="write", help="Mode hint for effort/bypass")
    p_fork.add_argument("-i", "--image", action="append", default=[], help="Attach image file(s)")

    # review
    p_rev = sub.add_parser("review", help="Run non-interactive Codex code review on git changes")
    add_common_exec_flags(p_rev)
    p_rev.add_argument("--uncommitted", action="store_true", help="Review staged, unstaged, and untracked changes")
    p_rev.add_argument("--base", default=None, help="Review changes against base branch")
    p_rev.add_argument("--commit", default=None, help="Review changes introduced by commit SHA")
    p_rev.add_argument("--title", default=None, help="Optional commit/PR title")
    p_rev.add_argument("--ephemeral", action="store_true", default=True, help="Run review ephemerally (default: true)")
    p_rev.add_argument("--keep-session", action="store_true", help="Persist review session")

    # models & doctor
    sub.add_parser("models", help="List locally cached Codex models and binary path")
    sub.add_parser("doctor", help="Check Codex binary, login status, and environment health")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    if raw_argv and raw_argv[0] not in SUBCOMMANDS and raw_argv[0] not in ("-h", "--help"):
        raw_argv = ["run"] + raw_argv

    parser = build_parser()
    args = parser.parse_args(raw_argv)

    if args.action == "models":
        return cmd_models(args)
    if args.action == "doctor":
        return cmd_doctor(args)

    codex_bin = find_codex_bin()
    run_dir = make_run_dir(getattr(args, "log_dir", None))
    last_msg_path = str(run_dir / "last_message.md")

    mode_resolved = resolve_mode(getattr(args, "mode", "read")) if args.action != "review" else "read-only"
    model_resolved = resolve_model(args.model)
    effort_resolved = resolve_effort(args.effort, mode_resolved)

    allow_empty_prompt = args.action == "review" and bool(
        args.uncommitted or args.base or args.commit
    )
    user_prompt = read_prompt_input(
        args.prompt, args.prompt_file, allow_empty=allow_empty_prompt
    )
    if user_prompt and not args.raw_prompt and not args.output_schema and args.action != "review":
        final_prompt = f"{user_prompt}\n\n{HANDOFF_CONTRACT}"
    else:
        final_prompt = user_prompt

    cmd: List[str] = [codex_bin]
    if getattr(args, "search", False):
        cmd.append("--search")
    cmd.append("exec")

    if args.cwd and args.action == "run":
        cmd.extend(["-C", args.cwd])

    if args.action == "run":
        ephemeral = should_be_ephemeral(
            mode_resolved, bool(args.ephemeral), bool(args.keep_session)
        )
        cmd.extend(["-m", model_resolved, "-c", f'model_reasoning_effort="{effort_resolved}"'])
        for extra_cfg in args.config:
            cmd.extend(["-c", extra_cfg])
        if not args.use_user_config:
            cmd.append("--ignore-user-config")
        cmd.append("--skip-git-repo-check")
        if ephemeral:
            cmd.append("--ephemeral")
        if args.worktree:
            cmd.append("--worktree")
        if mode_resolved == "bypass-all":
            cmd.append("--dangerously-bypass-approvals-and-sandbox")
        else:
            cmd.extend(["-s", mode_resolved])
        for d in args.add_dir:
            cmd.extend(["--add-dir", d])
        for img in args.image:
            cmd.extend(["-i", img])
        if args.output_schema:
            cmd.extend(["--output-schema", args.output_schema])
        cmd.extend(["--json", "-o", last_msg_path, "-"])

    elif args.action == "resume":
        if not args.session_id and not args.last:
            raise ValueError("`resume` requires either a <session_id> or --last.")
        ephemeral = False
        cmd.append("resume")
        cmd.extend(["-m", model_resolved, "-c", f'model_reasoning_effort="{effort_resolved}"'])
        for extra_cfg in args.config:
            cmd.extend(["-c", extra_cfg])
        if not args.use_user_config:
            cmd.append("--ignore-user-config")
        cmd.append("--skip-git-repo-check")
        if args.worktree:
            cmd.append("--worktree")
        if mode_resolved == "bypass-all":
            cmd.append("--dangerously-bypass-approvals-and-sandbox")
        for img in args.image:
            cmd.extend(["-i", img])
        if args.output_schema:
            cmd.extend(["--output-schema", args.output_schema])
        cmd.extend(["--json", "-o", last_msg_path])
        if args.last:
            cmd.append("--last")
        elif args.session_id:
            cmd.append(args.session_id)
        cmd.append("-")

    elif args.action == "fork":
        ephemeral = False
        cmd.append("fork")
        cmd.extend(["-m", model_resolved, "-c", f'model_reasoning_effort="{effort_resolved}"'])
        for extra_cfg in args.config:
            cmd.extend(["-c", extra_cfg])
        if not args.use_user_config:
            cmd.append("--ignore-user-config")
        cmd.append("--skip-git-repo-check")
        if args.worktree:
            cmd.append("--worktree")
        if mode_resolved == "bypass-all":
            cmd.append("--dangerously-bypass-approvals-and-sandbox")
        for img in args.image:
            cmd.extend(["-i", img])
        if args.output_schema:
            cmd.extend(["--output-schema", args.output_schema])
        cmd.extend(["--json", "-o", last_msg_path, args.session_id, "-"])

    elif args.action == "review":
        ephemeral = not bool(args.keep_session)
        cmd.append("review")
        cmd.extend(["-m", model_resolved, "-c", f'model_reasoning_effort="{effort_resolved}"'])
        for extra_cfg in args.config:
            cmd.extend(["-c", extra_cfg])
        if not args.use_user_config:
            cmd.append("--ignore-user-config")
        cmd.append("--skip-git-repo-check")
        if ephemeral:
            cmd.append("--ephemeral")
        if args.worktree:
            cmd.append("--worktree")
        if args.uncommitted:
            cmd.append("--uncommitted")
        if args.base:
            cmd.extend(["--base", args.base])
        if args.commit:
            cmd.extend(["--commit", args.commit])
        if args.title:
            cmd.extend(["--title", args.title])
        if args.output_schema:
            cmd.extend(["--output-schema", args.output_schema])
        cmd.extend(["--json", "-o", last_msg_path])
        if final_prompt:
            cmd.append("-")

    meta_summary = {
        "action": args.action,
        "model": model_resolved,
        "effort": effort_resolved,
        "mode": mode_resolved,
        "ephemeral": ephemeral,
        "cwd": args.cwd or os.getcwd(),
    }
    stdin_payload = final_prompt if final_prompt else None
    return execute_codex(cmd, stdin_payload, args.cwd, run_dir, meta_summary)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"[codex_subagent error] {exc}", file=sys.stderr)
        sys.exit(2)
