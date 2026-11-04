#!/usr/bin/env bash
set -euo pipefail

repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
python="${GOVERNEDGRAPH_PYTHON:-$repo/.venv/bin/python}"
if [[ ! -x "$python" ]]; then
    printf 'Missing Python environment. Create .venv and install requirements.txt first.\n' >&2
    exit 1
fi

cd "$repo"
export GOVERNEDGRAPH_MODE=local
export PYTHONPATH="$repo/src:$repo/examples${PYTHONPATH:+:$PYTHONPATH}"
printf 'ILLUSTRATIVE - synthetic data. Local rehearsal only; Azure state is unchanged.\n'
exec "$python" -m pension_wtp.demo.run_demo "$@"
