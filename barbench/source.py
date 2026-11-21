"""Which barbench a report came from.

`__version__` stays 0.0.0 until barbench is released, so a report names
the commit instead: two reports compare only when the same barbench made
them, since the corpus and the scoring are barbench's.
"""

import json
import os
import subprocess
from importlib import metadata
from urllib.parse import unquote, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))


def commit(distribution=metadata.distribution):
    """The commit of the barbench that is running: the one pip installed it
    from, when that was a Git URL; else that of the checkout it runs from
    or was installed from, with "-dirty" when files there differ from it.
    None when neither tells, or when the installed files are no longer
    those of the checkout it was installed from."""
    try:
        dist = distribution("barbench")
    except metadata.PackageNotFoundError:
        dist = None
    # An installed barbench elsewhere says nothing about this copy.
    if dist is not None and _same_file(dist.locate_file(os.path.join("barbench", "__init__.py"))):
        try:
            direct = json.loads(dist.read_text("direct_url.json") or "null")
        except ValueError:
            direct = None
        if not isinstance(direct, dict):
            return None
        if isinstance(direct.get("vcs_info"), dict):
            return direct["vcs_info"].get("commit_id")
        url = direct.get("url", "")
        if "dir_info" in direct and url.startswith("file://"):
            folder = unquote(urlparse(url).path)
            # The checkout may have moved on since the install; then its
            # commit is not the one that is running.
            if not _installed_as_in(dist, folder):
                return None
            return checkout_commit(folder)
        return None
    return checkout_commit(os.path.dirname(HERE))


def checkout_commit(path):
    """The commit checked out at `path`, with "-dirty" when a file under
    `path`, tracked or not ignored, differs from it; None when Git does not
    track `path`, which may be an untracked folder in another checkout."""
    try:
        if not _git(path, "ls-files", "--", "."):
            return None
        head = _git(path, "rev-parse", "HEAD")
        changed = _git(path, "status", "--porcelain", "--", ".")
    except (OSError, subprocess.SubprocessError):
        return None
    return head + "-dirty" if changed else head


def _git(path, *args):
    done = subprocess.run(
        # No optional locks: a report must not get in the way of a git
        # command the user runs at the same time.
        ["git", "--no-optional-locks", "-C", path, *args],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    return done.stdout.strip()


def _installed_as_in(dist, folder):
    """Whether every installed barbench file is byte for byte the file of
    the same name in `folder`."""
    for name in dist.files or ():
        if name.parts[0] != "barbench" or "__pycache__" in name.parts:
            continue
        try:
            with open(dist.locate_file(name), "rb") as a, open(os.path.join(folder, *name.parts), "rb") as b:
                if a.read() != b.read():
                    return False
        except OSError:
            return False
    return True


def _same_file(path):
    try:
        return os.path.samefile(path, os.path.join(HERE, "__init__.py"))
    except OSError:
        return False
