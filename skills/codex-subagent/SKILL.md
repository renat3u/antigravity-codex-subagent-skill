---
name: codex-subagent
description: >-
  Delegate coding, refactoring, debugging, file analysis, or code review tasks to the local OpenAI Codex CLI (`codex exec`) as a quota-saving subagent while Gemini acts as the high-level orchestrator. Activate ONLY when the user explicitly mentions "codex" or a Codex/GPT model name or alias (e.g., `gpt-6.2-sol`, `sol`, `gpt-6.2-astra`, `astra`, `gpt-6.1-terra`, `terra`, `gpt-reserve`, `gpt-6.1-sol`, `gpt-6-*`, `gpt-5.6-*`). Do NOT activate or suggest Codex when the user does not mention Codex or a GPT model.
---

# Codex Subagent Skill (Gemini Orchestrator -> Codex Executor)

Use this skill to run OpenAI Codex CLI (`codex exec`) as a headless subagent for writing code, analyzing files, debugging, or reviewing diffs while keeping Gemini's token usage minimal.

> **Path Convention**: Let `<SKILL_DIR>` be the directory containing this `SKILL.md` file (shown in Antigravity's `<skills>` listing). The helper script is located at `[codex_subagent.py](./scripts/codex_subagent.py)` (`"<SKILL_DIR>/scripts/codex_subagent.py"`).

---

## 1. Strict Activation Policy

- **Explicit Trigger Only**: Use Codex **only** when the user explicitly asks for `codex` or names a Codex/GPT model or alias (`sol`, `astra`, `terra`, `reserve`, `gpt-6.2-sol`, etc.).
- **Zero Nagging**: When the user does not mention Codex or a GPT model, work normally with Gemini's native tools/subagents—never ask whether to use Codex.

---

## 2. Gemini-as-Brain Quota-Saving Workflow

To save Gemini's context and quota, treat Gemini strictly as the **planner & dispatcher**:

1. **Do NOT pre-read large files**: At most run a lightweight `ls` / file tree check to confirm paths. Pass target file/directory paths, task goals, constraints, and verification commands to Codex—let Codex read files, search code, edit, and run tests itself.
2. **Use the wrapper script (`codex_subagent.py`)**: It automatically locates `codex` (PATH or macOS `ChatGPT.app`), hides noisy JSONL tool traces in a temp log directory, isolates heavy desktop plugins (`--ignore-user-config`), enforces a concise return template, and prints **only** metadata (`session_id`, exit code, command stats) + the compact final summary to `stdout`.
3. **Summary-First Verification**:
   - **If Codex reports `DONE` and tests pass**: Run at most `git status --short` / `git diff --stat` to confirm disk state, then synthesize and report directly to the user. **Do NOT read full changed source files or full `git diff` into Gemini's context.**
   - **If Codex fails, tests fail, or the user explicitly requests a review**: Inspect the specific diff/error or `trace_log`, and use `resume <session_id>` to instruct the *same* Codex session to fix the issue without re-reading the codebase.

---

## 3. Model & Reasoning Effort Selection

### Model Mapping (Default: `gpt-6.2-sol` or first model in `~/.codex/models_cache.json`)
| User Mentions / Shorthand | Resolved `--model` | Best For |
| :--- | :--- | :--- |
| Unspecified ("用 codex…"), `sol`, `6.2-sol` | `gpt-6.2-sol` | **Default**: Daily coding, refactoring, multi-file analysis |
| `astra`, `6.2-astra`, `gpt-6.2-astra` | `gpt-6.2-astra` | Complex architecture, hard debugging, high-stakes review |
| `terra`, `6.1-terra` | `gpt-6.1-terra` | Multimodal / screenshot UI inspection (`-i <img_path>`) |
| `reserve`, `mini` | `gpt-reserve` | Quick, low-cost edits or simple file scans (max effort: `max`) |
| `6.1-sol`, `6-astra`, `6-sol`, `luna`, `5.6-*` | Exact legacy slug | Only when explicitly requested by the user |

If the user names an unfamiliar model or environment health is uncertain, run `python3 "<SKILL_DIR>/scripts/codex_subagent.py" models` (or `doctor`).

### Reasoning Effort Mapping (`-e / --effort`)
Valid levels: `low` | `medium` | `high` | `xhigh` | `max` | `ultra` (note: `gpt-reserve` and `*-luna` cap at `max`).
- **When user specifies effort**: Map directly (`低`->`low`, `中`->`medium`, `高`->`high`, `超高/极高`->`xhigh`, `最大`->`max`, `拉满/满血/ultra/深度思考`->`ultra`).
- **When user omits effort** (auto-grade by task complexity—never default to `max`/`ultra` unless asked):
  - Simple file reading / Q&A -> `medium`
  - Standard feature coding / bugfix / unit tests -> `high`
  - Multi-module refactor / subtle concurrency or architecture bug -> `xhigh`

---

## 4. Sandbox & Session Persistence Rules

| Task Type | `--mode` (`-s`) | Session Persistence | Notes |
| :--- | :--- | :--- | :--- |
| File analysis / Q&A / Review | `read` (`read-only`) | `--ephemeral` (auto) | Leaves desktop Codex history clean |
| Coding / Refactoring / Local tests | `write` (`workspace-write`) | Keeps `session_id` (auto) | Supports multi-turn `resume <session_id>` |
| Blocked by sandbox (npm/cargo/uv cache, network, external dirs) | `full` (`danger-full-access`) | Keeps `session_id` | Escalate **only** when `write` is blocked or task clearly requires network/global cache |

---

## 5. Standard Invocation Commands

Always run via `run_command` with `WaitMsBeforeAsync: 10000` and `NotificationTimeoutSeconds: 300` (do not poll background tasks; wait passively for completion notification). Pass prompts via heredoc (`<<'EOF'`) to avoid shell-quoting issues.

### A. Write / Edit Code (`--mode write`, retains `session_id`)
```bash
python3 "<SKILL_DIR>/scripts/codex_subagent.py" run \
  -m sol -e high --mode write \
  -C "/absolute/path/to/workspace" <<'EOF'
Task: <clear objective>
Target files/dirs: <paths>
Constraints: <style, APIs, boundaries>
Verification: <exact test/build command to run before finishing>
EOF
```

### B. Analyze Files / Architecture (`--mode read`, ephemeral)
```bash
python3 "<SKILL_DIR>/scripts/codex_subagent.py" run \
  -m sol -e medium --mode read \
  -C "/absolute/path/to/workspace" <<'EOF'
Analyze <files/dirs> and explain <specific question>. Cite file paths and line numbers.
EOF
```

### C. Follow-Up / Rework in Same Codex Session (`resume`)
```bash
python3 "<SKILL_DIR>/scripts/codex_subagent.py" resume \
  "<SESSION_ID>" -m sol -e high <<'EOF'
Follow-up adjustment: <what to fix or refine, plus verification command>
EOF
```

### D. Dedicated Git Diff Review (`review`)
```bash
python3 "<SKILL_DIR>/scripts/codex_subagent.py" review \
  -m astra -e high --uncommitted -C "/absolute/path/to/workspace"
```
*(Or replace `--uncommitted` with `--base main` or `--commit <SHA>`.)*

### E. Parallel Dispatch
- **Parallel read/analysis**: Launch multiple `run_command` calls (`--mode read`) concurrently across different folders/questions.
- **Parallel edits**: Only run concurrent `--mode write` tasks if file ownership is strictly disjoint (state allowed paths in each prompt), or add `--worktree` inside Git repositories to isolate branches.

---

## 6. Advanced Native CLI Reference

For raw `codex exec` / `fork` / `--output-schema` / `--worktree` syntax, JSONL event schemas, or troubleshooting, read [cli-reference.md](./references/cli-reference.md).
