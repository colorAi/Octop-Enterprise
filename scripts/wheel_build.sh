#!/usr/bin/env bash
# Build a full wheel package including the latest dashboard frontend.
# Run from repo root: bash scripts/wheel_build.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

DASHBOARD_DIR="$REPO_ROOT/dashboard"
DASHBOARD_DEST="$REPO_ROOT/src/octop/dashboard"

echo "[wheel_build] Building dashboard frontend..."
(cd "$DASHBOARD_DIR" && npm ci)
(cd "$DASHBOARD_DIR" && npm run build)

if [ ! -f "$DASHBOARD_DEST/index.html" ]; then
    echo "[wheel_build] ERROR: dashboard build did not produce src/octop/dashboard/index.html" >&2
    exit 1
fi

echo "[wheel_build] Dashboard ready at src/octop/dashboard/"

echo "[wheel_build] Building wheel + sdist..."
python3 -m pip install --quiet build
rm -rf dist/*
python3 -m build --outdir dist .

echo "[wheel_build] Done. Artifacts in: $REPO_ROOT/dist/"
ls -la dist/
