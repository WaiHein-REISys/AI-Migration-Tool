"""
wizard.writer — File I/O with dry-run support
==============================================
WizardWriter centralises all file-system writes for the setup wizard.
Every write is either executed (normal mode) or logged only (dry_run=True),
so users can preview exactly what will be created before committing.

Usage
-----
    from wizard.writer import WizardWriter

    writer = WizardWriter(dry_run=True)
    writer.write(Path("prompts/plan_system_mytarget.txt"), content)
    writer.patch_json(Path("config/skillset-config.json"), {"key": "value"})
    writer.summary()
"""

import json
import sys
from pathlib import Path


def _safe_print(text: str) -> None:
    """Print text replacing un-encodable chars (Windows CP1252 safety)."""
    enc = sys.stdout.encoding or "utf-8"
    print(text.encode(enc, errors="replace").decode(enc, errors="replace"))


class WizardWriter:
    """
    Centralised file writer for the setup wizard.

    Tracks every write and skip so a final summary can be printed.
    In ``dry_run`` mode no bytes are ever written to disk.

    Parameters
    ----------
    dry_run : bool
        When True, log intended writes but do nothing on disk.
    """

    def __init__(self, dry_run: bool = False) -> None:
        self.dry_run = dry_run
        self._written: list[str] = []
        self._skipped: list[str] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def write(
        self,
        path: Path,
        content: str,
        overwrite: bool = False,
    ) -> bool:
        """
        Write *content* to *path*.

        Parameters
        ----------
        path : Path
            Destination file path (absolute or relative).
        content : str
            UTF-8 text content to write.
        overwrite : bool
            When False (default), skip writing if *path* already exists.

        Returns
        -------
        bool
            True if the file was written (or would be in dry-run mode).
        """
        if path.exists() and not overwrite:
            self._skipped.append(str(path))
            _safe_print(f"  [SKIP]    {path}  (already exists; use --overwrite to replace)")
            return False

        if self.dry_run:
            _safe_print(f"  [DRY-RUN] {path}")
            self._written.append(str(path))
            return True

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        _safe_print(f"  [OK]      {path}")
        self._written.append(str(path))
        return True

    def patch_json(
        self,
        path: Path,
        updates: dict,
        overwrite_keys: bool = False,
    ) -> None:
        """
        Merge *updates* into an existing JSON file.

        Only top-level keys are merged. If *path* does not exist, the file is
        created with the content of *updates*. Existing keys are left untouched
        unless *overwrite_keys* is True.

        Parameters
        ----------
        path : Path
            Path to the target JSON file.
        updates : dict
            Key-value pairs to merge in.
        overwrite_keys : bool
            Replace existing top-level keys when True (default False).
        """
        if not path.exists():
            self.write(path, json.dumps(updates, indent=2, ensure_ascii=False))
            return

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            data = {}

        new_keys = [k for k in updates if k not in data or overwrite_keys]
        if not new_keys:
            self._skipped.append(str(path))
            _safe_print(f"  [SKIP]    {path}  (all keys already present)")
            return

        if self.dry_run:
            _safe_print(
                f"  [DRY-RUN] {path}  (would add/update keys: {new_keys})"
            )
            self._written.append(str(path))
            return

        for k in new_keys:
            data[k] = updates[k]

        path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        _safe_print(f"  [OK]      {path}  (added/updated keys: {new_keys})")
        self._written.append(str(path))

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    def summary(self) -> None:
        """Print a concise summary of written and skipped files."""
        print()
        print(f"  Written: {len(self._written)} file(s)")
        print(f"  Skipped: {len(self._skipped)} file(s)  (already existed)")
        if self.dry_run:
            print()
            print("  [DRY-RUN] No files were actually written.")
            print("  Remove --dry-run to apply changes.")

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def written_files(self) -> list[str]:
        """List of paths that were written (or would be in dry-run mode)."""
        return list(self._written)

    @property
    def skipped_files(self) -> list[str]:
        """List of paths that were skipped because they already existed."""
        return list(self._skipped)
