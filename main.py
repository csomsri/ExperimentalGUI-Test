"""Portable entry point for the existing ExperimentalGUI application."""
from python.application.launcher import main

if __name__ == '__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    raise SystemExit(main())
