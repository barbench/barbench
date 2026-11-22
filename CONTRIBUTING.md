# Contributing to barbench

Thanks for your interest. The house rules, for people and agents alike, are in
[AGENTS.md](AGENTS.md); the benchmark rules there matter most.

## Before you open a pull request

Run the same checks as CI:

```bash
scripts/ci.sh
```

It needs Python 3.12 or newer and the network. It builds barbench into a wheel, installs it into
a virtual environment under `target/`, and runs the tests and a small synthetic run against that
wheel, then downloads the development photos (about 450 MB of archives the first time; the photos
are cached) and runs zxing-cpp on them. Pass job names to run a subset: `scripts/ci.sh test
synthetic`. Set `STREEPJES_BIN` to a `streepjes` binary to run and test that reader too.

Add a line under `[Unreleased]` in `CHANGELOG.md` for user-visible changes.

## Adding photos

A new set is a list in `barbench/lists/`: the README's "What each photo shows" says what each
photo records. Every photo needs a licence from the allowlist in AGENTS.md, a SHA-256, and its
symbols with their texts; say in the list where they come from, and record the set in
`ATTRIBUTION.md`. Place corners with `python -m barbench annotate` (see the README), never from a
reader's output. Photos for the test split need a `group` each, and the list must say
`"split_by": "group"`; barbench refuses a group whose hash does not fall in the list's split.

## Adding a reader

A reader is an adapter in `barbench/adapters.py`: PGM paths in, the texts it read per image out,
asked for the same symbology and given the same images as every other reader. Ask its
maintainers to check the adapter before its results are published.

## Developer Certificate of Origin

Contributions are accepted under the [Developer Certificate of
Origin](https://developercertificate.org/) (DCO). Sign off every commit to
certify that you wrote it or otherwise have the right to submit it:

```bash
git commit -s
```

This adds a `Signed-off-by: Your Name <you@example.com>` line. No separate
contributor licence agreement is needed.

## Licence

By contributing, you agree that your contributions are dual licensed under
MIT OR Apache-2.0, as described in the README.
