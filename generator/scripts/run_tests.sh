#!/bin/sh
set -eu

ROOT_DIR=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT_DIR"

PYTHONPATH="$ROOT_DIR" python3 -m unittest discover -s tests -p 'test_*.py'
