#!/bin/sh
set -eu

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../../.." && pwd)
source_env=${1:?Usage: bootstrap-worktree-env.sh /absolute/path/to/existing/.env}
source_env=$(CDPATH= cd -- "$(dirname -- "$source_env")" && pwd)/$(basename -- "$source_env")
if [ ! -f "$source_env" ] || [ ! -s "$source_env" ]; then
  echo "Credential file must exist and be nonempty" >&2
  exit 2
fi
destination="$repo_root/.env"
if [ -e "$destination" ] || [ -L "$destination" ]; then
  echo "Workspace .env already exists; leaving it unchanged" >&2
  exit 0
fi
ln -s "$source_env" "$destination"
echo "Linked existing credential file into this worktree"
