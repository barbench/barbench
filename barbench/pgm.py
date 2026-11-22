"""Binary PGM (P5) files, 8-bit, the format every adapter reads."""


def write(path, pixels):
    """Writes a 2D uint8 numpy array as a binary PGM."""
    height, width = pixels.shape
    with open(path, "wb") as f:
        f.write(b"P5\n%d %d\n255\n" % (width, height))
        f.write(pixels.tobytes())


def read(path):
    """Reads a binary 8-bit PGM written by `write` as a 2D uint8 array."""
    import numpy as np

    with open(path, "rb") as f:
        data = f.read()
    width, height, at = _header(data, path)
    raster = data[at + 1 : at + 1 + width * height]
    if len(raster) != width * height:
        raise ValueError(f"{path}: truncated")
    return np.frombuffer(raster, dtype=np.uint8).reshape(height, width)


def size(path):
    """The width and height of a binary 8-bit PGM, from its header alone."""
    with open(path, "rb") as f:
        # A header `write` wrote is a few bytes; this allows for much more.
        width, height, _ = _header(f.read(4096), path)
    return width, height


def _header(data, path):
    """The width and height in a PGM's header, and where the header ends."""
    fields = []
    at = 0
    while len(fields) < 4:
        while at < len(data) and data[at : at + 1].isspace():
            at += 1
        start = at
        while at < len(data) and not data[at : at + 1].isspace():
            at += 1
        fields.append(data[start:at])
    if fields[0] != b"P5" or int(fields[3]) != 255:
        raise ValueError(f"{path}: not an 8-bit binary PGM")
    return int(fields[1]), int(fields[2]), at
