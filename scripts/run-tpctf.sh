#!/usr/bin/env bash
set -euo pipefail
PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${TPCTF_ENV_FILE:-/root/tpctf.env}"
if [[ ! -f "$ENV_FILE" ]]; then
  echo "Missing private configuration: set TPCTF_ENV_FILE to its path." >&2
  exit 2
fi
if [[ "$(stat -c %a "$ENV_FILE")" != "600" ]]; then
  echo "Private configuration must have mode 600: chmod 600 the file." >&2
  exit 2
fi
set -a
# This is the operator's trusted shell environment file; never a downloaded file.
source "$ENV_FILE"
set +a
cd -- "$PROJECT_DIR"
exec "$PROJECT_DIR/.venv/bin/python" -m backend.tpctf "$@"
