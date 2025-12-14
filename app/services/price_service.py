import tempfile
from pathlib import Path
from typing import Dict, List

import pandas as pd

from . import sftp_service


def summarise_dataset(uploaded_file) -> Dict[str, str]:
    suffix = Path(uploaded_file.filename).suffix.lower()
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        uploaded_file.save(tmp.name)
        tmp_path = Path(tmp.name)

    records = 0
    columns: List[str] = []
    preview: List[Dict[str, str]] = []

    if suffix in {".xlsx", ".xls"}:
        frame = pd.read_excel(tmp_path)
    elif suffix in {".csv", ".txt"}:
        frame = pd.read_csv(tmp_path)
    else:
        raise ValueError("Поддерживаются только CSV и Excel")

    records = len(frame)
    columns = list(frame.columns)
    preview = frame.head(5).to_dict(orient="records")

    # Convert to CSV for upload to SFTP when requested
    normalized_path = tmp_path.with_suffix(".csv")
    frame.to_csv(normalized_path, index=False)
    return {
        "temp_path": str(normalized_path),
        "uploaded_name": Path(uploaded_file.filename).stem + ".csv",
        "records": records,
        "columns": columns,
        "preview": preview,
    }


def push_generated(csv_path: str, config: Dict[str, str]) -> str:
    return sftp_service.upload_file(config, Path(csv_path), config.get("remote_path", "/"))
