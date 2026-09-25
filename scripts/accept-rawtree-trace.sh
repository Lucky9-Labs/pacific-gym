#!/bin/sh
set -eu

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$repo_root/plugins/pacific-gym"
python3 -m unittest discover -s tests -v
python3 -m pacific_gym trace --repo-root "$repo_root" --out "$repo_root/proof/slice-02/proof.json" "$@"
