# Codex CLI Native Reference (Antigravity Integration)

Verified against `codex-cli 0.169.0+` (standalone CLI & macOS ChatGPT Desktop bundle).

## 1. Binary Discovery & Config Isolation

- **Auto-discovered binary locations** (checked in order by `codex_subagent.py`):
  1. `$CODEX_BIN` environment variable
  2. `codex` on system `$PATH` (`npm i -g @openai/codex`, Homebrew, etc.)
  3. `~/.local/bin/codex`, `/opt/homebrew/bin/codex`, `/usr/local/bin/codex`, `~/.npm-global/bin/codex`, `~/.bun/bin/codex`
  4. `/Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex` (macOS ChatGPT Desktop app bundle)
- **Auth & Cache Directory (`CODEX_HOME`, default `~/.codex/`)**:
  - `auth.json`: Reused automatically even when `--ignore-user-config` is set (or via `OPENAI_API_KEY`).
  - `models_cache.json`: Live list of models and reasoning levels supported by the signed-in account.
  - `config.toml`: Desktop configuration (often loads heavy `computer-use`, `browser`, `node_repl` MCP servers and turn-end notifications — **always pass `--ignore-user-config` in headless subagent runs** unless the user specifically asks to load their Codex MCP servers).

Inspect live environment and models:
```bash
python3 "<SKILL_DIR>/scripts/codex_subagent.py" doctor
python3 "<SKILL_DIR>/scripts/codex_subagent.py" models
```

---

## 2. Model & Reasoning Effort Catalog

### Model Families & Aliases
| Slug | Alias(es) | Recommended Effort | Supported Efforts | Best Use Case |
| :--- | :--- | :--- | :--- | :--- |
| `gpt-6.2-sol` | `sol`, `6.2-sol` | `medium` (read) / `high` (write) | `low`, `medium`, `high`, `xhigh`, `max`, `ultra` | Fast go-to primary model for coding, edits, and analysis |
| `gpt-6.2-astra` | `astra`, `6.2-astra` | `high` / `xhigh` | `low`, `medium`, `high`, `xhigh`, `max`, `ultra` | Flagship model for hard architecture, subtle bugs, complex refactors |
| `gpt-6.1-terra` | `terra`, `6.1-terra` | `medium` / `high` | `low`, `medium`, `high`, `xhigh`, `max`, `ultra` | Efficient multimodal / visual inspection (`-i screenshot.png`) |
| `gpt-reserve` | `reserve`, `mini`, `fast` | `low` / `medium` | `low`, `medium`, `high`, `xhigh`, `max` | Fast, lightweight coding/triage tasks |
| `gpt-6.1-sol` | `6.1-sol` | `high` | `low`..`ultra` | Previous-gen daily coding model |
| `gpt-6-astra` | `6-astra` | `high` | `low`..`ultra` | Previous-gen complex coding model |
| `gpt-6-sol` / `gpt-6-luna` | `6-sol`, `luna` | `medium` | `low`..`ultra` (`max` on luna) | Legacy 6.0 series |
| `gpt-5.6-sol` / `terra` / `luna` | `5.6-sol`, etc. | `medium` | `low`..`ultra` (`max` on luna) | Legacy 5.6 series |

> Note: `gpt-reserve` and `*-luna` support up to `max` (not `ultra`). Cap effort at `max` for those models.

### Reasoning Effort Config Flag
Codex selects reasoning effort via `-c`:
```bash
-c model_reasoning_effort="high"   # low | medium | high | xhigh | max | ultra
```

---

## 3. Native Non-Interactive Commands (`codex exec`)

When invoking `codex` directly without `codex_subagent.py`, always feed multi-line prompts via `stdin` using trailing `-` so shell quoting never breaks and `codex` never blocks on open pipe descriptors.

### A. One-Off Read-Only Analysis (Ephemeral)
```bash
CODEX="${CODEX_BIN:-$(command -v codex || echo /Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex)}"
"$CODEX" exec \
  --ignore-user-config \
  --ephemeral \
  --skip-git-repo-check \
  -C "/path/to/repo" \
  -s read-only \
  -m gpt-6.2-sol \
  -c model_reasoning_effort="medium" \
  -o /tmp/codex_last.md \
  - <<'EOF' > /tmp/codex_trace.log 2>&1
Analyze src/auth/ and summarize the token refresh flow in <30 lines.
EOF
cat /tmp/codex_last.md
```

