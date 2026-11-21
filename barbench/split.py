"""Development and test data, kept apart from the start.

Development data is what readers are tuned against: CI gates every commit
on it, and its numbers catch regressions. Test data is what published
numbers come from, to be run only when a release is tagged. Every photo and seed
barbench had before this split had already been run and tuned against, so
all of it is development data.

A test photo is never picked by hand. Its list names a `group` for each
photo (its set plus its capture session or scene, so near-duplicates stay
on one side), and the group's hash decides the split. A group is named for
what it is before anyone looks at where its hash falls: names tried until
one lands on the wanted side would pick by hand again. The rule below is
fixed: changing the hashed text, the bytes read or the threshold would
re-deal photos that have already been looked at, so a new rule is a new
round of photos, never an edit here.
"""

import hashlib

SPLITS = ("dev", "test")

#: A group is test when the first two bytes of the SHA-256 of its name,
#: as UTF-8 and read big-endian, are below this: 19661 of 65536, 30%.
TEST_BELOW = 19661

#: Synthetic images from seeds below this are development images; from
#: this seed up, test images.
TEST_SEEDS = 1000


def of_group(group):
    """The split a group of photos falls in."""
    digest = hashlib.sha256(group.encode("utf-8")).digest()
    return "test" if int.from_bytes(digest[:2], "big") < TEST_BELOW else "dev"


def of_seed(seed):
    """The split the synthetic images from `seed` fall in."""
    if seed < 0:
        raise ValueError(f"seeds are 0 or more, not {seed}")
    return "test" if seed >= TEST_SEEDS else "dev"
