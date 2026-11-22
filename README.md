# barbench

A reproducible benchmark for 1D barcode readers, starting with Code 128.
It began in the repository of the Streepjes reader and lives here, apart
from any one reader.

## What it measures

Synthetic tiers and a photo tier. **Synthetic**: Code 128 symbols whose
modules come from third-party encoders (zxing-cpp and python-barcode,
alternating), drawn and degraded with numpy over a fixed grid:

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

### Curves

The **sweep** tier draws read-rate curves: from a baseline symbol (two
pixels per module, blur 0.2 modules, noise 4 grey levels, tilted as a
hand-held one is, up to 5° either way and upside down half the time), it
moves one parameter at a time through a range, 20 symbols a point, from the
same encoders and with the same checks as the grid:

| Sweep      | Values |
|------------|--------|
| module     | 0.6, 0.8, 1, 1.2, 1.4, 1.6, 2, 2.5, 3, 4, 6 pixels |
| blur       | 0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.8, 1 module (Gaussian sigma) |
| noise      | 0, 4, 8, 16, 24, 32, 48, 64 grey levels (Gaussian sigma) |
| rotation   | 0° to 90° in steps of 10° |

The baseline is tilted rather than turned at random because some readers
read only near-upright symbols, and a random turn would make all their
curves rotation curves; rotation has its own sweep, whose points are
exact angles. Each symbol sits at a random sub-pixel offset, and its pitch
is the point's module size within ±5%, as no camera lines its pixels up
with the modules or holds a pitch exactly. At an exact whole number of
pixels per module every edge falls at the same phase within its pixel, and
a reader fares very differently there than just beside it: in one run
with blur 0.6, zxing-cpp read 90% of symbols at one pixel against none at
0.95 and 1.05, and 32% at two pixels against 95% and 80% at 1.9 and 2.1.

The baseline's noise of 4 grey levels drives ZBar's module-size curve: in
one run ZBar read none of ten symbols at 6 pixels per module with that
noise, and all ten without it, so its fall at large modules is about noise
it does not average away, not about size.

`run` on a sweep tier reports each point's read rate with its 95% Wilson
interval, and `plot` draws the curves as SVG, one panel per sweep, with
the intervals as bands: where bands overlap, the run cannot tell the
readers apart. Twenty symbols a point resolve a read rate to about ±20
percentage points in the middle of a curve; at 20 of 20 the interval
reaches down to 84%, and at 0 of 20 up to 16%.

### Photos

The **photos** tier is 40 real photographs of Code 128 labels (ten
symbols, four shots each: angles, blur, glare, fingers in the frame) and,
as negatives, 80 photos of EAN-13 and UPC-A barcodes, which a Code 128
reader should leave alone. They are ZXing's blackbox test images, ©
2007-2020 ZXing authors, Apache-2.0. The repository holds only their list
(`barbench/lists/zxing-blackbox.json`): each is downloaded from a pinned
ZXing commit, checked against its SHA-256, and converted to PGM.

zxing-cpp's test suite includes some of these images (and ZXing's includes
all of them), so this tier favours it. A reader's misses here are to be
fixed by better decoding, not by tuning to these files.

**Wikimedia Commons** (`barbench/lists/commons-code128.json`) adds 13
photos and scans of Code 128 labels that neither reader's own tests use:
postal registration labels, GS1-128 pallet and parcel labels, a price tag,
shelf, device and street-lamp tags, and thermal receipts. They come from
Commons' Code 128, GS1-128 and Barcode categories. Each file has its own
author and licence (CC0, CC BY 4.0 or public domain), which the list
records with the file's page and pinned original, and so does
`ATTRIBUTION.md`. The text of each Code 128 symbol is the line printed
under it, read by eye, without its grouping spaces and without the
parentheses around GS1 application identifiers, which is how streepjes
reports GS1 data, and zxing-cpp in its plain text mode, which barbench
uses. (The zbar adapter has not been checked on GS1 data.) The list's
`selection` says what was looked at and why the rest was left out. They
are downloaded one at a time, a second apart, with a User-Agent that names
this repository, since Wikimedia's robot policy asks automated clients for
contact details; a 429 answer is waited out as its Retry-After says.

Two more sets add 869 photos of EAN-13 and UPC-A barcodes, negatives for
now, listed with their digits so they can become positives for readers of
EAN:

