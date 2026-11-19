"""Back up generated files before an explicitly authorized overwrite."""
from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path
import uuid


def backup_files(paths, root:Path)->Path|None:
    """Copy existing regular files before any replacement; fail closed on errors."""
    existing=[]
    for path in paths:
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise ValueError(f'Expected a regular generated file: {path}')
        if path.exists():existing.append(path)
    if not existing:return None
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    destination=root/'.dlb_backups'/f'{stamp}.{uuid.uuid4().hex}'
    destination.mkdir(parents=True)
    for path in existing:
        target=destination/path.relative_to(root)
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(path,target)
    print(f'BACKUP generated files -> {destination}')
    return destination
