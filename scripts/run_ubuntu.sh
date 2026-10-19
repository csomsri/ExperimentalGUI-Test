#!/usr/bin/env bash
set -euo pipefail
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
venv_root="${VENV_PATH:-$project_root/.venv-ubuntu}"
[[ "$venv_root" = /* ]] || venv_root="$project_root/$venv_root"
if [[ ! -x "$venv_root/bin/python" ]]; then
    printf '%s\n' 'Ubuntu environment missing. Run bash scripts/install_ubuntu.sh first.' >&2
    exit 1
fi
source "$venv_root/bin/activate"
exec "$venv_root/bin/python" "$project_root/main.py" "$@"