- **KAIST** (`barbench/lists/kaist-real.json`): 524 cropped phone
  photos from the dataset of the Quick Browser and Smart Inference papers
  (Duy-Thao Do, [Zenodo 13586402](https://zenodo.org/records/13586402)),
  CC BY 4.0. Its `real` folder also holds crops of other sets, which are
  left out: 1,055 from the WWU Muenster database (non-commercial only) and
  406 from ArTe-Lab's, of which the 130 from the Medium collection
  are taken from ArTe-Lab below instead. So are its progressive JPEGs, and
  one photo that also shows a Code 128 label. The 524 kept are the
  baseline JPEGs among the photos named by their 13 digits, which match
  neither of those sets; their licence is the one the dataset states.
- **ArTe-Lab** (`barbench/lists/artelab-medium.json`): the 345
  photos of the Medium Barcode 1D Collection (University of Insubria;
  Zamberletti et al., 2011), with and without autofocus, CC BY 3.0.

Their photos are read from each set's archive, pinned by size and SHA-256,
which is downloaded only while a listed photo is missing from the cache
and deleted afterwards. barbench decodes their baseline JPEGs itself
(`barbench/jpeg.py`): the Python JPEG libraries bundle libjpeg, whose IJG
licence is not on the allowlist.

### What each photo shows

Each photo in a list carries `symbols`, the symbols known to be in it:
each a `symbology` (`code128`, `ean13`, `upca`, `itf`, `qr` or `aztec`, or
`null` when its kind cannot be told) and the `text` a reader should
report, `null` where nobody has read it, with optional `corners` and
`note`. The list's `truth` says where they come from: the digits ZXing,
KAIST and ArTe-Lab give, and a look at each Commons photo and each ZXing
`code128-2` photo. Besides their Code 128 symbols, that found a QR code on
each lamp tag, an Aztec code on the deposit voucher, an Interleaved 2 of 5
symbol on the DHL label and on the label in ZXing's 37 to 40, covered bars
on FlagTag that may be a symbol, codes cut off at the edge in nine ZXing
photos, and one mostly hidden by a finger.

A UPC-A symbol is listed as `upca` with its 12 digits, never as the EAN-13
with a leading zero that has the same bars. zxing-cpp 3.1.1 reports that
13-digit form, even when it names the format UPC-A, so per-format scoring
must strip the zero. barbench checks every symbology name, and the digit
count and check digit of every EAN-13 and UPC-A text.

`symbols` lists what is known to be there, not everything that is: the
KAIST, ArTe-Lab and ZXing EAN-13 and UPC-A photos were not looked over for
other symbols, so their counting as Code 128 negatives is still an
assumption, which no reader's reads have contradicted. For now each
manifest row also has the `expected` text a Code 128 reader must report,
taken from its Code 128 symbols; barbench refuses a photo with Code 128
symbols of different texts, or one of unknown text, which scoring cannot
judge yet.

`corners` say where a symbol is: four points `[x, y]` in the photo's
stored pixels (x right, y down, `(0, 0)` the top-left corner of the
top-left pixel; a JPEG's orientation tag is ignored, as barbench ignores
it when it converts the photo), at the corners of the bars alone, without
quiet zones or text. They run start-top, stop-top, stop-bottom,
start-bottom, where the start is where reading begins and "top" is the
upper edge with the start on the left, so with y down they go clockwise,
and they give the symbol's position, size, rotation and direction. The
direction was taken from the printed text, which reads the way the symbol
does, not from the start and stop patterns. barbench checks that corners
are four points that run clockwise round a convex quadrilateral inside the
photo, which catches a mirrored order but not one that starts at the wrong
corner.

All 53 Code 128 symbols have corners, placed by eye on magnified views in
November 2026. A second look at six of them, made without seeing the
first, agreed to within about a pixel, and on a blurred photo the listed
corners are good to two or three; a symbol's note says where a straight
quadrilateral fits a curved or distorted label poorly. No source gives
corners, and those a reader reports would favour that reader, so other
photos have none yet.

To place a symbol's corners, `python -m barbench annotate` draws views
from the photo cache `barbench photos` filled, naming a photo `SET/FILE`
as the lists do: `overview` (the whole photo with a grid labelled in its
pixels), `zoom` (a magnified square round a point), `corners` (the four
proposed corners magnified and marked, laid out start-top and stop-top
over start-bottom and stop-bottom) and `overlay` (the quadrilateral drawn
on the photo, its first edge green). Move each corner until its mark sits
on the corner, check the overlay, and write the corners into the list; the
views refuse corners that break the rules a list's corners must keep.
`annotate sheet SET PREFIX` draws every listed symbol of a set with its
corners in the list's order, to check a list by eye; a second person
placing a few without seeing the first is the better check.

### Slices, and what each reader holds out

The library records facts about each photo, never which reader may look at
it. Every photo has a `group` (the physical label or scene it shows, named
as its set, `/`, and the label's text or a name for the scene, with a
folder between where a set's folders differ, so photos of one label always
travel together), a `slice` and the date it was `added`. Its slice is the
first hex digit of the SHA-256 of its group name:

```bash
printf '%s' 'zxing-blackbox/code128-2/005-3379497200006' | sha256sum | cut -c1
```

The rule is fixed (`barbench/slices.py`): changing the hash, the digit or
a group's name would move photos that readers have declared held out or
not, so barbench checks every stored slice against it, and a group is
named for what it shows before anyone looks at where it lands. Photos of
one label are not independent: reports count positives and negatives by
group as well as by photo (`positive_groups`, `negative_groups`), since an
interval's real width rests on the number of groups, and give the pooled
`read_interval` (Wilson, which treats photos as independent and so is too
narrow where groups hold several).

Whether a photo can measure a reader fairly depends on that reader:
whether its developers have looked at it. Each reader that takes part
declares, in `barbench/readers/NAME.json`, the slices it holds out and the
date from which it has; its held-out photos are those in its slices added
strictly after that date, so not one added the same day. A reader whose
developers have looked at every photo listed so far declares from the day
it starts holding out; one that never looked can declare from any earlier
date. barbench records declarations and cannot check them. Streepjes,
developed against every photo listed on 21 November 2026, holds out slices
`e` and `f` for photos added after 22 November 2026, so it has no held-out
photos yet.

Published comparisons all hold out the same slices, `e` and `f`, so no
reader picks easy ones, and use only photos added after every compared
reader's declaration, so each photo is held out for all of them. `photos
--held-out-for a,b` writes exactly those, and `photos --dev-for a`
everything reader `a` does not hold out, which is what its own CI should
run on; `--slices` (digits and ranges, such as `0-d`) and `--added-after`
choose others, and each report says which slices and date it covers and
what it left out. Every current set is public, so a reader may have met
its photos outside barbench (ZXing's are ZXing's own test images); such
facts belong in a reader's declaration. With 53 Code 128 photos in 21
groups, no choice of slices measures much yet: honest numbers need several
hundred, in many groups, added after the readers declare.

The synthetic tiers need no slices: anyone can draw a fresh corpus from a
new seed.

## Readers

Each reader runs through an adapter with the same contract: PGM paths in,
decoded Code 128 texts per image out.

- **streepjes**: the Streepjes reader's `streepjes` command, default
  effort, one process for the batch. Set `STREEPJES_BIN` to the binary.
- **zxing-cpp**: its Python binding, in process, in its plain text mode.
- **zbar**: `zbarimg`, one process per image, if installed.

A reader that is not installed is skipped and reported as such.

## Running it

It needs Python 3.12 or newer. barbench is a package (`pyproject.toml`,
which pins its dependencies exactly) and runs from where it is installed,
photo lists included:

```bash
python3 -m venv target/venv
target/venv/bin/pip install .
target/venv/bin/python -m barbench corpus target/corpus
target/venv/bin/python -m barbench run target/corpus --out target/results.json
```

Install again after changing barbench (`pip install .` reinstalls it), or
run `python -m barbench` from the repository root, where it finds the
checkout before the installed copy. Each report names the barbench commit
that made it (`barbench_commit`): the one pip installed from when that was
a Git URL, else that of the checkout, with `-dirty` when its files differ
from it. It is `null` when an installed copy is no longer what its
checkout holds, or when Git cannot tell.

For the curves, `python -m barbench sweep DIR` in place of `corpus`, then
`run` as above with `--out`, and `python -m barbench plot RESULTS --out
curves.svg`.

For the photo tier, `python -m barbench photos DIR` in place of `corpus`
downloads and converts the photos into `DIR-cache` (the archives are about
450 MB together; only the 140 MB of listed photos stay). `--sets
zxing-blackbox` limits it to the ZXing set, which is 20 MB.

`scripts/ci.sh` does the same on a smaller synthetic corpus and on every
photo, against the installed wheel, and runs the tests in `tests/`;
CONTRIBUTING.md says more. It reads with zxing-cpp, and with streepjes too
when `STREEPJES_BIN` is set.

## Later

- Invite the other readers' maintainers to check their adapters.
- Pin every reader and tool in a Docker image, and publish results from CI
  as a static page.
- More tiers: more real photos of Code 128 (ZXing's set is small and
  familiar to zxing-cpp, and Commons holds few with a usable licence),
  print defects, perspective, and more symbologies.
- Time per image rather than per batch, measured the same way for every
  reader.
- Only released versions are benchmarked for published results.

## Licence

barbench is dual licensed under [MIT](LICENSE-MIT) OR
[Apache-2.0](LICENSE-APACHE). The photos it downloads are not part of it;
each set's licence and credits are in [ATTRIBUTION.md](ATTRIBUTION.md).
