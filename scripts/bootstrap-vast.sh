#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[gpu,dev]'
mkdir -p runs
.venv/bin/python -m pip freeze --exclude-editable > runs/vast-environment.lock.txt
.venv/bin/python -m saeruq doctor --gpu > runs/vast-doctor.json
cat runs/vast-doctor.json
