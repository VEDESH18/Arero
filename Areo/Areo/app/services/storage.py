"""File storage for generated certificates.

Kept behind a tiny class so local disk could be swapped for S3/GCS without
touching the worker or the API. The DB stores paths *relative* to the storage
root, so the root directory can be moved via configuration.
"""

import os
from pathlib import Path


class LocalStorage:
    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def relative_path_for(self, job_id: str, certificate_id: str) -> str:
        return f"{job_id}/{certificate_id}.pdf"

    def absolute_path(self, relative_path: str) -> Path:
        path = (self.root / relative_path).resolve()
        if not path.is_relative_to(self.root):  # defence against path traversal
            raise ValueError("Path escapes storage root")
        return path

    def temp_path_for(self, relative_path: str) -> Path:
        """Where to write before the atomic rename; same directory => same filesystem."""
        final = self.absolute_path(relative_path)
        final.parent.mkdir(parents=True, exist_ok=True)
        return final.with_suffix(".pdf.tmp")

    def commit(self, relative_path: str) -> None:
        """Atomically move the finished temp file into place."""
        final = self.absolute_path(relative_path)
        os.replace(final.with_suffix(".pdf.tmp"), final)

    def discard_temp(self, relative_path: str) -> None:
        self.absolute_path(relative_path).with_suffix(".pdf.tmp").unlink(missing_ok=True)

    def exists(self, relative_path: str) -> bool:
        return self.absolute_path(relative_path).is_file()
