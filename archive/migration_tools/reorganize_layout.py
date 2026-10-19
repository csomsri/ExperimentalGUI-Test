"""One-time layout migration. All moves are confined to ExperimentalGUI."""
import ast
import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if not (ROOT / 'main_control_gui.py').is_file():
    raise SystemExit('Historical one-time migration tool; this checkout is already organized.')
ACTIVE = set('BeamTransportMonitoringWindow MagneticFieldControllerWindow MagneticFieldMonitoringWindow ScaleCalWindow SettingsDialog SmallScreenMagneticBeam SourceExtractionMonitoringWindow VacuumBeamMonitoringWindow alarm app_settings beam_cal beam_range beam_trace controller_context controller_theme ga_pid_tuner ga_recovery_manager ga_test_runner main main_control_gui manual_control_tab multi_tc_sequence_tab neon_style pid_beam_buffer pid_controller pid_decision_log pid_ga_control_tab scaling sequence_control_tab snapshot_recall_qt snapshot_save_qt sqlite_logger sqlite_viewer_qt sqlite_viewer_report sqlite_viewer_scaling zmq_rep_server pid_sqlite_logger pid_axis_trend_plot'.split())
MOVES = []


def confined(path):
    resolved = path.resolve()
    if resolved == ROOT or ROOT not in resolved.parents:
        raise RuntimeError(f'Move outside workspace refused: {resolved}')
    return resolved


def move(source, relative):
    source, destination = confined(source), confined(ROOT / relative)
    if destination.exists():
        raise RuntimeError(f'Destination already exists: {destination}')
    destination.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(source.read_bytes()).hexdigest() if source.is_file() else None
    shutil.move(str(source), str(destination))
    MOVES.append(dict(source=str(source.relative_to(ROOT)), destination=str(destination.relative_to(ROOT)), sha256=digest))


for entry in list(ROOT.iterdir()):
    name = entry.name
    if name.startswith('.') or name in ('__pycache__', 'reorganize_layout.py'):
        continue
    if entry.is_dir():
        if name == 'BO':
            continue
        relative = ({'icons': 'assets/icons', 'static': 'assets/web/static',
                     'snapshots': 'runtime/snapshots', 'ga_runs': 'runtime/ga_runs',
                     'bo_runs': 'runtime/bo_runs', 'pid_logs': 'runtime/pid_logs',
                     'pid_decision_logs': 'runtime/pid_decision_logs'}.get(name,
                        'archive/previous_versions/' + name))
    elif entry.suffix == '.py':
        relative = ('python/app/' + name if entry.stem in ACTIVE else
                    'python/tools/' + name if name == 'web_app.py' else
                    'archive/legacy_scripts/' + name)
    elif entry.suffix.lower() in ('.png', '.jpg', '.jpeg'):
        relative = 'assets/images/' + name
    elif entry.suffix.lower() == '.mp4':
        relative = 'assets/videos/' + name
    elif entry.suffix.lower() == '.json' or name == 'calibration.xlsx':
        relative = 'config/' + name
    elif name.endswith(('.db', '.db-wal', '.db-shm')):
        relative = 'runtime/data/' + name
    elif name == 'trimcoil_snapshots.xlsx':
        relative = 'runtime/snapshots/' + name
    elif entry.suffix.lower() == '.log':
        relative = 'runtime/logs/' + name
    elif entry.suffix.lower() in ('.vi', '.lvlib', '.lvproj', '.lvlps', '.aliases'):
        relative = 'archive/labview/' + name
    elif entry.suffix.lower() in ('.md', '.txt'):
        relative = 'docs/legacy/' + name
    elif entry.suffix.lower() in ('.xlsx', '.pdf'):
        relative = 'archive/reference_data/' + name
    elif entry.suffix.lower() == '.js':
        relative = 'assets/web/' + name
    else:
        relative = 'archive/update_bundles/' + name
    move(entry, relative)

for entry in list((ROOT / 'BO').iterdir()):
    if entry.name.startswith('test_'):
        move(entry, 'tests/bo/' + entry.name)
    elif entry.name == 'README.md':
        move(entry, 'docs/BO.md')
    elif entry.name == 'ui_preview.png':
        move(entry, 'docs/images/bo_ui_preview.png')
    elif entry.name == 'requirements.txt':
        move(entry, 'archive/legacy_requirements/bo_requirements.txt')
