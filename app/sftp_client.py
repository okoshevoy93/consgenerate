import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import paramiko


@dataclass
class SFTPConfig:
    sftp_host: str
    sftp_port: int
    sftp_user: str
    sftp_pass: str
    remote_path: str
    media_path: str | None = None
    extra: dict | None = None


class SFTPClient:
    def __init__(self, config: SFTPConfig):
        self.config = config
        self._transport: Optional[paramiko.Transport] = None
        self._client: Optional[paramiko.SFTPClient] = None

    def __enter__(self):
        self._transport = paramiko.Transport((self.config.sftp_host, self.config.sftp_port))
        self._transport.connect(username=self.config.sftp_user, password=self.config.sftp_pass)
        self._client = paramiko.SFTPClient.from_transport(self._transport)
        return self

    def __exit__(self, exc_type, exc, tb):
        if self._client:
            self._client.close()
        if self._transport:
            self._transport.close()

    def download(self, remote_name: str, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        remote = os.path.join(self.config.remote_path, remote_name).replace("\\", "/")
        self._client.get(remote, str(destination))
        return destination

    def read_text(self, remote_path: str, encoding: str = "utf-8") -> str:
        remote = remote_path.replace("\\", "/")
        with self._client.file(remote, mode="r") as handle:  # type: ignore[attr-defined]
            return handle.read().decode(encoding)

    def write_text(self, remote_path: str, content: str, encoding: str = "utf-8") -> str:
        remote = remote_path.replace("\\", "/")
        folder = os.path.dirname(remote) or self.config.remote_path
        folder = folder.replace("\\", "/")
        try:
            self._client.stat(folder)
        except IOError:
            self._ensure_remote_dir(folder)
        with self._client.file(remote, mode="w") as handle:  # type: ignore[attr-defined]
            handle.write(content.encode(encoding))
        return remote

    def upload(self, local_file: Path, remote_folder: Optional[str] = None) -> str:
        folder = remote_folder or self.config.remote_path
        folder = folder.replace("\\", "/")
        remote = os.path.join(folder, local_file.name).replace("\\", "/")
        try:
            self._client.stat(folder)
        except IOError:
            self._ensure_remote_dir(folder)
        self._client.put(str(local_file), remote)
        return remote

    def _ensure_remote_dir(self, path: str) -> None:
        parts = path.strip("/").split("/")
        current = ""
        for part in parts:
            current = f"{current}/{part}" if current else f"/{part}"
            try:
                self._client.stat(current)
            except IOError:
                self._client.mkdir(current)
