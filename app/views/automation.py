from pathlib import Path
from typing import Dict
from types import SimpleNamespace

from flask import Blueprint, flash, redirect, render_template, request, url_for

from ..services.config_manager import AUTOMATION_CONFIG
from ..services import automation_service, sftp_service

automation_bp = Blueprint("automation", __name__)


def _build_config(form) -> Dict[str, str]:
    return {
        "host": form.get("host", ""),
        "port": form.get("port", 22),
        "username": form.get("username", ""),
        "password": form.get("password", ""),
        "remote_path": form.get("remote_path", "/"),
        "mapping_path": form.get("mapping_path", "/"),
    }


@automation_bp.route("/", methods=["GET", "POST"])
def dashboard():
    loaded_config = None
    cities = automation_service.list_cities()
    if request.method == "POST" and request.form.get("action") == "load_config":
        password = request.form.get("secret", "")
        raw = AUTOMATION_CONFIG.load(password)
        if raw:
            loaded_config = SimpleNamespace(**raw)
            flash("Конфигурация загружена", "success")
        else:
            flash("Не удалось загрузить конфигурацию", "danger")
    return render_template("automation.html", config=loaded_config, cities=cities)


@automation_bp.route("/save", methods=["POST"])
def save_config():
    password = request.form.get("secret", "")
    AUTOMATION_CONFIG.save(password, _build_config(request.form))
    flash("Настройки автоматики сохранены", "success")
    return redirect(url_for("automation.dashboard"))


@automation_bp.route("/test", methods=["POST"])
def test_connection():
    password = request.form.get("secret", "")
    saved = AUTOMATION_CONFIG.load(password)
    if not saved:
        flash("Сначала загрузите конфигурацию", "warning")
        return redirect(url_for("automation.dashboard"))
    if sftp_service.test_connection(saved):
        flash("SFTP подключение активно", "success")
    else:
        flash("Не удалось подключиться", "danger")
    return redirect(url_for("automation.dashboard"))


@automation_bp.route("/mapping", methods=["POST"])
def upload_mapping():
    password = request.form.get("secret", "")
    saved = AUTOMATION_CONFIG.load(password)
    if not saved:
        flash("Загрузите конфигурацию", "warning")
        return redirect(url_for("automation.dashboard"))

    file = request.files.get("mapping")
    if not file:
        flash("Выберите файл отображений", "danger")
        return redirect(url_for("automation.dashboard"))

    temp_path = Path("/tmp") / file.filename
    file.save(temp_path)
    remote_path = automation_service.push_mapping(temp_path, saved)
    flash(f"Карта отправлена в {remote_path}", "success")
    return redirect(url_for("automation.dashboard"))


@automation_bp.route("/cities", methods=["GET"])
def search_cities():
    query = request.args.get("q", "")
    matches = automation_service.list_cities(query)
    return {"results": matches}