move(ROOT / 'BO', 'python/app/BO')

# Route resource filenames through one resolver, retaining source formatting.
# This changes paths only; imports and all backend/control functions are preserved.
index = {}
for folder in ('assets', 'config'):
    for file in (ROOT / folder).rglob('*'):
        if file.is_file():
            index.setdefault(file.name, file.relative_to(ROOT).as_posix())
index.update({'CNL_logo.png': 'assets/images/cnl_logo.png',
              'cyclotron_data.db': 'runtime/data/cyclotron_data.db',
              'cyclotron_data_fresh.db': 'runtime/data/cyclotron_data.db',
              'snapshot_log.xlsx': 'runtime/snapshots/snapshot_log.xlsx',
              'snapshot_log.csv': 'runtime/snapshots/snapshot_log.csv',
              'trimcoil_snapshots.xlsx': 'runtime/snapshots/trimcoil_snapshots.xlsx',
              'beam_trace.log': 'runtime/logs/beam_trace.log'})
for name in ('snapshots', 'ga_runs', 'bo_runs', 'pid_logs', 'pid_decision_logs'):
    index[name] = 'runtime/' + name
index['static'] = 'assets/web/static'
(ROOT / 'config/resource_paths.json').write_text(json.dumps(index, indent=2), encoding='utf-8')

for file in [*(ROOT / 'python/app').glob('*.py'), ROOT / 'python/tools/web_app.py']:
    text = file.read_text(encoding='utf-8-sig')
    tree = ast.parse(text)
    lines = text.encode('utf-8').splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    edits = []
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        value = node.value
        # Remove machine-specific artwork paths; absent optional artwork keeps its normal fallback.
        if value.startswith('C:\\') and value.endswith(('.png', '.jpg', '.mp4')):
            value = value.replace('\\', '/').split('/')[-1]
            index.setdefault(value, 'assets/images/' + value)
        if value not in index or isinstance(parents.get(node), ast.JoinedStr):
            continue
        parent = parents.get(node)
        replacement = f'str(project_file({value!r}))'
        if isinstance(parent, ast.Call) and isinstance(parent.func, ast.Attribute) and parent.func.attr == 'with_name':
            node = parent
            replacement = f'project_file({value!r})'
        edits.append((offsets[node.lineno-1] + node.col_offset,
                      offsets[node.end_lineno-1] + node.end_col_offset, replacement.encode('utf-8')))
    if edits:
        raw = text.encode('utf-8')
        for start, end, replacement in sorted(edits, reverse=True):
            raw = raw[:start] + replacement + raw[end:]
        text = raw.decode('utf-8')
        tree = ast.parse(text)
        insert_after = 0
        for node in tree.body:
            if (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)) or (isinstance(node, ast.ImportFrom) and node.module == '__future__'):
                insert_after = node.end_lineno
            else:
                break
        lines = text.splitlines(keepends=True)
        lines.insert(insert_after, 'from project_paths import project_file\n')
        file.write_text(''.join(lines), encoding='utf-8')

# Variable filenames used by the transport monitor cannot be replaced as literals.
file = ROOT / 'python/app/BeamTransportMonitoringWindow.py'
text = file.read_text(encoding='utf-8')
text = text.replace('Path(__file__).resolve().with_name(filename)', 'project_file(filename)')
# Keep candidate names as basenames for the resolver above.
text = text.replace('for filename in (str(project_file(\'cnl_logo.png\')), str(project_file(\'CNL_logo.png\'))):',
                    'for filename in ("cnl_logo.png", "CNL_logo.png"):')
text = text.replace('for filename in (str(project_file(\'cnl_logo.png\')), str(project_file(\'CNL_logo.png\'))):',
                    'for filename in ("cnl_logo.png", "CNL_logo.png"):')
file.write_text(text, encoding='utf-8')
(ROOT / 'config/resource_paths.json').write_text(json.dumps(index, indent=2), encoding='utf-8')
(ROOT / 'docs/layout_migration.json').write_text(json.dumps(MOVES, indent=2), encoding='utf-8')
print(f'Preserved and relocated {len(MOVES)} entries. Backend algorithms unchanged.')
