from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import List, Optional

import pandas as pd

from app.config import settings


def rows_to_excel(
    rows: List[dict],
    filename: Optional[str] = None,
    output_dir: Optional[Path] = None,
) -> Path:
    out_dir = output_dir or settings.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    if not filename:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"scrape_{stamp}.xlsx"
    if not filename.endswith(".xlsx"):
        filename = f"{filename}.xlsx"
    path = out_dir / filename

    if not rows:
        df = pd.DataFrame([{"note": "No rows scraped"}])
    else:
        # stable column order: non-_ first, then meta
        keys = []
        for row in rows:
            for k in row.keys():
                if k not in keys:
                    keys.append(k)
        primary = [k for k in keys if not str(k).startswith("_")]
        meta = [k for k in keys if str(k).startswith("_")]
        df = pd.DataFrame(rows)[primary + meta]

    df.to_excel(path, index=False, engine="openpyxl")
    return path
