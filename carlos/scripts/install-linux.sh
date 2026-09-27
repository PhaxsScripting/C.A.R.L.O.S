#!/bin/sh
set -eu
carlos_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
carlos_python=${CARLOS_PYTHON:-python3}
if [ "${1:-}" = "--help" ]; then
    printf '%s\n' 'Usage: sh carlos/scripts/install-linux.sh [--no-start]' 'First run python3 carlos/scripts/linux-support.py install-deps.'
    exit 0
fi
if [ "${1:-}" != '' ] && [ "${1:-}" != '--no-start' ]; then
    printf '%s\n' 'Unknown option; use --help.' >&2
    exit 2
fi
"$carlos_python" "$carlos_root/scripts/linux-support.py" doctor
cmake -S "$carlos_root/ui" -B "$carlos_root/build/ui" -DBUILD_TESTING=ON
cmake --build "$carlos_root/build/ui" -j "${CARLOS_BUILD_JOBS:-2}"
ctest --test-dir "$carlos_root/build/ui" --output-on-failure
exec sh "$carlos_root/scripts/install-user.sh" "$@"
