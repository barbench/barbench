"""barbench: a reproducible benchmark for 1D barcode readers.

v0, kept in the Streepjes repository until it moves to a neutral home. It
has one tier, synthetic: Code 128 symbols encoded by third-party encoders
(zxing-cpp and python-barcode), rendered and degraded here with numpy.
zxing-cpp, which is also benchmarked, encodes half the symbols, and its
reader checks every clean symbol before use, on perfect drawings where any
working reader agrees. Every reader runs through an
adapter with the same contract, and results are JSON.
"""

__version__ = "0.0.0"
