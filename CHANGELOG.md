# Changelog

All notable changes to barbench are documented in this file. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- barbench has a repository of its own, with the history of its code, which `git log` shows. It
  began in the repository of the Streepjes reader and grew there: the synthetic
  and sweep tiers, read-rate curves, the photo tier (ZXing's blackbox photos, Wikimedia Commons
  photos of Code 128 labels, and the KAIST and ArTe-Lab EAN-13 and UPC-A photos), the
  per-photo symbols and corners, `barbench annotate`, and packaging
  as a wheel.
- Slices in place of a development and test split: every photo has a frozen `group`, the `slice`
  its hash gives (one of 16) and the date it was `added`, and each reader declares in
  `barbench/readers/` which slices it holds out and since when. `photos` takes `--slices`,
  `--added-after`, `--held-out-for` (for comparisons) and `--dev-for` (for a reader's own runs);
  reports count photos by group and give a pooled interval.
  Three ArTe-Lab photos get the digits printed under their symbols, and near-duplicates share a
  group. Streepjes declares slices `e` and `f` held out for photos added after 22 November 2026.
- A `vision` reader: Apple Vision's barcode detector at a pinned revision, on macOS, through a
  Swift helper (`barbench/vision.swift`) that the adapter builds with `swiftc` on first use. Its
  version names the macOS build and the chip, since Vision comes with the OS.
- `barbench time DIR`: each reader's start-up and its cost per image, as medians of runs on the
  tier's smallest image and on the whole tier, with the machine and the load average recorded.
- Its own licence files, `AGENTS.md`, `CONTRIBUTING.md`, `ATTRIBUTION.md`, `scripts/ci.sh` and CI
  workflow. The Wikimedia User-Agent names this repository.
