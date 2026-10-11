"""Safe storage of uploaded images (custom template logos and signatures).

Uploads are never trusted:
- The size is capped before decoding.
- Pillow decodes the actual bytes; the file name, extension and Content-Type
  header are ignored. Only real PNG and JPEG images are accepted.
- Dimensions are checked before the pixels are loaded (no decompression bombs),
  large images are downscaled, and everything is re-encoded as a fresh PNG, which
  drops metadata and anything appended to the file.
- Files are saved under random names in UPLOAD_DIR; the user's file name is never
  used on disk, and stored names are validated before any path is built from them.
"""

import io
import os
import re
import secrets
from pathlib import Path

from PIL import Image, UnidentifiedImageError

ALLOWED_FORMATS = {"PNG", "JPEG"}
_STORED_NAME = re.compile(r"^[0-9a-f]{32}\.png$")


class ImageRejectedError(ValueError):
    """The upload is not an acceptable image (422)."""


class ImageTooLargeError(ImageRejectedError):
    """The upload exceeds the size limit (413)."""


class ImageStore:
    def __init__(self, root: Path, max_bytes: int, max_dimension: int, downscale_to: int) -> None:
        self.root = Path(root).resolve()
        self.max_bytes = max_bytes
        self.max_dimension = max_dimension
        self.downscale_to = downscale_to

    def validate_and_save(self, content: bytes, label: str) -> str:
        """Check `content` is a real PNG/JPEG within limits, store a cleaned copy, return its stored name."""
        if len(content) > self.max_bytes:
            raise ImageTooLargeError(f"{label}: file is larger than {self.max_bytes // 1024} KB")
        if not content:
            raise ImageRejectedError(f"{label}: file is empty")

        try:
            with Image.open(io.BytesIO(content)) as probe:
                image_format = probe.format
                width, height = probe.size
                if image_format not in ALLOWED_FORMATS:
                    raise ImageRejectedError(f"{label}: only PNG or JPEG images are accepted (got {image_format})")
                if width > self.max_dimension or height > self.max_dimension:
                    raise ImageRejectedError(
                        f"{label}: image is {width}x{height}px; the maximum is {self.max_dimension}px per side"
                    )
                probe.verify()  # structural check (CRCs, truncation)
            with Image.open(io.BytesIO(content)) as image:
                image.load()
                cleaned = self._normalise(image)
        except ImageRejectedError:
            raise
        except (UnidentifiedImageError, Image.DecompressionBombError, OSError, SyntaxError, ValueError) as exc:
            raise ImageRejectedError(f"{label}: not a valid PNG or JPEG image") from exc

        name = f"{secrets.token_hex(16)}.png"
        self.root.mkdir(parents=True, exist_ok=True)
        final = self.path(name)
        temp = final.with_suffix(".tmp")
        cleaned.save(temp, format="PNG", optimize=True)
        os.replace(temp, final)
        return name

    def path(self, stored_name: str) -> Path:
        """Absolute path of a stored image. Rejects anything that is not one of our generated names."""
        if not _STORED_NAME.fullmatch(stored_name):
            raise ValueError("Invalid stored image name")
        path = (self.root / stored_name).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("Path escapes upload directory")
        return path

    def delete(self, stored_name: str | None) -> None:
        if stored_name:
            self.path(stored_name).unlink(missing_ok=True)

    def _normalise(self, image: Image.Image) -> Image.Image:
        has_alpha = image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info)
        image = image.convert("RGBA" if has_alpha else "RGB")
        image.thumbnail((self.downscale_to, self.downscale_to))  # keeps aspect ratio; never enlarges
        return image
