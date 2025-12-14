from pathlib import Path
from typing import Dict, Optional

from flask import Blueprint, flash, redirect, render_template, request, url_for

from .automation import AutomationResult, filter_cities, run_automation
from .price_generation import PriceRunResult, run_price_pipeline
from .sftp_client import SFTPConfig
from .storage import EncryptedConfigStore

main_bp = Blueprint('main', __name__)

CITY_PRESETS = [
    "Москва",
    "Санкт-Петербург",
    "Новосибирск",
    "Екатеринбург",
    "Алматы",
    "Минск",
]

CONFIG_LABELS = {
    "sftp_price": "SFTP для прайсов",
    "sftp_auto": "SFTP для автоматизации",
}

LAST_ACTIVITY: Dict[str, str] = {}
LOADED_CONFIGS: Dict[str, Optional[SFTPConfig]] = {"sftp_price": None, "sftp_auto": None}


def _parse_config_form(data: dict) -> dict:
    extra_raw = data.get("extra") or ""
    extra = None
    if extra_raw:
        try:
            import json

            extra = json.loads(extra_raw)
        except Exception:
            extra = {"raw": extra_raw}
    return {
        "sftp_host": data.get("sftp_host", ""),
        "sftp_port": int(data.get("sftp_port", 22)),
        "sftp_user": data.get("sftp_user", ""),
        "sftp_pass": data.get("sftp_pass", ""),
        "remote_path": data.get("remote_path", ""),
        "media_path": data.get("media_path") or None,
        "extra": extra,
    }


@main_bp.route('/')
def home():
    return render_template('home.html', title='Главная')


@main_bp.route('/config/<config_name>', methods=['GET', 'POST'])
def config_page(config_name: str):
    store = EncryptedConfigStore(config_name)
    label = CONFIG_LABELS.get(config_name, config_name)
    values = {"sftp_port": 22}
    if request.method == 'POST':
        password = request.form.get('password', '')
        payload = _parse_config_form(request.form)
        store.save(payload, password)
        flash('Конфигурация сохранена и зашифрована', 'success')
        return redirect(url_for('config_page', config_name=config_name))
    if store.exists():
        values = {"sftp_port": 22}
    return render_template('config.html', config_label=label, values=values)


@main_bp.route('/config/<config_name>/load', methods=['POST'])
def load_config(config_name: str):
    store = EncryptedConfigStore(config_name)
    password = request.form.get('password', '')
    data = store.load(password)
    if not data:
        flash('Файл конфигурации не найден или пароль неверный', 'danger')
    else:
        LOADED_CONFIGS[config_name] = SFTPConfig(**data)
        flash('Конфигурация загружена', 'success')
    target = 'price_generation' if config_name == 'sftp_price' else 'automation'
    return redirect(url_for(target))


@main_bp.route('/price-generation')
def price_generation():
    return render_template('price_generation.html', config=LOADED_CONFIGS.get('sftp_price'), activity=LAST_ACTIVITY.get('price'))


@main_bp.route('/price-generation/run', methods=['POST'])
def run_price_generation():
    password = request.form.get('password', '')
    download_file = request.form.get('download_file') or None
    upload_file = request.form.get('upload_file') or None
    notes = request.form.get('notes', '')
    config = LOADED_CONFIGS.get('sftp_price')
    if not config:
        flash('Сначала загрузите конфигурацию', 'warning')
        return redirect(url_for('price_generation'))

    local_file = None
    file = request.files.get('local_file')
    if file and file.filename:
        temp_path = Path('/tmp') / file.filename
        file.save(temp_path)
        local_file = temp_path

    try:
        result: PriceRunResult = run_price_pipeline(config, password, download_file=download_file, upload_file=upload_file, local_override=local_file, notes=notes)
        message = [
            f"Задача: {notes}",
            f"Скачано: {result.downloaded}" if result.downloaded else "Скачивание не выполнялось",
            f"Загружено: {result.uploaded_remote}" if result.uploaded_remote else "Файл не загружен",
        ]
        LAST_ACTIVITY['price'] = "\n".join(message)
        flash('Процесс завершён', 'success')
    except Exception as exc:  # noqa: BLE001
        LAST_ACTIVITY['price'] = f"Ошибка: {exc}"
        flash('Ошибка при выполнении: %s' % exc, 'danger')
    return redirect(url_for('price_generation'))


@main_bp.route('/automation')
def automation():
    query = request.args.get('query')
    cities = filter_cities(CITY_PRESETS, query)
    return render_template('automation.html', cities=cities, query=query, activity=LAST_ACTIVITY.get('automation'))


@main_bp.route('/automation/run', methods=['POST'])
def run_automation_action():
    password = request.form.get('password', '')
    city = request.form.get('city') or CITY_PRESETS[0]
    notes = request.form.get('notes', '')
    config = LOADED_CONFIGS.get('sftp_auto')
    if not config:
        flash('Сначала загрузите конфигурацию', 'warning')
        return redirect(url_for('automation'))

    scenario_path: Optional[Path] = None
    file = request.files.get('scenario')
    if file and file.filename:
        scenario_path = Path('/tmp') / file.filename
        file.save(scenario_path)

    try:
        result: AutomationResult = run_automation(config, city=city, scenario_file=scenario_path, notes=notes)
        message = [
            f"Город: {result.city}",
            f"Сценарий: {result.scenario_preview[:200] + '…' if result.scenario_preview else 'нет данных'}",
            f"Загружено: {result.uploaded_remote}" if result.uploaded_remote else "Сценарий не загружен",
            f"Комментарий: {result.notes}" if result.notes else "",
        ]
        LAST_ACTIVITY['automation'] = "\n".join(filter(None, message))
        flash('Сценарий отправлен', 'success')
    except Exception as exc:  # noqa: BLE001
        LAST_ACTIVITY['automation'] = f"Ошибка: {exc}"
        flash('Ошибка при выполнении: %s' % exc, 'danger')
    return redirect(url_for('automation'))
