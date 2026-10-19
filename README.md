# ExperimentalGUI

The original CST application with PID, GA and BO control tabs. This layout refactor keeps the existing Python backend, ZMQ transport, multiprocessing entry point and controller algorithms. The root launcher only configures paths and runs `python/app/main.py`.

## Windows PowerShell

From this folder:

```powershell
.\scripts\install.ps1
.\scripts\run.ps1
```

The installer creates `.venv`, activates it, installs the full `requirements.txt` including BO, checks dependencies and checks application imports. The run script activates that environment and starts the whole original GUI. Both scripts work when invoked using their full paths from another directory.

Use **64-bit Python 3.11, 3.12 or 3.13**. To select an installed interpreter explicitly:

```powershell
.\scripts\install.ps1 -PythonExecutable 'C:\path\to\python.exe'
```

If PowerShell blocks local scripts, allow them for the current terminal session, then rerun:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
```

To activate the environment for your own commands:

```powershell
. .\.venv\Scripts\Activate.ps1
python main.py --check
deactivate
```

## Ubuntu / WSL

On a desktop Ubuntu installation, or WSL with WSLg:

```bash
bash scripts/install_ubuntu.sh --system-deps
bash scripts/run_ubuntu.sh
```

`--system-deps` uses `sudo apt-get` to install the Qt/Linux runtime packages and Python venv support. Omit it if those packages are already available. Ubuntu 24.04's Python 3.12 is suitable; on other releases, install a supported Python with its matching venv package. Select another interpreter with:

```bash
PYTHON=python3.12 bash scripts/install_ubuntu.sh --system-deps
```

Linux uses **`.venv-ubuntu`**, so Windows and WSL can share the source folder without trying to reuse each other's binaries. A desktop display/WSLg is required to open the GUI; a terminal-only server can run `--check`.

```bash
source .venv-ubuntu/bin/activate
python main.py --check
deactivate
```

Use `VENV_PATH=/absolute/path` with both Linux scripts to choose another environment. Windows scripts accept `-VenvPath`. Installers stop rather than overwrite an incompatible or incomplete environment.

## Folder layout

```text
main.py                        Portable launcher
requirements.txt               Full desktop + BO dependencies
requirement.txt                Compatibility alias for the singular spelling
requirements-optimization.txt  BO dependencies, included by requirements.txt
requirements-web.txt           Optional historical web viewer dependencies
scripts/                       Windows/Ubuntu install, run and test helpers
python/application/            Startup and central path resolution
python/app/                    Existing application modules and BO package
python/tools/                  Optional web viewer
assets/images/                 Images and backgrounds
assets/icons/                  UI icons
assets/videos/                 Existing videos
assets/web/                    Browser viewer assets
config/                        Existing editable settings and calibration files
runtime/                       Databases, snapshots, logs, GA and BO experiments
tests/                         Maintained layout and BO checks
docs/                          BO guide, preview and migration record
archive/                       Previous copies, update bundles, LabVIEW and legacy files
```

Old copies and data were moved, not deleted. `docs/layout_migration.json` records their destinations and file hashes. The older calibration workbooks and reports are in `archive/reference_data`; the active calibration workbook is in `config`.

## Paths and logging

Application paths are resolved from the project, independent of the terminal's current directory. New runtime files stay under `runtime/`:

- Main logger and record viewer: `runtime/data/cyclotron_data.db`.
- Snapshots: `runtime/snapshots/`.
- GA / BO trials: `runtime/ga_runs/` and `runtime/bo_runs/`.
- PID decision and diagnostic logs: `runtime/pid_decision_logs/`, `runtime/pid_logs/`, `runtime/logs/`.

Existing environment overrides (`CNL_DB_PATH`, `CNL_SQLITE`, `CNL_CALIB`, `CNL_BEAM_CAL`, `CNL_APP_SETTINGS`, `CNL_ALARM_CFG`) remain supported. Relative overrides are resolved before the launcher changes to the runtime directory. The transport and LabVIEW connection behavior are unchanged. LabVIEW itself remains an external application and is not installed by these scripts.

## Checks

```powershell
.\scripts\run.ps1 -Check
.\.venv\Scripts\python.exe scripts\run_tests.py --ui
```

```bash
bash scripts/run_ubuntu.sh --check
.venv-ubuntu/bin/python scripts/run_tests.py --ui
```

Omit `--ui` for hardware-free checks that do not need Qt. UI tests use fake feedback/proposals and do not connect hardware. `--check` validates dependency and application imports without starting backend processes or opening the GUI.

Dependencies are constrained ranges, not a platform-specific lockfile. After installing on a target machine, capture its exact environment with `python -m pip freeze > requirements-lock.txt` if repeatable deployments are needed. PyTorch is a large download; the installer uses the standard pip distribution and does not require CUDA.

See [the BO guide](docs/BO.md) for optimization controls and experiment records. Archived scripts are reference material and are not launched by the new entry point.

The optional existing browser viewer can be started separately after installing `requirements-web.txt`:

```text
python scripts/run_web.py --host 127.0.0.1 --port 8000
```
