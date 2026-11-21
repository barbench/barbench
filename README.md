# barbench v0

A reproducible benchmark for 1D barcode readers, starting with Code 128. It
lives here until it moves to a neutral home of its own (see "Later" below).

## What it measures

One tier so far, **synthetic**: Code 128 symbols whose modules come from
third-party encoders (zxing-cpp and python-barcode, alternating), drawn and
degraded with numpy over a fixed grid:

| Parameter  | Values                  |
|------------|-------------------------|
| module     | 1, 1.5, 2, 3 pixels     |
| blur       | 0, 0.3, 0.6 modules (Gaussian sigma) |
| rotation   | 0°, 15°, 45°, 90°       |
| noise      | 0, 8 grey levels (Gaussian sigma) |

Two payloads per cell, 192 symbols, plus 8 images with no symbol (noise,
and random bars that spell nothing). Each payload is text or digits at
random, and the encoder is balanced over the grid, so every value of every
parameter gets each encoder equally often. Every clean symbol is read back
by zxing-cpp's reader at four pixels per module before it is used, and
payloads an encoder gets wrong are replaced and listed in the manifest
(python-barcode 0.16.1 drops a leading "99" in code set C). zxing-cpp is
also benchmarked: it encodes half the symbols and checks them all, on
perfect drawings, where any working reader agrees.

For each reader: how many symbols it read, how many it misread (any output
other than the payload), how many negatives it read a code in, and the
wall-clock seconds for the batch, plus read rates by each parameter.

The seconds are **not comparable** between readers yet: streepjes's
include starting its process and parsing the files, zxing-cpp's exclude
reading the files, and zbar starts a process per image. Each reader also
runs with its own defaults: streepjes at its fast effort, zxing-cpp with
its rotation, downscaling and inversion retries on.

## Readers

Each reader runs through an adapter with the same contract: PGM paths in,
decoded Code 128 texts per image out.

- **streepjes**: the `streepjes` command (`crates/streepjes-cli`), default
  effort, one process for the batch. Set `STREEPJES_BIN` to the binary.
- **zxing-cpp**: its Python binding, in process.
- **zbar**: `zbarimg`, one process per image, if installed.

A reader that is not installed is skipped and reported as such.

## Running it

It needs Python 3.12 or newer. From the repository root:

```bash
python3 -m venv target/bench-venv
target/bench-venv/bin/pip install -r bench/requirements.txt
cargo build --release -p streepjes-cli
cd bench
../target/bench-venv/bin/python -m barbench corpus ../target/bench/corpus
STREEPJES_BIN=../target/release/streepjes \
    ../target/bench-venv/bin/python -m barbench run ../target/bench/corpus --out ../target/bench/results.json
```

`scripts/ci.sh bench` does the same on a smaller corpus, runs the tests in
`bench/tests`, and fails if streepjes misreads, reads a code where there is
none, fails on a file, or reads under half the symbols.

## Later

- Move to a neutral organisation, with the other projects' maintainers
  invited to check their adapters.
- Pin every reader and tool in a Docker image, and publish results from CI
  as a static page.
- More tiers: real photos (once an openly licensed set is chosen), print
  defects, perspective, and more symbologies as Streepjes reads them.
- Time per image rather than per batch, measured the same way for every
  reader.
- Only released versions are benchmarked for published results.
