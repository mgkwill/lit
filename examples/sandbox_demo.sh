#!/usr/bin/env bash
# Demonstrate a strict OpenLit sandbox in a disposable repository.
set -euo pipefail
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
lit_binary="${LIT_BINARY:-$script_dir/../target/release/lit}"
if [[ ! -x "$lit_binary" ]]; then
    echo 'Build OpenLit first, or set LIT_BINARY to its executable.' >&2
    exit 1
fi
lit_binary="$(cd -- "$(dirname -- "$lit_binary")" && pwd)/$(basename -- "$lit_binary")"
demo_dir="$(mktemp -d "${TMPDIR:-/tmp}/openlit-sandbox.XXXXXXXX")"
trap 'rm -rf -- "$demo_dir"' EXIT
cd "$demo_dir"
"$lit_binary" init
printf 'A disposable sandbox example.\n' > example.txt
"$lit_binary" sandbox init demo
# A strict run refuses before payload launch if this host cannot enforce isolation.
"$lit_binary" sandbox run --strict demo -- sh -c 'cat example.txt'
echo 'Inspect controls_enforced in the result. Default mode does not provide isolation.'