### B. Workspace-Write Coding Task (Resumable Session)
```bash
CODEX="${CODEX_BIN:-$(command -v codex || echo /Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex)}"
"$CODEX" exec \
  --ignore-user-config \
  --skip-git-repo-check \
  -C "/path/to/repo" \
  -s workspace-write \
  -m gpt-6.2-sol \
  -c model_reasoning_effort="high" \
  --json \
  -o /tmp/codex_last.md \
  - <<'EOF' > /tmp/codex_events.jsonl 2>/tmp/codex_stderr.log
Implement pagination in src/api/users.ts and run unit tests. Keep final report concise.
EOF
grep -m1 '"thread.started"' /tmp/codex_events.jsonl
cat /tmp/codex_last.md
```

### C. Multi-Turn Follow-Up (`codex exec resume` / `fork`)
```bash
"$CODEX" exec resume \
  --ignore-user-config \
  --skip-git-repo-check \
  -m gpt-6.2-sol \
  -c model_reasoning_effort="high" \
  --json \
  -o /tmp/codex_resume_last.md \
  "<SESSION_UUID>" \
  - <<'EOF' > /tmp/codex_resume.jsonl 2>&1
Fix the failing edge case when cursor is empty and re-run tests.
EOF
```
Use `codex exec fork "<SESSION_UUID>" -` with the same flags to branch into a new session without mutating the original thread history.

### D. Dedicated Git Code Review (`codex exec review`)
Note: Pass `--uncommitted`, `--base <branch>`, or `--commit <sha>` without a custom prompt argument (or use `codex_subagent.py run --mode read` for custom review rubrics).
```bash
"$CODEX" exec review \
  --ignore-user-config \
  --ephemeral \
  -m gpt-6.2-astra \
  -c model_reasoning_effort="high" \
  --uncommitted \
  -o /tmp/codex_review.md > /tmp/codex_review.log 2>&1
```

---

## 4. Structured JSON Schema Output (`--output-schema`)

When aggregating results from parallel Codex subagents, pass `--output-schema <path>` (for example, `examples/handoff-schema.json` at the repo root) so Codex's final message conforms strictly to JSON.

---

## 5. Parallel Subagents & Worktree Patterns

1. **Parallel Read-Only Analysis**:
   Launch 2–4 concurrent `run_command` calls using `codex_subagent.py run --mode read` on separate directories/subsystems.
2. **Parallel Code Editing**:
   - **Disjoint files/modules**: Assign strict file ownership boundaries in each subagent's prompt (`"ONLY modify files under packages/auth/"`).
   - **Overlapping or risky refactors**: Pass `--worktree` (requires a Git repository) so Codex executes inside an isolated managed Git worktree.

---

## 6. JSONL Event Stream Reference (`--json`)

Key events emitted by `codex exec --json`:
- `{"type":"thread.started","thread_id":"<UUID>"}`: The `session_id` used for `resume` / `fork`.
- `{"type":"item.completed","item":{"type":"command_execution","command":"...","exit_code":0,"aggregated_output":"..."}}`: Shell execution record.
- `{"type":"item.completed","item":{"type":"agent_message","text":"..."}}`: Assistant message text.
- `{"type":"turn.completed","usage":{"input_tokens":...,"cached_input_tokens":...,"output_tokens":...,"reasoning_output_tokens":...}}`: Token consumption summary.
- `{"type":"turn.failed","error":{"message":"..."}}`: Turn failure details.

---

## 7. Troubleshooting

- **Sandbox blocks package cache (`~/.npm`, `~/.cargo`, `~/.cache/uv`) or localhost/network tests**:
  Re-run or `resume` with `--mode full` (`-s danger-full-access`) or `--add-dir <cache_dir>`.
- **`gpt-reserve` or `*-luna` rejects `ultra` effort**:
  Downgrade `-e` to `max` or `xhigh`.
- **Slow startup (30–120s) or desktop popups**:
  Ensure `--ignore-user-config` is active (default in `codex_subagent.py`).
- **Command runs longer than `run_command` sync wait**:
  Set `WaitMsBeforeAsync: 10000` and `NotificationTimeoutSeconds: 300` on `run_command`; wait passively for completion notification instead of polling.
