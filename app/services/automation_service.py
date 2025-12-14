from pathlib import Path
from typing import Dict, List

from . import sftp_service

AVAILABLE_CITIES = [
    "Москва",
    "Санкт-Петербург",
    "Алматы",
    "Минск",
    "Новосибирск",
    "Екатеринбург",
    "Казань",
    "Нижний Новгород",
    "Челябинск",
    "Самара",
]


def push_mapping(file_path: Path, config: Dict[str, str]) -> str:
    remote_dir = config.get("mapping_path", "/")
    return sftp_service.upload_file(config, file_path, remote_dir)


def list_cities(query: str = "") -> List[str]:
    normalized = query.strip().lower()
    if not normalized:
        return AVAILABLE_CITIES
    return [city for city in AVAILABLE_CITIES if normalized in city.lower()]
