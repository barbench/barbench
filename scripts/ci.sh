#!/usr/bin/env bash
# Runs barbench's CI jobs locally, as .github/workflows/ci.yml does.
#
#   scripts/ci.sh                 every job
#   scripts/ci.sh test synthetic  some of them
#
# Jobs: test, synthetic, photos. barbench is first built into a wheel and
# installed with its pinned dependencies into a virtual environment under
# target/ (BARBENCH_TARGET to put it elsewhere); every job runs that wheel,
# from target/, where no barbench folder can be imported instead.
#
# Needs Python 3.12 or newer and the network. The photos job downloads
# about 143 MB from the photo mirror the first time (450 MB of archives from
# the sources when the mirror fails) and keeps the listed photos in
# BARBENCH_PHOTO_CACHE (default target/photo-cache). Set STREEPJES_BIN to a
# streepjes binary to run and test that reader too.
set -euo pipefail

cd "$(dirname "$0")/.."
root="$PWD"
target="${BARBENCH_TARGET:-$root/target}"
case "$target" in /*) ;; *) target="$root/$target" ;; esac
venv="$target/venv"
cache="${BARBENCH_PHOTO_CACHE:-$target/photo-cache}"
case "$cache" in /*) ;; *) cache="$root/$cache" ;; esac
readers=zxing-cpp
photo_choice=()
if [ -n "${STREEPJES_BIN:-}" ]; then
    readers="zxing-cpp,streepjes"
    # streepjes runs on no photo it declares held out.
    photo_choice=(--dev-for streepjes)
fi
# Gates on zxing-cpp that check barbench, not zxing-cpp: a corpus, a
# decoder or scoring that broke would make it misread or read far less.
# Its pinned version reads about 51% of the small synthetic corpus and 50
# of the 55 Code 128 photos; a new pin that moves those moves the floors.
gates=(--no-misreads zxing-cpp)

step() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }

setup() {
    step "Python environment, and barbench as a wheel"
    python3 -c 'import sys; sys.exit(sys.version_info < (3, 12))' ||
        { echo "error: Python 3.12 or newer is needed" >&2; exit 1; }
    # Rebuild the environment unless it is whole: an interrupted creation,
    # or a cache that kept only bin/'s symlinks, leaves one whose python
    # is the system's, and installing through it would reach outside.
    if [ ! -f "$venv/pyvenv.cfg" ] ||
        ! "$venv/bin/python" -c 'import sys; sys.exit(sys.prefix == sys.base_prefix)' 2>/dev/null ||
        ! "$venv/bin/python" -m pip --version >/dev/null 2>&1; then
        python3 -m venv --clear "$venv"
    fi
    # pip reinstalls a folder each time, whatever its version.
    "$venv/bin/python" -m pip install --quiet --disable-pip-version-check "$root"
    mkdir -p "$target"
}

job_test() {
    step "tests, against the installed wheel"
    (cd "$target" && BARBENCH_INSTALLED=1 "$venv/bin/python" -m unittest discover -s "$root/tests" -t "$root/tests")
}

job_synthetic() {
    step "a small synthetic corpus, read by $readers"
    rm -rf "$target/corpus"
    (cd "$target" && "$venv/bin/python" -m barbench corpus corpus --per-cell 1)
    (cd "$target" && "$venv/bin/python" -m barbench run corpus --readers "$readers" --require "$readers" \
        "${gates[@]}" --min-read zxing-cpp=0.4 --out synthetic-results.json)
    # Timing has no gate, since CI machines vary; this checks that it runs.
    (cd "$target" && "$venv/bin/python" -m barbench time corpus --readers "$readers" --runs 1 \
        --out synthetic-timing.json)
}

job_photos() {
    step "photos (downloaded once, checked against their digests), read by $readers"
    rm -rf "$target/photos"
    (cd "$target" && "$venv/bin/python" -m barbench photos photos --cache "$cache" ${photo_choice[@]+"${photo_choice[@]}"})
    (cd "$target" && "$venv/bin/python" -m barbench run photos --readers "$readers" --require "$readers" \
        "${gates[@]}" --min-read zxing-cpp=0.85 --out photo-results.json)
}

jobs=("$@")
[ ${#jobs[@]} -eq 0 ] && jobs=(test synthetic photos)
for job in "${jobs[@]}"; do
    case "$job" in
    test | synthetic | photos) ;;
    *)
        echo "error: unknown job '$job' (expected test, synthetic or photos)" >&2
        exit 2
        ;;
    esac
done
setup
for job in "${jobs[@]}"; do
    "job_$job"
done
printf '\n\033[1;32mAll requested CI jobs passed.\033[0m\n'
