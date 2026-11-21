# barbench v0

A reproducible benchmark for 1D barcode readers, starting with Code 128. It
lives here until it moves to a neutral home of its own (see "Later" below).

## What it measures

Two tiers. **Synthetic**: Code 128 symbols whose modules come from
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

### Photos

The **photos** tier is 40 real photographs of Code 128 labels (ten symbols,
four shots each: angles, blur, glare, fingers in the frame) and, as
negatives, 80 photos of EAN-13 and UPC-A barcodes, which a Code 128 reader
should leave alone. They are ZXing's blackbox test images, © 2007-2020
ZXing authors, Apache-2.0. The repository holds only their list
(`bench/photos/zxing-blackbox.json`): each is downloaded from a pinned ZXing
commit, checked against its SHA-256, and converted to PGM.

zxing-cpp's test suite includes some of these images (and ZXing's includes
all of them), so this tier favours it. Streepjes has not been tuned on
them; its misses here are to be fixed by better decoding, not by tuning to
these files.

Two more sets add 869 photos of EAN-13 and UPC-A barcodes, negatives for
now, listed with their digits (`payload`, for all but three photos that
the set gives none for) so they can become positives when Streepjes reads
EAN:

- **KAIST** (`bench/photos/kaist-real.json`): 524 cropped phone photos
  from the dataset of the Quick Browser and Smart Inference papers
  (Duy-Thao Do, [Zenodo 13586402](https://zenodo.org/records/13586402)),
  CC BY 4.0. Its `real` folder also holds crops of other sets, which are
  left out: 1,055 from the WWU Muenster database (non-commercial only) and
  406 from ArTe-Lab's, of which the 130 from the Medium collection
  are taken from ArTe-Lab below instead. So are its progressive JPEGs, and
  one photo that also shows a Code 128 label. The 524 kept are the
  baseline JPEGs among the photos named by their 13 digits, which match
  neither of those sets; their licence is the one the dataset states.
- **ArTe-Lab** (`bench/photos/artelab-medium.json`): the 345 photos of the
  Medium Barcode 1D Collection (University of Insubria; Zamberletti et
  al., 2011), with and without autofocus, CC BY 3.0.

Their photos are read from each set's archive, pinned by size and SHA-256,
which is downloaded only while a listed photo is missing from the cache
and deleted afterwards. barbench decodes their baseline JPEGs itself
(`barbench/jpeg.py`): the Python JPEG libraries bundle libjpeg, whose IJG
licence is not on the allowlist.

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

For the photo tier, `python -m barbench photos DIR` in place of `corpus`
downloads and converts the photos into `DIR-cache` (the archives are about
450 MB together; only the 130 MB of listed photos stay). `--sets
zxing-blackbox` limits it to the ZXing set, which is 20 MB.

`scripts/ci.sh bench` does the same on a smaller corpus and on the
photos, runs the tests in `bench/tests`, and fails if streepjes misreads,
reads a code where there is none, fails on a file, or reads under half the
synthetic symbols or 90% of the photos.

## Later

- Move to a neutral organisation, with the other projects' maintainers
  invited to check their adapters.
- Pin every reader and tool in a Docker image, and publish results from CI
  as a static page.
- More tiers: more real photos of Code 128 (ZXing's set is small and
  familiar to zxing-cpp), print defects, perspective, and more symbologies
  as Streepjes reads them.
- Time per image rather than per batch, measured the same way for every
  reader.
- Only released versions are benchmarked for published results.
