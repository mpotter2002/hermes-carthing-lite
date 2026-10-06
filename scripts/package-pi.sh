#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUTPUT_DIR="$PROJECT_DIR/output"
ARCHIVE="$OUTPUT_DIR/agent-monitor-pi.tar.gz"

mkdir -p "$OUTPUT_DIR"
tar -C "$PROJECT_DIR" \
  --exclude='*/__pycache__' \
  --exclude='*.pyc' \
  -czf "$ARCHIVE" \
  README.md \
  index.html \
  config.js \
  assets \
  bridge \
  pi \
  scripts/install-pi.sh \
  scripts/install-standalone.sh \
  scripts/restore-deskthing.sh

echo "$ARCHIVE"
