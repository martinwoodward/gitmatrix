# PNG checks and fitting for notifications.
#
# Sizes are checked from the IHDR header before anything is decoded, so a
# hostile PNG can't make us allocate a huge image. Decoding uses picovector's
# image.load(bytes); images taller than 7 rows AND wider than 53 columns are
# scaled (nearest neighbour, aspect kept) down to 7 rows.

WIDTH = 53
HEIGHT = 7

MAX_BYTES = 32 * 1024
MAX_W = 2048
MAX_H = 64

SIG = b"\x89PNG\r\n\x1a\n"


class PNGError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def size(data):
    """(width, height) from the IHDR chunk, without decoding."""
    if len(data) < 24 or bytes(data[:8]) != SIG or bytes(data[12:16]) != b"IHDR":
        raise PNGError("image is not a PNG")
    w = int.from_bytes(bytes(data[16:20]), "big")
    h = int.from_bytes(bytes(data[20:24]), "big")
    return w, h


def check(data):
    """Raise PNGError (413 for too big) unless the PNG is within the limits."""
    if len(data) > MAX_BYTES:
        raise PNGError(f"image larger than {MAX_BYTES // 1024} KB", 413)
    w, h = size(data)
    if w < 1 or h < 1:
        raise PNGError("image is empty")
    if w > MAX_W or h > MAX_H:
        raise PNGError(f"image larger than {MAX_W}x{MAX_H}", 413)
    return w, h


def fitted_size(w, h):
    """Size after fitting: both dimensions too big -> scaled to 7 rows."""
    if w > WIDTH and h > HEIGHT:
        return max(1, (w * HEIGHT + h // 2) // h), HEIGHT
    return w, h


def decode(data):
    """Check, decode and fit a PNG. Returns a picovector image."""
    w, h = check(data)
    img = image.load(data)
    w, h = img.width, img.height
    fw, fh = fitted_size(w, h)
    if (fw, fh) == (w, h):
        return img
    out = image(fw, fh)
    out.blit(img, rect(0, 0, w, h), rect(0, 0, fw, fh))
    return out
