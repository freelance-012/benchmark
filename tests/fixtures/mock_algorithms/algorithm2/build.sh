#!/usr/bin/env bash
set -euo pipefail

mkdir -p build
compiler="${CC:-cc}"
"${compiler}" -std=c11 -Wall -Wextra -Werror -pedantic main.c -o build/algorithm2
