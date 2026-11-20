#!/usr/bin/env bash
# Build and install OpenLit from this checkout. The command remains lit.
set -euo pipefail

repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
install_args=(install --locked --path "$repo_dir" --bin lit)
if [[ $# -ne 0 ]]; then
    if [[ $# -ne 2 || "$1" != --root || -z "$2" ]]; then
        echo 'Usage: ./install.sh [--root INSTALL_DIRECTORY]' >&2
        exit 2
    fi
    install_args+=(--root "$2")
fi
if ! command -v cargo >/dev/null 2>&1; then
    echo 'Cargo is required. Install Rust, then run this script again.' >&2
    exit 1
fi

cargo "${install_args[@]}"
echo 'OpenLit is installed. The executable is lit in your Cargo installation directory.'
echo 'Run lit --version from that directory. See docs/INSTALL.md for setup details.'
