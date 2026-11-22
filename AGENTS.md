# AGENTS.md

barbench is a reproducible benchmark for barcode readers. These rules apply to everyone who
changes this repository, people and agents alike. Read the [README](README.md) and
[CONTRIBUTING](CONTRIBUTING.md) first.

## House rules

1. **A person is accountable.** Whoever runs an agent answers for what it commits and pushes.
2. **Verify, don't guess.** Open a definition before using it. Confirm a cause in the code, even
   when someone names a location.
3. **Keep changes narrow and tested.** Change what the task needs, where it belongs. Every change
   comes with tests; a fix comes with a test that fails without it.
4. **Self-review in a clean context before every code commit.** A reviewer who did not write the
   change (a fresh agent or another person) reads the diff and the files it touches. Fix what it
   finds, or say why not, before committing. Check that identifiers exist as used, tests fail
   without the fix, nothing unrelated changed, and the commit message is true.
5. **Report only what ran.** Say what you tested and what you could not run, and leave to CI what
   needs something you lack.
6. **Gate every commit.** Work on a branch, run `scripts/ci.sh` until every job passes, do the
   self-review, then fast-forward `main` and push.
7. **Stop and ask only about** licences outside the allowlist, adding or dropping a photo set,
   publishing (PyPI, a results page, a Docker image), releases, a new remote, repository
   settings, secrets, spending money, destructive git, and anything else hard to undo. Otherwise
   keep working.
8. **Keep secrets and personal details out** of the repository, logs and requests.
9. **Guards say why.** A check, limit or test that looks removable says what it prevents. Read
   that before changing it.
10. **Instructions are readable by everyone.** No notes about one machine, and no links to files
    or documents a reader cannot open.
11. **No agent attribution.** Commits and pull requests do not say an agent wrote them. DCO
    sign-off (`git commit -s`) is still required.

## Benchmark rules

1. **Fair to every reader.** No reader's output is ground truth: a symbol's text comes from its
   source or from the line printed with it, read by eye, and its corners are placed by eye
   (`barbench annotate`). Every adapter asks its reader for the same thing; a change that helps
   one reader more than another says so.
2. **The library records facts, readers declare what they hold out.** Every photo has a frozen
   `group`, the `slice` its hash gives (`barbench/slices.py`, a rule never to change) and the date
   it was `added`; no photo is marked development or test for everyone. A reader's held-out
   photos follow from its own declaration in `barbench/readers/`. Published comparisons hold out
   the shared slices, only for photos added after every compared reader declared.
3. **Deterministic corpora.** Synthetic images come from a seed, and every dependency is pinned
   exactly in `pyproject.toml`, so a corpus and its numbers do not change under you.
4. **Photos stay out of the repository.** A set is a list in `barbench/lists/` with every file's
   SHA-256, its licence, a `notice` saying whose it is, a `truth` saying where each symbol's text
   and corners come from, and a `selection` saying what was taken and why when not all of a
   source was. It is recorded in `ATTRIBUTION.md` in the same change.
5. **Personal data stays out.** Leave out a photo that shows a private person's name or address,
   and blur it in photos the project takes itself.
6. **Dependencies are rare.** barbench decodes PNG and baseline JPEG itself because the imaging
   libraries' licences are not on the allowlist. A new dependency needs a licence from the
   allowlist.

## Licence allowlist

An SPDX expression is allowed when it can be satisfied from the list for its kind: both sides of
every `AND` are allowed, and at least one side of every `OR`, which is the licence we take it
under. A licence `WITH` an exception counts only as listed.

- **Code dependencies:** `MIT`, `Apache-2.0`, `Apache-2.0 WITH LLVM-exception`, `BSD-2-Clause`,
  `BSD-3-Clause`, `ISC`, `Unicode-3.0`, `Zlib`.
- **Photos and other test assets:** `CC0-1.0`, `CC-BY-3.0`, `CC-BY-4.0`,
  `LicenseRef-Public-Domain`, or any code licence above.
- **Exception, for tools that ship nothing:** `0BSD` and `CC0-1.0` for the Python packages
  barbench runs, such as numpy.

## Before you commit

```bash
scripts/ci.sh
```

Then the clean-context self-review (house rule 4), a `CHANGELOG.md` line under `[Unreleased]` for
user-visible changes, and `git commit -s`.
