#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILLS_DIR="${REPO_ROOT}/skills"
SKILL_SRC="${SKILLS_DIR}/codex-subagent"
RUNNER="${SKILL_SRC}/scripts/codex_subagent.py"

AGY_CONFIG_DIR="${GEMINI_CONFIG_DIR:-${HOME}/.gemini/config}"
AGY_SKILLS_DIR="${AGY_CONFIG_DIR}/skills"
AGY_SKILLS_JSON="${AGY_CONFIG_DIR}/skills.json"
SYMLINK_PATH="${AGY_SKILLS_DIR}/codex-subagent"

chmod +x "${RUNNER}"

if [[ "${1:-}" == "--uninstall" || "${1:-}" == "uninstall" ]]; then
  echo "Uninstalling codex-subagent from ${AGY_CONFIG_DIR} ..."
  if [[ -L "${SYMLINK_PATH}" ]]; then
    rm -f "${SYMLINK_PATH}"
    echo "  Removed symlink: ${SYMLINK_PATH}"
  fi
  if [[ -f "${AGY_SKILLS_JSON}" ]]; then
    python3 - "${AGY_SKILLS_JSON}" "${SKILLS_DIR}" <<'PY'
import json, sys
from pathlib import Path

cfg_path = Path(sys.argv[1])
target = sys.argv[2]
try:
    data = json.loads(cfg_path.read_text(encoding="utf-8"))
except Exception:
    sys.exit(0)

entries = data.get("entries")
if isinstance(entries, list):
    data["entries"] = [
        e for e in entries
        if not (isinstance(e, dict) and e.get("path") == target)
    ]
    cfg_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"  Removed {target} from {cfg_path}")
PY
  fi
  echo "Uninstall complete."
  exit 0
fi

echo "Installing codex-subagent globally into ${AGY_CONFIG_DIR} ..."
mkdir -p "${AGY_SKILLS_DIR}"
ln -sfn "${SKILL_SRC}" "${SYMLINK_PATH}"
echo "  Linked: ${SYMLINK_PATH} -> ${SKILL_SRC}"

python3 - "${AGY_SKILLS_JSON}" "${SKILLS_DIR}" <<'PY'
import json, sys
from pathlib import Path

cfg_path = Path(sys.argv[1])
target = sys.argv[2]

if cfg_path.exists() and cfg_path.stat().st_size > 0:
    data = json.loads(cfg_path.read_text(encoding="utf-8"))
else:
    data = {}

entries = data.setdefault("entries", [])
if not any(isinstance(e, dict) and e.get("path") == target for e in entries):
    entries.append({"path": target})
    cfg_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"  Registered {target} in {cfg_path}")
else:
    print(f"  Already registered in {cfg_path}")
PY

echo ""
python3 "${RUNNER}" doctor || true
echo ""
echo "Done! Restart or open any workspace in Google Antigravity and mention 'codex' (or e.g. 'sol' / 'astra') to delegate tasks."
