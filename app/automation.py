import json
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional

from .sftp_client import SFTPClient, SFTPConfig


@dataclass
class AutomationResult:
    city: str
    uploaded_remote: Optional[str]
    notes: str
    scenario_preview: str


def filter_cities(all_cities: Iterable[str], query: str | None) -> List[str]:
    if not query:
        return list(all_cities)
    q = query.lower()
    return [city for city in all_cities if q in city.lower()]


def run_automation(config: SFTPConfig, *, city: str, scenario_file: Optional[Path], notes: str) -> AutomationResult:
    uploaded: Optional[str] = None
    preview = ""
    if scenario_file and scenario_file.exists():
        try:
            preview = json.dumps(json.loads(scenario_file.read_text(encoding="utf-8")), ensure_ascii=False, indent=2)[:4000]
        except json.JSONDecodeError:
            preview = scenario_file.read_text(encoding="utf-8")[:4000]
    with SFTPClient(config) as client:
        if scenario_file:
            remote_folder = config.media_path or config.remote_path
            uploaded = client.upload(scenario_file, remote_folder)
    return AutomationResult(city=city, uploaded_remote=uploaded, notes=notes, scenario_preview=preview)
