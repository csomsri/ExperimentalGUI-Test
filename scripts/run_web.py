"""Launch the preserved optional web viewer using the central project paths."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

if __name__ == '__main__':
    import argparse
    from python.application.paths import configure_environment
    parser = argparse.ArgumentParser(description='Optional existing web history viewer')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', default=8000, type=int)
    args = parser.parse_args()
    configure_environment()
    sys.path.insert(0, str(ROOT / 'python' / 'tools'))
    try:
        import uvicorn
    except ImportError:
        raise SystemExit('Install optional requirements: python -m pip install -r requirements-web.txt')
    uvicorn.run('web_app:app', host=args.host, port=args.port)
