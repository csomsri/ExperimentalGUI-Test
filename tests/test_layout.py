"""Check portable startup, resource resolution, and preservation of backend files."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from python.application.paths import ROOT, APP, RUNTIME, configure_environment, project_file


class LayoutTests(unittest.TestCase):
    def test_original_backend_files_are_byte_identical(self):
        records = {r['source']: r for r in json.loads((ROOT / 'docs/layout_migration.json').read_text())}
        for name in ('main.py', 'zmq_rep_server.py', 'pid_controller.py', 'ga_pid_tuner.py',
                     'ga_recovery_manager.py', 'ga_test_runner.py', 'pid_beam_buffer.py',
                     'manual_control_tab.py', 'sequence_control_tab.py', 'controller_context.py'):
            record = records[name]
            destination = ROOT / record['destination']
            self.assertEqual(hashlib.sha256(destination.read_bytes()).hexdigest(), record['sha256'], name)

    def test_preserved_files_and_directories_still_exist(self):
        for record in json.loads((ROOT / 'docs/layout_migration.json').read_text()):
            self.assertTrue((ROOT / record['destination']).exists(), record['destination'])

    def test_resources_resolve_from_another_working_directory(self):
        before = Path.cwd()
        with tempfile.TemporaryDirectory() as folder:
            try:
                os.chdir(folder)
                for name in ('beam_cal.json', 'calibration.json', 'cnl_logo.png',
                             'icon_controllers_3d.png', 'gui_bg.mp4'):
                    self.assertTrue(project_file(name).is_file(), name)
                self.assertEqual(project_file('bo_runs'), RUNTIME / 'bo_runs')
            finally:
                os.chdir(before)

    def test_database_overrides_are_preserved_independently(self):
        before = Path.cwd()
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {}, clear=True):
            try:
                os.chdir(folder)
                os.environ['CNL_DB_PATH'] = 'custom.db'
                os.environ['CNL_SQLITE'] = 'viewer.db'
                configure_environment()
                self.assertEqual(os.environ['CNL_DB_PATH'], str(Path(folder) / 'custom.db'))
                self.assertEqual(os.environ['CNL_SQLITE'], str(Path(folder) / 'viewer.db'))
                self.assertEqual(Path.cwd(), RUNTIME)
            finally:
                os.chdir(before)

    def test_root_help_needs_no_gui_or_backend_workers(self):
        with tempfile.TemporaryDirectory() as folder:
            result = subprocess.run([sys.executable, str(ROOT / 'main.py'), '--help'],
                                    cwd=folder, text=True, capture_output=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('--check', result.stdout)


if __name__ == '__main__':
    unittest.main()
