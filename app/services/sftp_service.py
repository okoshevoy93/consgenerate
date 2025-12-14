import os
from contextlib import contextmanager
from pathlib import Path
from typing import Dict

import paramiko


def build_transport(config: Dict[str, str]) -> paramiko.Transport:
    transport = paramiko.Transport((config["host"], int(config.get("port", 22))))
    transport.connect(username=config.get("username"), password=config.get("password"))
    return transport


@contextmanager
def sftp_client(config: Dict[str, str]):
    transport = build_transport(config)
    client = paramiko.SFTPClient.from_transport(transport)
    try:
        yield client
    finally:
        client.close()
        transport.close()


def test_connection(config: Dict[str, str]) -> bool:
    try:
        with sftp_client(config) as client:
            client.listdir(".")
        return True
    except Exception:
        return False


def upload_file(config: Dict[str, str], local_path: Path, remote_folder: str) -> str:
    remote_path = os.path.join(remote_folder, local_path.name).replace("\\", "/")
    with sftp_client(config) as client:
        try:
            client.stat(remote_folder)
        except IOError:
            _ensure_remote_dir(client, remote_folder)
        client.put(str(local_path), remote_path)
    return remote_path


def download_file(config: Dict[str, str], remote_path: str, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with sftp_client(config) as client:
        client.get(remote_path, str(destination))
    return destination


def _ensure_remote_dir(client: paramiko.SFTPClient, remote_dir: str) -> None:
    # create nested directories as needed
    parts = remote_dir.strip("/").split("/")
    current = ""
    for part in parts:
        current = f"{current}/{part}" if current else f"/{part}"
        try:
            client.stat(current)
        except IOError:
            client.mkdir(current)
