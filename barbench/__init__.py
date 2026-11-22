"""barbench: a reproducible benchmark for 1D barcode readers.

Three tiers: synthetic Code 128 symbols from third-party encoders
(zxing-cpp and python-barcode), rendered and degraded here with numpy, over
a grid (corpus.generate) or along one parameter at a time (the sweep tier);
and real photos, downloaded from pinned sources and checked against their
digests (photos.py). zxing-cpp, which is also benchmarked, encodes half the
synthetic symbols, and its reader checks every clean symbol before use, on
perfect drawings where any working reader agrees. Every reader runs through
an adapter with the same contract, and results are JSON.
"""

__version__ = "0.0.0"
