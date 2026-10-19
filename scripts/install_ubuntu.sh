#!/usr/bin/env bash
set -euo pipefail
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
python_command="${PYTHON:-python3}"
venv_root="${VENV_PATH:-$project_root/.venv-ubuntu}"
[[ "$venv_root" = /* ]] || venv_root="$project_root/$venv_root"
system_deps=0
while (($#)); do
    case "$1" in
        --system-deps) system_deps=1 ;;
        --help|-h) printf '%s\n' 'Usage: bash scripts/install_ubuntu.sh [--system-deps]' 'PYTHON selects Python 3.11-3.13; VENV_PATH selects the environment.'; exit 0 ;;
        *) printf 'Unknown option: %s\n' "$1" >&2; exit 2 ;;
    esac
    shift
done
if ((system_deps)); then
    sudo apt-get update
    alsa_package=libasound2
    if apt-cache show libasound2t64 >/dev/null 2>&1; then alsa_package=libasound2t64; fi
    sudo apt-get install -y python3-venv python3-pip libgl1 libegl1 \
        libxkbcommon-x11-0 libxcb-cursor0 libxcb-icccm4 libxcb-keysyms1 \
        libxcb-shape0 libxcb-xinerama0 libxcb-randr0 libxcb-render-util0 \
        libxcb-image0 libxcb-xkb1 libx11-xcb1 libdbus-1-3 "$alsa_package"
fi
command -v "$python_command" >/dev/null || { printf 'Python not found: %s\n' "$python_command" >&2; exit 1; }
"$python_command" -c 'import sys,struct; assert (3,11) <= sys.version_info[:2] < (3,14) and struct.calcsize("P") == 8, "Use 64-bit Python 3.11-3.13"'
if [[ ! -x "$venv_root/bin/python" ]]; then
    if [[ -e "$venv_root" ]]; then
        printf '%s\n' 'Existing environment is incomplete or belongs to Windows. Choose another VENV_PATH; it will not be overwritten.' >&2
        exit 1
    fi
    "$python_command" -m venv "$venv_root"
fi
source "$venv_root/bin/activate"
"$venv_root/bin/python" -c 'import sys,struct; assert (3,11) <= sys.version_info[:2] < (3,14) and struct.calcsize("P") == 8, "Use 64-bit Python 3.11-3.13"'
"$venv_root/bin/python" -m pip install --upgrade pip
"$venv_root/bin/python" -m pip install -r "$project_root/requirements.txt"
"$venv_root/bin/python" -m pip check
"$venv_root/bin/python" "$project_root/main.py" --check
printf '%s\n' 'Installation complete. Start with bash scripts/run_ubuntu.sh.' \
    "To activate in your current shell: source \"$venv_root/bin/activate\""
