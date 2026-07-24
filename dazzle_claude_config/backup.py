"""Pre-apply backups: nothing in the live tree is overwritten or removed
without a timestamped copy first (acceptance check A3)."""
from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path


class BackupSession:
    """One timestamped backup directory per apply run, created lazily."""

    def __init__(self, root: Path, label: str = "apply"):
        stamp = datetime.now().strftime("%Y-%m-%d__%H-%M-%S")
        self.dir = Path(root) / f"{stamp}__{label}"
        self.saved: list[str] = []

    def save(self, src: Path, rel: str) -> Path:
        """Copy src into the backup dir under its relative path."""
        dest = self.dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        self.saved.append(rel)
        return dest

    def stage_removal(self, src: Path, rel: str) -> Path:
        """Move src into the backup dir (staged removal -- never delete in place)."""
        dest = self.dir / "_removed" / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dest))
        self.saved.append(f"_removed/{rel}")
        return dest
