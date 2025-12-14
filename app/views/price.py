from pathlib import Path
from typing import Dict
from types import SimpleNamespace

from flask import Blueprint, flash, redirect, render_template, request, send_file, url_for

from ..services.config_manager import PRICE_CONFIG
from ..services import price_service, sftp_service

price_bp = Blueprint("price", __name__)


def _build_config(form) -> Dict[str, str]:
    return {
        "host": form.get("host", ""),
        "port": form.get("port", 22),
        "username": form.get("username", ""),
        "password": form.get("password", ""),
        "remote_path": form.get("remote_path", "/"),
        "photo_path": form.get("photo_path", ""),
    }


@price_bp.route("/", methods=["GET", "POST"])
def dashboard():
    loaded_config = None
    summary = None

    if request.method == "POST" and request.form.get("action") == "load_config":
        password = request.form.get("secret", "")
        raw_config = PRICE_CONFIG.load(password)
        if raw_config:
            loaded_config = SimpleNamespace(**raw_config)
            flash("Конфигурация успешно загружена", "success")
        else:
            flash("Не удалось прочитать конфигурацию", "danger")

    return render_template("price.html", config=loaded_config, summary=summary)


@price_bp.route("/save", methods=["POST"])
def save_config():
    password = request.form.get("secret", "")
    config_payload = _build_config(request.form)
    PRICE_CONFIG.save(password, config_payload)
    flash("Конфигурация сохранена в зашифрованном виде", "success")
    return redirect(url_for("price.dashboard"))


@price_bp.route("/test", methods=["POST"])
def test_connection():
    password = request.form.get("secret", "")
    raw_config = PRICE_CONFIG.load(password)
    if not raw_config:
        flash("Сначала загрузите конфигурацию корректным паролем", "warning")
        return redirect(url_for("price.dashboard"))
    saved = SimpleNamespace(**raw_config)
    if sftp_service.test_connection(saved):
        flash("SFTP подключение успешно", "success")
    else:
        flash("Не удалось подключиться к SFTP", "danger")
    return redirect(url_for("price.dashboard"))


@price_bp.route("/process", methods=["POST"])
def process_file():
    password = request.form.get("secret", "")
    saved = PRICE_CONFIG.load(password)
    if not saved:
        flash("Сначала загрузите конфигурацию корректным паролем", "warning")
        return redirect(url_for("price.dashboard"))

    file = request.files.get("dataset")
    if not file:
        flash("Загрузите файл прайса для обработки", "danger")
        return redirect(url_for("price.dashboard"))

    try:
        summary = price_service.summarise_dataset(file)
    except Exception as exc:
        flash(f"Ошибка обработки файла: {exc}", "danger")
        return redirect(url_for("price.dashboard"))

    return render_template(
        "price.html",
        config=saved,
        summary=summary,
        secret=password,
    )


@price_bp.route("/upload", methods=["POST"])
def upload_generated():
    password = request.form.get("secret", "")
    raw_config = PRICE_CONFIG.load(password)
    csv_path = request.form.get("csv_path")
    if not raw_config or not csv_path:
        flash("Отсутствуют данные для отправки", "warning")
        return redirect(url_for("price.dashboard"))
    saved = SimpleNamespace(**raw_config)
    remote_path = price_service.push_generated(csv_path, vars(saved))
    flash(f"Файл отправлен: {remote_path}", "success")
    return send_file(csv_path, as_attachment=True, download_name=Path(csv_path).name)
