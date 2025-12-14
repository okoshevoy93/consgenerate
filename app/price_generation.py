import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .sftp_client import SFTPClient, SFTPConfig


@dataclass
class PriceRunResult:
    downloaded: Optional[Path]
    uploaded_remote: Optional[str]
    notes: str


def run_price_pipeline(config: SFTPConfig, password: str, *, download_file: str | None, upload_file: str | None, local_override: Optional[Path], notes: str) -> PriceRunResult:
    downloaded_path: Optional[Path] = None
    uploaded_remote: Optional[str] = None

    with SFTPClient(config) as client:
        if download_file:
            destination = Path(tempfile.gettempdir()) / download_file
            downloaded_path = client.download(download_file, destination)
        target_file: Optional[Path] = None
        if local_override:
            target_file = local_override
        elif upload_file and downloaded_path:
            target_file = downloaded_path
        if target_file:
            uploaded_remote = client.upload(target_file, config.media_path or config.remote_path)
    return PriceRunResult(downloaded=downloaded_path, uploaded_remote=uploaded_remote, notes=notes)
