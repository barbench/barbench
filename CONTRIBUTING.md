# Contributing to barbench

Thanks for your interest. The house rules, for people and agents alike, are in
[AGENTS.md](AGENTS.md); the benchmark rules there matter most.

## Before you open a pull request

Run the same checks as CI:

```bash
scripts/ci.sh
```

It needs Python 3.12 or newer and the network. It builds barbench into a wheel, installs it into a
virtual environment under `target/`, and runs the tests and a small synthetic run against that
wheel, then downloads the photos (about 143 MB from the mirror the first time, or 450 MB of
archives from the sources; the photos are cached) and runs zxing-cpp on them. Pass job names to
run a subset: `scripts/ci.sh test synthetic`. Set `STREEPJES_BIN` to a `streepjes` binary to run
and test that reader too.

Add a line under `[Unreleased]` in `CHANGELOG.md` for user-visible changes.

## Adding photos

A new set is a list in `barbench/lists/`: the README's "What each photo shows" says what each
photo records. Every photo needs a licence from the allowlist in AGENTS.md, a SHA-256, and its
symbols with their texts; say in the list where they come from, and record the set in
`ATTRIBUTION.md`. Place corners with `python -m barbench annotate` (see the README), never from a
reader's output. Give each photo a `group` named by the rule in the README (its set, `/`, and the
label's text or a name for the scene), decided before you look at where it lands; its `slice` is
then the first hex digit of the group's SHA-256, which barbench checks; and the date it was
`added`.

## Releasing a photo mirror

When the photos listed change, a maintainer builds the next release's assets from a clean
checkout of the commit that lists them, with
`python -m barbench mirror OUT --cache DIR --release photos-N`, checks the credits in each tar,
publishes the tars as release `photos-N` of this repository with its tag on that commit, and then
commits the `mirror.json` that `OUT` holds as `barbench/mirror.json`, so no barbench points at a
release before it exists. Releases are publishing: AGENTS.md's rule 7 asks before each one.

A new set fails the tests until the mirror has it, since a set the mirror leaves out would quietly
come from its source. So its list is committed on a branch, which is pushed for the release's tag,
and `main` is fast-forwarded to the list and the new `mirror.json` together, once `scripts/ci.sh`
passes on the second. Photos added to a set the mirror has come from the source, with a warning,
until the next release.

## Declaring what a reader holds out

A reader's maintainers say which slices they hold out, and since when, in
`barbench/readers/NAME.json` (`reader`, `held_out`, `since`, `declared_by`, and a `note`). Declare
honestly: from the day you start holding out if you have looked at the photos already listed,
earlier only if you never have. Published comparisons use the shared slices `e` and `f`, so a
reader that wants to be compared holds those out. Its own CI then runs on `photos --dev-for NAME`,
which leaves out its held-out photos.

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
