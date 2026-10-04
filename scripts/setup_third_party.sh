#!/usr/bin/env bash
# Fetch third-party OCR engine: NDLOCR-Lite (ndl-lab, CC-BY-4.0)
set -e
cd "$(dirname "$0")/.."
if [ -f third_party/ndlocr-lite/src/ocr.py ]; then echo "ndlocr-lite already present"; exit 0; fi
mkdir -p third_party
git clone --depth 1 https://github.com/ndl-lab/ndlocr-lite third_party/ndlocr-lite
echo "OK: third_party/ndlocr-lite (deps: pip install -r requirements-ndl.txt)"
