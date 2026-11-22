# Changelog

All notable changes to barbench are documented in this file. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- barbench has a repository of its own, with the history of its code, which `git log` shows. It
  began in the repository of the Streepjes reader and grew there: the synthetic
  and sweep tiers, read-rate curves, the photo tier (ZXing's blackbox photos, Wikimedia Commons
  photos of Code 128 labels, and the KAIST and ArTe-Lab EAN-13 and UPC-A photos), the
  development and test split, per-photo symbols and corners, `barbench annotate`, and packaging
  as a wheel.
- Its own licence files, `AGENTS.md`, `CONTRIBUTING.md`, `ATTRIBUTION.md`, `scripts/ci.sh` and CI
  workflow. The Wikimedia User-Agent names this repository.
