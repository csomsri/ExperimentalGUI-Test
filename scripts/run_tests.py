"""Run the maintained tests without importing archived code."""
import argparse
import sys
import unittest
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'python' / 'app'))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--ui', action='store_true', help='also check Qt widgets and the simulated BO workflow')
    args = parser.parse_args()
    temporary = ROOT / 'runtime' / 'test-tmp'
    temporary.mkdir(parents=True, exist_ok=True)
    tempfile.tempdir = str(temporary)
    suites = ['tests.test_layout', 'tests.bo.test_bo_integration', 'tests.bo.test_experiment_log',
              'tests.bo.test_gp_statistics']
    if args.ui:
        suites.append('tests.bo.test_ui')
    suite = unittest.defaultTestLoader.loadTestsFromNames(suites)
    return 0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1


if __name__ == '__main__':
    raise SystemExit(main())
