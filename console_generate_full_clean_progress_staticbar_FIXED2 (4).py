import argparse
import pandas as pd
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from pathlib import Path
import json
import sys
import xml.etree.ElementTree as ET
import csv
import paramiko
import os
import re
import transliterate
import requests
import base64
from tqdm import tqdm
import time
from rich.progress import Progress, BarColumn, TextColumn, TransferSpeedColumn, TimeRemainingColumn, SpinnerColumn
from rich.console import Console
from rich.table import Table
import shutil
import subprocess
import tempfile
from googleapiclient.discovery import build
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
import pickle
import logging
import urllib.parse
from datetime import datetime

# --- Pre-detection of download method for Yandex & other URLs ---
from enum import Enum

class DownloadMethod(Enum):
    DIRECT = "direct"
    YANDEX_IMAGE = "yandex_image"   # https://disk.yandex.ru/i/<id>
    YANDEX_PUBLIC = "yandex_public" # https://disk.yandex.ru/d/<id>
    SHORT_CLCK = "short_clck"
    UNKNOWN = "unknown"

YANDEX_HOST_RE = re.compile(r'https://disk\.yandex\.[a-z]+/(i|d)/([^/?#]+)', re.IGNORECASE)

def classify_url_kind(url: str) -> "DownloadMethod":
    if not url or not isinstance(url, str):
        return DownloadMethod.UNKNOWN
    u = url.strip()
    if 'clck.ru' in u:
        return DownloadMethod.SHORT_CLCK
    m = YANDEX_HOST_RE.match(u)
    if m:
        return DownloadMethod.YANDEX_IMAGE if m.group(1).lower() == 'i' else DownloadMethod.YANDEX_PUBLIC
    if u.startswith('http'):
        return DownloadMethod.DIRECT
    return DownloadMethod.UNKNOWN

def normalize_yandex_public_key_and_path(url: str):
    public_key_match = re.search(r'https://disk\.yandex\.[a-z]+/d/[^\s]+', url)
    public_key = public_key_match.group(0) if public_key_match else url
    path_match = re.search(r'[\?&]path=([^&]+)', url)
    path = urllib.parse.unquote(path_match.group(1)) if path_match else None
    return public_key, path

def resolve_download_link(original_url: str, url_cache: dict):
    """Return (download_url, DownloadMethod)."""
    if not original_url or not original_url.startswith('http'):
        return None, DownloadMethod.UNKNOWN

    if original_url in url_cache:
        return url_cache[original_url], classify_url_kind(original_url)

    method = classify_url_kind(original_url)

    # Expand clck.ru first
    if method == DownloadMethod.SHORT_CLCK:
        expanded = expand_clck_link(original_url)
        method = classify_url_kind(expanded)
        url_cache[original_url] = expanded
        original_url = expanded

    if method == DownloadMethod.YANDEX_IMAGE:
        download_url = f"https://getfile.dokpub.com/yandex/get/{original_url}"
        url_cache[original_url] = download_url
        return download_url, method

    if method == DownloadMethod.YANDEX_PUBLIC:
        public_key, path = normalize_yandex_public_key_and_path(original_url)
        download_url = get_yandex_download_link(public_key, path)
        if not download_url:
            normalized_url = re.sub(r'(&?clckid=\w+)?$', '', original_url)
            download_url = f"https://getfile.dokpub.com/yandex/get/{normalized_url}"
        url_cache[original_url] = download_url
        return download_url, method

    if method == DownloadMethod.DIRECT:
        url_cache[original_url] = original_url
        return original_url, method

    return None, method

# Подавление FutureWarning для .fillna()
pd.set_option('future.no_silent_downcasting', True)

# Инициализация консоли для rich
console = Console()

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    filename='price_generation.log',
    filemode='a'
)
logger = logging.getLogger(__name__)



# Verbosity flag: hide per-link method logs by default
VERBOSE_METHOD_LOG = False
# Константы для API iikoCloud
API_KEY_RF = "ВАШ_КЛЮЧ_ДЛЯ_РФ"  # Замените на действующий ключ для РФ
API_KEY_KZ = "ВАШ_КЛЮЧ_ДЛЯ_КЗ"  # Замените на действующий ключ для КЗ
API_KEY_RB = "ВАШ_КЛЮЧ_ДЛЯ_РБ"  # Замените на действующий ключ для РБ
BASE_URL = "https://api-ru.iiko.services"
PRICE_CATEGORY_ID = "00000000-0000-0000-0000-000000000000"

# Параметры подключения к SFTP
SFTP_HOST = "mesh.chicko.me"
SFTP_PORT = 22
SFTP_USERNAME = "test"
SFTP_PASSWORD = "test"
REMOTE_PATH = "/var/www/html/auth_menu/"
PHOTO_BASE_PATH = "/var/www/html/img/TEST"  # Для РФ
PHOTO_BASE_PATH_KZ = "/var/www/html/img/NEW_PHOTO/KZ"  # Для КЗ
PHOTO_BASE_PATH_RB = "/var/www/html/img/NEW_PHOTO/RB"  # Для РБ

# Определение пути к файлам для временного хранения
def get_local_file_path(file_name):
    try:
        local_path = Path.home() / 'auth_menu'
        local_path.mkdir(parents=True, exist_ok=True)
    except PermissionError as e:
        console.print(f"[bold red]Ошибка доступа при создании директории: {e}[/bold red]")
        sys.exit(1)
    return local_path / file_name

# Функция для загрузки файла с SFTP
def download_from_sftp(file_name):
    local_file_path = get_local_file_path(file_name)
    try:
        transport = paramiko.Transport((SFTP_HOST, SFTP_PORT))
        transport.connect(username=SFTP_USERNAME, password=SFTP_PASSWORD)
        sftp = paramiko.SFTPClient.from_transport(transport)
        remote_file_path = os.path.join(REMOTE_PATH, file_name).replace('\\', '/')
        
        console.print(f"[bold blue]Попытка скачать файл {file_name} с пути {remote_file_path}[/bold blue]")
        sftp.get(remote_file_path, str(local_file_path))
        sftp.close()
        console.print(f"[bold green]Файл {file_name} успешно скачан с SFTP.[/bold green]")
        return local_file_path
    except FileNotFoundError as e:
        console.print(f"[bold red]Ошибка: Файл {file_name} не найден на SFTP по пути {remote_file_path}: {e}[/bold red]")
        action = input("Хотите продолжить без этого файла? (да/нет): ").lower()
        if action == 'да':
            return None
        else:
            sys.exit(1)
    except Exception as e:
        console.print(f"[bold red]Ошибка при подключении к SFTP или скачивании файла {file_name}: {e}[/bold red]")
        sys.exit(1)

# Функция для загрузки файла на SFTP с прогресс-баром
def upload_file_to_sftp(local_file_path, remote_folder, sftp, progress=None, task=None):
    remote_file_path = os.path.join(remote_folder, local_file_path.name).replace('\\', '/')
    file_size = os.path.getsize(local_file_path)

    console.print(f"[bold blue]Uploading file:[/bold blue] {local_file_path} -> {remote_file_path} ({file_size} bytes)")
    logger.info(f"Загрузка файла: {local_file_path} -> {remote_file_path}")

    try:
        directory = os.path.dirname(remote_file_path)
        try:
            sftp.stat(directory)
            console.print(f"[bold green]Директория {directory} существует на SFTP.[/bold green]")
        except IOError:
            console.print(f"[bold yellow]Директория {directory} не существует. Создаём её...[/bold yellow]")
            ensure_remote_directory_exists(sftp, directory)

        try:
            sftp.chmod(directory, 0o775)
            console.print(f"[bold green]Установлены права 775 для директории {directory}.[/bold green]")
        except Exception as e:
            console.print(f"[bold yellow]Не удалось установить права для {directory}: {e}. Проверяйте права пользователя.[/bold yellow]")
            logger.warning(f"Не удалось установить права для {directory}: {str(e)}")

        with open(local_file_path, "rb") as file:
            if progress and task:
                sftp.putfo(file, remote_file_path, callback=lambda sent, total: progress.update(task, advance=sent))
            else:
                sftp.putfo(file, remote_file_path)
        console.print(f"[bold green]Файл {local_file_path.name} успешно загружен на SFTP в {remote_file_path}[/bold green]")
        logger.info(f"Файл {local_file_path.name} успешно загружен на SFTP")
    except Exception as e:
        console.print(f"[bold red]Ошибка при загрузке файла {local_file_path} на SFTP: {e}[/bold red]")
        logger.error(f"Ошибка при загрузке файла {local_file_path}: {str(e)}")
        raise

# Функция для проверки и создания удалённой директории на SFTP
def ensure_remote_directory_exists(sftp, remote_directory):
    try:
        base_path = "/var/www/html"
        if not remote_directory.startswith(base_path):
            remote_directory = os.path.join(base_path, remote_directory.lstrip('/')).replace('\\', '/')
        else:
            remote_directory = remote_directory.replace('\\', '/')

        parts = remote_directory.split('/')
        current_path = base_path
        for part in parts[len(base_path.split('/')):]:
            if part:
                current_path = os.path.join(current_path, part).replace('\\', '/')
                try:
                    sftp.chdir(current_path)
                    console.print(f"[bold green]Директория {current_path} существует на SFTP.[/bold green]")
                    try:
                        sftp.chmod(current_path, 0o775)
                        console.print(f"[bold green]Установлены права 775 для {current_path}.[/bold green]")
                    except Exception as e:
                        console.print(f"[bold yellow]Не удалось установить права для {current_path}: {e}. Проверяйте права пользователя.[/bold yellow]")
                        logger.warning(f"Не удалось установить права для {current_path}: {str(e)}")
                except IOError:
                    try:
                        sftp.mkdir(current_path)
                        console.print(f"[bold green]Создано: {current_path}[/bold green]")
                        try:
                            sftp.chmod(current_path, 0o775)
                            console.print(f"[bold green]Установлены права 775 для {current_path}.[/bold green]")
                        except Exception as e:
                            console.print(f"[bold yellow]Не удалось установить права для {current_path}: {e}. Проверяйте права пользователя.[/bold yellow]")
                            logger.warning(f"Не удалось установить права для {current_path}: {str(e)}")
                    except Exception as e:
                        console.print(f"[bold red]Ошибка при создании директории {current_path} на SFTP: {e}[/bold red]")
                        raise
        console.print(f"[bold green]Директория {remote_directory} успешно создана или проверена на SFTP.[/bold green]")
    except Exception as e:
        console.print(f"[bold red]Ошибка при проверке/создании директории {remote_directory} на SFTP: {e}[/bold red]")
        logger.error(f"Ошибка при создании директории {remote_directory}: {str(e)}")
        raise

# Функция для получения списка файлов на SFTP
def get_sftp_files(sftp, directory):
    try:
        files = []
        for item in sftp.listdir_attr(directory):
            files.append(item.filename)
        return files
    except Exception as e:
        console.print(f"[bold red]Ошибка при получении списка файлов на SFTP в {directory}: {e}[/bold red]")
        logger.error(f"Ошибка при получении списка файлов в {directory}: {str(e)}")
        return []

# Функции для работы с ревизиями на SFTP
def load_revisions_from_sftp(sftp):
    local_path = get_local_file_path('revisions.json')
    remote_path = os.path.join(REMOTE_PATH, 'revisions.json').replace('\\', '/')
    try:
        sftp.get(remote_path, str(local_path))
        with open(local_path, 'r', encoding='utf-8') as f:
            content = f.read().strip()
            logger.info(f"Содержимое revisions.json: '{content[:100]}...'")
            if not content:
                console.print("[bold yellow]Файл revisions.json пуст. Инициализируем пустым словарем.[/bold yellow]")
                logger.info("Файл revisions.json пуст")
                return {}
            revisions = json.loads(content)
            logger.info(f"Загружены ревизии из revisions.json: {len(revisions)} записей")
            return revisions
    except FileNotFoundError:
        console.print("[bold yellow]Файл revisions.json не найден на SFTP. Создаём новый.[/bold yellow]")
        logger.info("Файл revisions.json не найден, создается новый")
        return {}
    except json.JSONDecodeError as e:
        console.print(f"[bold red]Ошибка при разборе revisions.json: {e}. Используем пустой словарь.[/bold red]")
        logger.error(f"Ошибка разбора revisions.json: {str(e)}")
        return {}
    except Exception as e:
        console.print(f"[bold red]Ошибка при загрузке revisions.json с SFTP: {e}[/bold red]")
        logger.error(f"Ошибка загрузки revisions.json: {str(e)}")
        return {}

def save_revisions_to_sftp(sftp, revisions):
    local_path = get_local_file_path('revisions.json')
    remote_path = os.path.join(REMOTE_PATH, 'revisions.json').replace('\\', '/')
    try:
        with open(local_path, 'w', encoding='utf-8') as f:
            json.dump(revisions, f, ensure_ascii=False, indent=4)
        console.print(f"[bold green]Ревизии сохранены локально: {local_path}[/bold green]")
        logger.info(f"Ревизии сохранены локально: {local_path}")

        upload_file_to_sftp(local_path, REMOTE_PATH, sftp)
        console.print(f"[bold green]Файл revisions.json загружен на SFTP по пути {remote_path}[/bold green]")
        logger.info(f"Файл revisions.json загружен на SFTP по пути {remote_path}")
        return True
    except Exception as e:
        console.print(f"[bold red]Ошибка при сохранении ревизий на SFTP: {e}[/bold red]")
        logger.error(f"Ошибка при сохранении ревизий на SFTP: {str(e)}")
        return False

# Функция для загрузки city_url.json с SFTP
def load_city_urls_from_sftp():
    local_path = get_local_file_path('city_url.json')
    remote_path = os.path.join(REMOTE_PATH, 'city_url.json').replace('\\', '/')
    try:
        sftp = connect_to_sftp(SFTP_HOST, SFTP_PORT, SFTP_USERNAME, SFTP_PASSWORD)
        sftp.get(remote_path, str(local_path))
        sftp.close()
        with open(local_path, 'r', encoding='utf-8') as f:
            content = f.read().strip()
            logger.info(f"Содержимое city_url.json: '{content[:100]}...'")
            if not content:
                console.print("[bold yellow]Файл city_url.json пуст. Инициализируем пустым словарем.[/bold yellow]")
                return {}
            city_urls = json.loads(content)
            logger.info(f"Загружены URL городов из city_url.json: {len(city_urls)} записей")
            return city_urls
    except FileNotFoundError:
        console.print("[bold yellow]Файл city_url.json не найден на SFTP. Создаём новый.[/bold yellow]")
        logger.info("Файл city_url.json не найден, создается новый")
        return {}
    except json.JSONDecodeError as e:
        console.print(f"[bold red]Ошибка при разборе city_url.json: {e}. Используем пустой словарь.[/bold red]")
        logger.error(f"Ошибка разбора city_url.json: {str(e)}")
        return {}
    except Exception as e:
        console.print(f"[bold red]Ошибка при загрузке city_url.json с SFTP: {e}[/bold red]")
        logger.error(f"Ошибка загрузки city_url.json: {str(e)}")
        return {}

# Функция для сохранения city_url.json на SFTP
def save_city_urls_to_sftp(city_urls):
    local_path = get_local_file_path('city_url.json')
    remote_path = os.path.join(REMOTE_PATH, 'city_url.json').replace('\\', '/')
    try:
        with open(local_path, 'w', encoding='utf-8') as f:
            json.dump(city_urls, f, ensure_ascii=False, indent=4)
        console.print(f"[bold green]URL городов сохранены локально: {local_path}[/bold green]")
        logger.info(f"URL городов сохранены локально: {local_path}")

        sftp = connect_to_sftp(SFTP_HOST, SFTP_PORT, SFTP_USERNAME, SFTP_PASSWORD)
        upload_file_to_sftp(local_path, REMOTE_PATH, sftp)
        sftp.close()
        console.print(f"[bold green]Файл city_url.json загружен на SFTP по пути {remote_path}[/bold green]")
        logger.info(f"Файл city_url.json загружен на SFTP по пути {remote_path}")
        return True
    except Exception as e:
        console.print(f"[bold red]Ошибка при сохранении city_url.json на SFTP: {e}[/bold red]")
        logger.error(f"Ошибка при сохранении city_url.json на SFTP: {str(e)}")
        return False

# Проверка состояния API
def check_api_status(api_key):
    if not api_key or api_key.startswith("ВАШ_КЛЮЧ"):
        console.print("[bold red]API-ключ не указан.[/bold red]")
        logger.error("API-ключ не указан")
        return False
    
    url = f"{BASE_URL}/api/1/access_token"
    headers = {"Content-Type": "application/json"}
    payload = {"apiLogin": api_key}
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        if response.status_code == 200:
            console.print("[bold green]API доступен и работает корректно.[/bold green]")
            logger.info("API доступен и работает корректно")
            return True
        elif response.status_code == 403:
            console.print(f"[bold red]API заблокирован: {response.text}[/bold red]")
            logger.error(f"API заблокирован: {response.text}")
            return False
        else:
            console.print(f"[bold yellow]Неожиданный ответ от API: код {response.status_code}, текст: {response.text}[/bold yellow]")
            logger.warning(f"Неожиданный ответ от API: {response.status_code}, текст: {response.text}")
            return False
    except requests.RequestException as e:
        console.print(f"[bold red]Ошибка при проверке API: {e}[/bold red]")
        logger.error(f"Ошибка проверки API: {str(e)}")
        return False

# Функции для API iikoCloud
def get_access_token(api_key):
    url = f"{BASE_URL}/api/1/access_token"
    headers = {"Content-Type": "application/json"}
    payload = {"apiLogin": api_key}
    response = requests.post(url, json=payload, headers=headers)
    response.raise_for_status()
    token = response.json()["token"]
    logger.info("Получен access token для API")
    return token

def get_organizations(token):
    url = f"{BASE_URL}/api/1/organizations"
    headers = {"Authorization": f"Bearer {token}"}
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    orgs = response.json()["organizations"]
    logger.info(f"Получено {len(orgs)} организаций из API")
    return orgs

def get_external_menus(token, organization_id):
    url = f"{BASE_URL}/api/2/menu"
    headers = {"Authorization": f"Bearer {token}"}
    payload = {"organizationIds": [organization_id]}
    response = requests.post(url, json=payload, headers=headers)
    response.raise_for_status()
    menu_data = response.json()
    logger.info(f"Получены внешние меню для организации {organization_id}")
    return menu_data

def get_menu_by_id(token, external_menu_id, organization_id, price_category_id=PRICE_CATEGORY_ID, start_revision=0):
    url = f"{BASE_URL}/api/2/menu/by_id"
    headers = {"Authorization": f"Bearer {token}"}
    payload = {
        "externalMenuId": external_menu_id,
        "organizationIds": [organization_id],
        "priceCategoryId": price_category_id,
        "version": 2,
        "language": "ru",
        "asyncMode": False,
        "startRevision": start_revision
    }
    response = requests.post(url, json=payload, headers=headers)
    response.raise_for_status()
    menu_data = response.json()
    logger.info(f"Получены данные меню для externalMenuId={external_menu_id}, revision={menu_data.get('revision')}")
    return menu_data

def extract_prices(menu_data, article_list, organization_id):
    results = {}
    for category in menu_data.get("itemCategories", []):
        for item in category.get("items", []):
            sku = str(item.get("sku", ""))
            if sku in article_list:
                sizes = item.get("itemSizes", [])
                if sizes:
                    for price in sizes[0].get("prices", []):
                        if price.get("organizationId") == organization_id:
                            price_val = price.get("price")
                            if price_val and price_val > 0:
                                results[sku] = price_val
    logger.info(f"Извлечено {len(results)} цен для артикулов из API")
    logger.debug(f"Пример цен: {list(results.items())[:5]}")
    return results

def update_prices_with_api(df, locations, api_key, currency="RUB"):
    if not check_api_status(api_key):
        console.print("[bold red]Прерывание выполнения: API недоступен.[/bold red]")
        logger.error("Прерывание: API недоступен")
        return {}

    token = get_access_token(api_key)
    orgs = get_organizations(token)
    
    article_list = df['Артикул iiko'].dropna().astype(str).unique().tolist() if 'Артикул iiko' in df.columns else []
    console.print(f"[bold blue]Найдено {len(article_list)} уникальных артикулов для запроса к API[/bold blue]")
    logger.info(f"Найдено {len(article_list)} уникальных артикулов: {article_list[:5]}...")

    data_with_api_prices = {}
    sftp = connect_to_sftp(SFTP_HOST, SFTP_PORT, SFTP_USERNAME, SFTP_PASSWORD)
    revisions = load_revisions_from_sftp(sftp)
    
    org_list = [(file_name, city_name) for file_name, (city_name,) in locations.items()]
    for i in range(0, len(org_list), 3):
        batch = org_list[i:i+3]
        console.print(f"[bold blue]Обработка группы организаций {i+1}-{i+len(batch)} из {len(org_list)}[/bold blue]")
        logger.info(f"Обработка группы организаций {i+1}-{i+len(batch)} из {len(org_list)}")
        
        for file_name, city_name in batch:
            org = next((o for o in orgs if o["name"] == city_name), None)
            if not org:
                console.print(f"[red]Организация '{city_name}' не найдена в API.[/red]")
                logger.warning(f"Организация '{city_name}' не найдена в API")
                continue

            org_id = org["id"]
            start_revision = revisions.get(org_id, 0)

            menu_data = get_external_menus(token, org_id)
            if not menu_data.get("externalMenus"):
                console.print(f"[red]Нет меню для '{city_name}'.[/red]")
                logger.warning(f"Нет меню для '{city_name}'")
                continue

            menu_id = menu_data["externalMenus"][0]["id"]
            for attempt in range(3):
                try:
                    menu_detail = get_menu_by_id(token, menu_id, org_id, start_revision=start_revision)
                    revision = menu_detail.get("revision", start_revision)
                    if revision != start_revision:
                        console.print(f"[bold blue]Новая ревизия для {city_name}: {revision} (было {start_revision})[/bold blue]")
                        revisions[org_id] = revision
                    
                    api_prices = extract_prices(menu_detail, article_list, org_id)
                    console.print(f"[bold blue]Получено {len(api_prices)} цен из API для {city_name}[/bold blue]")

                    if 'Артикул iiko' not in df.columns:
                        console.print(f"[yellow]Колонка 'Артикул iiko' отсутствует. Используем все данные без фильтрации по артикулам.[/yellow]")
                        logger.warning("Колонка 'Артикул iiko' отсутствует")
                        filtered_data = df.copy()
                    else:
                        filtered_data = df[df['Артикул iiko'].astype(str).isin(api_prices.keys())].copy()
                        logger.info(f"Отфильтровано {len(filtered_data)} строк по артикулам из API для {city_name}")
                    
                    if filtered_data.empty:
                        console.print(f"[yellow]Нет данных для '{city_name}' с соответствующими артикулами из API.[/yellow]")
                        logger.warning(f"Нет данных для '{city_name}' с соответствующими артикулами")
                        data_with_api_prices[file_name] = []
                        break
                    
                    filtered_data['price'] = filtered_data.apply(
                        lambda row: api_prices.get(str(row['Артикул iiko']), row['price']) if 'Артикул iiko' in row and pd.notna(row['Артикул iiko']) else row['price'],
                        axis=1
                    )
                    filtered_data['price_local'] = filtered_data['price']
                    logger.info(f"Обновлены цены для {city_name}: {len(filtered_data)} записей")
                    logger.debug(f"Пример обновленных данных: {filtered_data[['Артикул iiko', 'price']].head().to_dict('records')}")
                    
                    filtered_data = filtered_data[['Категория', 'Название', 'Описание', 'price', 'ФОТО', 'Популярное']].rename(columns={
                        'Категория': 'category',
                        'Название': 'nameAAA',
                        'Описание': 'description',
                        'ФОТО': 'picture',
                        'Популярное': 'popular'
                    }).fillna({'category': '', 'nameAAA': '', 'description': '', 'picture': '', 'popular': ''})
                    
                    data_with_api_prices[file_name] = filtered_data.to_dict('records')
                    console.print(f"[bold green]Обработано {len(filtered_data)} записей для {city_name}[/bold green]")
                    break
                
                except requests.exceptions.HTTPError as e:
                    if e.response.status_code == 403:
                        console.print(f"[bold red]API заблокирован для {city_name}: {e}[/bold red]")
                        logger.error(f"API заблокирован для {city_name}: {str(e)}")
                        break
                    elif e.response.status_code == 429:
                        console.print(f"[bold yellow]Получен код 429 для {city_name}. Ожидание 60 секунд...[/bold yellow]")
                        logger.warning(f"Получен код 429 для {city_name}, ожидание 60 секунд")
                        time.sleep(60)
                    else:
                        console.print(f"[bold red]Ошибка API для {city_name}: {e}[/bold red]")
                        logger.error(f"Ошибка API для {city_name}: {str(e)}")
                        break
                except Exception as e:
                    console.print(f"[bold red]Неизвестная ошибка для {city_name}: {e}[/bold red]")
                    logger.error(f"Неизвестная ошибка для {city_name}: {str(e)}")
                    break
        
        save_revisions_to_sftp(sftp, revisions)
        if i + 3 < len(org_list):
            console.print("[bold blue]Ожидание 60 секунд перед следующей группой...[/bold blue]")
            with Progress(SpinnerColumn(), TextColumn("[bold cyan]Оставшееся время: {task.description} сек[/bold cyan]"), transient=True) as progress:
                task = progress.add_task("", total=60)
                for remaining in range(60, 0, -1):
                    progress.update(task, description=f"{remaining}", advance=1)
                    time.sleep(1)

    sftp.close()
    return data_with_api_prices

def update_prices_with_api_for_city(df, locations, selected_city, api_key, currency="RUB"):
    if not check_api_status(api_key):
        console.print("[bold red]Прерывание выполнения: API недоступен.[/bold red]")
        logger.error("Прерывание: API недоступен")
        return {}

    token = get_access_token(api_key)
    orgs = get_organizations(token)
    
    article_list = df['Артикул iiko'].dropna().astype(str).unique().tolist() if 'Артикул iiko' in df.columns else []
    console.print(f"[bold blue]Найдено {len(article_list)} уникальных артикулов для запроса к API[/bold blue]")
    logger.info(f"Найдено {len(article_list)} уникальных артикулов: {article_list[:5]}...")

    data_with_api_prices = {}
    sftp = connect_to_sftp(SFTP_HOST, SFTP_PORT, SFTP_USERNAME, SFTP_PASSWORD)
    try:
        revisions = load_revisions_from_sftp(sftp)
        logger.info(f"Ревизии успешно загружены с SFTP: {len(revisions)} записей")
    except Exception as e:
        console.print(f"[bold red]Ошибка при загрузке ревизий с SFTP: {e}[/bold red]")
        logger.error(f"Ошибка при загрузке ревизий: {str(e)}")
        revisions = {}

    org_list = [(file_name, city_name) for file_name, (city_name,) in locations.items()]
    file_name, city_name = next(((fn, cn) for fn, cn in org_list if cn == selected_city), (None, None))
    
    if not file_name or not city_name:
        console.print(f"[bold red]Город '{selected_city}' не найден в списке локаций.[/bold red]")
        logger.error(f"Город '{selected_city}' не найден")
        sftp.close()
        return {}

    console.print(f"[bold blue]Обработка города: {city_name}[/bold blue]")
    logger.info(f"Обработка города: {city_name}")
    
    org = next((o for o in orgs if o["name"] == city_name), None)
    if not org:
        console.print(f"[red]Организация '{city_name}' не найдена в API.[/red]")
        logger.warning(f"Организация '{city_name}' не найдена в API")
        sftp.close()
        return {}

    org_id = org["id"]
    start_revision = revisions.get(org_id, 0)

    required_columns = ['Категория', 'Название', 'Описание', 'ФОТО', 'Артикул iiko']
    missing_columns = [col for col in required_columns if col not in df.columns]
    if missing_columns:
        console.print(f"[bold red]Отсутствуют обязательные столбцы в Google Sheets: {missing_columns}[/bold red]")
        logger.error(f"Отсутствуют столбцы: {missing_columns}")
        sftp.close()
        return {}
    
    filtered_data = df[required_columns + (['Популярное'] if 'Популярное' in df.columns else [])].rename(columns={
        'Категория': 'category',
        'Название': 'nameAAA',
        'Описание': 'description',
        'ФОТО': 'picture',
        'Артикул iiko': 'article_iiko',
        'Популярное': 'popular'
    }).fillna({'category': '', 'nameAAA': '', 'description': '', 'picture': '', 'article_iiko': '', 'popular': ''})
    filtered_data['price'] = 0
    logger.info(f"Исходный DataFrame подготовлен: {len(filtered_data)} строк")

    menu_data = get_external_menus(token, org_id)
    if not menu_data.get("externalMenus"):
        console.print(f"[red]Нет меню для '{city_name}'.[/red]")
        logger.warning(f"Нет меню для '{city_name}'")
    else:
        menu_id = menu_data["externalMenus"][0]["id"]
        for attempt in range(3):
            try:
                menu_detail = get_menu_by_id(token, menu_id, org_id, start_revision=start_revision)
                revision = menu_detail.get("revision", start_revision)
                if revision != start_revision:
                    console.print(f"[bold blue]Новая ревизия для {city_name}: {revision} (было {start_revision})[/bold blue]")
                    revisions[org_id] = revision
                
                menu_file_path = get_local_file_path(f"menu_{city_name}_{revision}.json")
                with open(menu_file_path, 'w', encoding='utf-8') as f:
                    json.dump(menu_detail, f, ensure_ascii=False, indent=4)
                console.print(f"[bold green]Меню сохранено локально: {menu_file_path}[/bold green]")
                logger.info(f"Меню сохранено в {menu_file_path}")

                api_prices = extract_prices(menu_detail, article_list, org_id)
                console.print(f"[bold blue]Получено {len(api_prices)} цен из API для {city_name}[/bold blue]")
                logger.info(f"Получено {len(api_prices)} цен из API: {list(api_prices.items())[:5]}...")

                if 'article_iiko' in filtered_data.columns and api_prices:
                    logger.info(f"Обновление цен для {city_name} из API...")
                    filtered_data['price'] = filtered_data.apply(
                        lambda row: api_prices.get(str(row['article_iiko']), 0) if pd.notna(row['article_iiko']) else 0,
                        axis=1
                    )
                    logger.debug(f"Цены из API: {api_prices}")
                    logger.debug(f"Обновлённые цены в filtered_data: {filtered_data[['nameAAA', 'price']].head().to_dict('records')}")
                else:
                    logger.warning("Цены не обновлены: отсутствует 'article_iiko' или данные из API")

                break
            except requests.exceptions.HTTPError as e:
                if e.response.status_code == 403:
                    console.print(f"[bold red]API заблокирован для {city_name}: {e}[/bold red]")
                    logger.error(f"API заблокирован для {city_name}: {str(e)}")
                    break
                elif e.response.status_code == 429:
                    console.print(f"[bold yellow]Получен код 429 для {city_name}. Ожидание 60 секунд...[/bold yellow]")
                    logger.warning(f"Получен код 429 для {city_name}, ожидание 60 секунд")
                    time.sleep(60)
                else:
                    console.print(f"[bold red]Ошибка API для {city_name}: {e}[/bold red]")
                    logger.error(f"Ошибка API для {city_name}: {str(e)}")
                    break
            except Exception as e:
                console.print(f"[bold red]Неизвестная ошибка для {city_name}: {e}[/bold red]")
                logger.error(f"Неизвестная ошибка для {city_name}: {str(e)}", exc_info=True)
                break

    filtered_data['price_local'] = filtered_data['price']
    logger.info(f"Итоговые данные для {city_name}: {len(filtered_data)} записей")
    logger.debug(f"Пример данных: {filtered_data.head().to_dict('records')}")

    data_with_api_prices[file_name] = filtered_data.to_dict('records')
    console.print(f"[bold green]Обработано {len(filtered_data)} записей для {city_name}[/bold green]")

    save_revisions_to_sftp(sftp, revisions)
    sftp.close()
    return data_with_api_prices

def load_locations_from_file(locations_file):
    with open(locations_file, 'r', encoding='utf-8') as file:
        locations = json.load(file)
    logger.info(f"Загружены локации из файла {locations_file}: {len(locations)} записей")
    return locations

def load_sheet_url_from_file(location_menu_file):
    with open(location_menu_file, 'r', encoding='utf-8') as file:
        menu_data = json.load(file)
    logger.info(f"Загружен URL Google Sheets из файла {location_menu_file}: {menu_data['sheet_url']}")
    return menu_data['sheet_url']

def update_sheet_url_in_file(location_menu_file, new_url):
    with open(location_menu_file, 'r+', encoding='utf-8') as file:
        menu_data = json.load(file)
        menu_data['sheet_url'] = new_url
        file.seek(0)
        json.dump(menu_data, file, ensure_ascii=False, indent=4)
        file.truncate()
    logger.info(f"Обновлен URL Google Sheets в файле {location_menu_file}: {new_url}")

def edit_city_url(locations, region):
    console.print(f"[bold blue]Начало редактирования URL для {region}[/bold blue]")
    logger.info(f"Начало редактирования URL для {region}")
    
    city_urls = load_city_urls_from_sftp()
    table = Table(title=f"Города и URL ({region})", title_style="bold magenta")
    table.add_column("Номер", style="cyan")
    table.add_column("Город", style="green")
    table.add_column("URL", style="yellow")

    city_list = [(file_name, city_name) for file_name, (city_name,) in locations.items()]
    for i, (file_name, city_name) in enumerate(city_list, 1):
        url = city_urls.get(file_name, '')
        table.add_row(str(i), city_name, url)
    console.print(table)

    try:
        city_choice = int(input("Введите номер города: ")) - 1
        if city_choice < 0 or city_choice >= len(city_list):
            console.print("[bold red]Неверный выбор города.[/bold red]")
            logger.error("Неверный выбор города")
            return
        
        selected_file_name, selected_city = city_list[city_choice]
        console.print(f"[bold blue]Выбран город: {selected_city}[/bold blue]")
        new_url = input("Введите новый URL: ")
        city_urls[selected_file_name] = new_url
        save_city_urls_to_sftp(city_urls)
        console.print(f"[bold green]URL для {selected_city} обновлен: {new_url}[/bold green]")
        logger.info(f"URL для {selected_city} обновлен: {new_url}")
    except ValueError:
        console.print("[bold red]Ошибка: введите корректный номер города.[/bold red]")
        logger.error("Ошибка: некорректный ввод номера города")

def view_cities(locations):
    table = Table(title="Текущие города в скрипте", title_style="bold magenta")
    table.add_column("Файл", style="cyan")
    table.add_column("Город", style="green")

    for file_name, (city_name,) in locations.items():
        table.add_row(file_name, city_name)
    console.print(table)
    logger.info("Отображены все города из файла локаций")

def add_city_to_file(locations_file, city_name, column_name):
    locations = load_locations_from_file(locations_file)
    
    locations[city_name] = [column_name]

    with open(locations_file, 'w', encoding='utf-8') as file:
        json.dump(locations, file, ensure_ascii=False, indent=4)
    
    sftp = connect_to_sftp(SFTP_HOST, SFTP_PORT, SFTP_USERNAME, SFTP_PASSWORD)
    upload_file_to_sftp(get_local_file_path(locations_file), REMOTE_PATH, sftp)
    sftp.close()
    console.print(f"[bold green]Город {city_name} добавлен в файл с локациями и загружен на SFTP.[/bold green]")
    logger.info(f"Добавлен город {city_name} в файл {locations_file}")
    return locations

def check_new_cities(sheet_url, locations_file):
    df = read_google_sheet(sheet_url)
    locations = load_locations_from_file(locations_file)
    existing_columns = {col for loc in locations.values() for col in loc}
    
    # Нормализация имён столбцов: удаление переносов строк и лишних пробелов
    df.columns = df.columns.str.replace('\n', ' ').str.strip()
    
    # Обновлённый список стандартных столбцов
    standard_columns = {
        'Категория', 'Название', 'Описание', 'ФОТО', 'Цена', 'IMAGE', 'IMAGE ФОТО', 'WEBP', 'IMAGE WEBP',
        'Наименование интереса (для сайта+приложение)', 'Наименование интереса для сайта+приложение',
        'Теги интересов (для сайта+приложение)', 'Теги интересов для сайта+приложение',
        'Теги iiko', 'Артикул iiko', 'В наличии', 'Количество', 'Единицы измерения', 'Идет ли на доставку',
        'Популярное', 'Артикул'
    }
    
    new_columns = set(df.columns) - existing_columns - standard_columns
    
    for column in new_columns:
        console.print(f"[bold yellow]Найден новый город: {column}[/bold yellow]")
        logger.info(f"Найден новый город: {column}")
        add_city = input("Добавить новый город? (1 - да, 0 - нет): ")
        if add_city == "1":
            city_name = input("Введите название файла выгрузки для этого города (например, yandex_moscow): ")
            locations = add_city_to_file(locations_file, city_name, column)
            logger.info(f"Добавлен новый город {city_name} с колонкой {column}")
    return locations

def filter_excel_data_by_location(df, location, sell_column, currency="RUB"):
    if (sell_column not in df.columns) or df.empty:
        logger.warning(f"Столбец {sell_column} не найден или DataFrame пустой для локации {location}")
        console.print(f"[bold red]Ошибка: Столбец {sell_column} не найден или таблица пуста для {location}[/bold red]")
        return pd.DataFrame()
    
    df[sell_column] = df[sell_column].fillna('')
    
    # Логирование данных для диагностики
    logger.info(f"Обработка локации {location}, столбец {sell_column}, валюта {currency}")
    logger.debug(f"Первые 5 строк столбца {sell_column}: {df[sell_column].head().tolist()}")
    
    # Основной метод: фильтрация строк, где значение в столбце является числом
    df_filtered = df[df[sell_column].apply(lambda x: re.search(r'^\d+$', str(x)) is not None)]
    
    # Альтернативный метод для КЗ, если основной метод не дал результатов
    if df_filtered.empty and currency == "KZT":
        logger.info(f"Основной метод не дал результатов для {location} (КЗ), применяем альтернативный метод")
        # Фильтрация строк, где значение в столбце города — "Да" или "да"
        df_filtered = df[df[sell_column].str.strip().str.lower().isin(['да', 'Да'])]
        
        if df_filtered.empty:
            logger.info(f"Нет данных для локации {location} в столбце {sell_column} с 'Да'")
            console.print(f"[bold yellow]Нет данных для локации {location} в столбце {sell_column} с 'Да'[/bold yellow]")
            return pd.DataFrame()
        
        # Включение столбца "Популярное", если он есть
        columns_to_include = ['Категория', 'Название', 'Описание', 'Цена', 'ФОТО', sell_column]
        if 'Популярное' in df.columns:
            columns_to_include.append('Популярное')
        
        df_filtered = df_filtered[columns_to_include]
        df_filtered = df_filtered.rename(columns={
            'Категория': 'category',
            'Название': 'nameAAA',
            'Описание': 'description',
            'Цена': 'price',
            'ФОТО': 'picture',
            'Популярное': 'popular'
        })
        # Нормализация столбца popular
        df_filtered['popular'] = df_filtered['popular'].str.strip().str.lower()
        # Используем столбец "Цена" для price_local
        df_filtered['price_local'] = df_filtered['price']
        
        df_filtered = df_filtered.dropna(subset=['price'])
        
        df_filtered['price'] = pd.to_numeric(df_filtered['price'], errors='coerce')
        df_filtered['price_local'] = pd.to_numeric(df_filtered['price_local'], errors='coerce')
        
        df_filtered = df_filtered.fillna({'category': '', 'nameAAA': '', 'description': '', 'picture': '', 'popular': ''})
        
        logger.info(f"Отфильтровано {len(df_filtered)} записей для локации {location} (альтернативный метод КЗ)")
        console.print(f"[bold green]Отфильтровано {len(df_filtered)} записей для локации {location} (КЗ)[/bold green]")
        return df_filtered
    
    if df_filtered.empty:
        logger.info(f"Нет данных для локации {location} в столбце {sell_column}")
        console.print(f"[bold yellow]Нет данных для локации {location} в столбце {sell_column}[/bold yellow]")
        return pd.DataFrame()
    
    # Основной метод продолжается
    columns_to_include = ['Категория', 'Название', 'Описание', 'Цена', 'ФОТО', sell_column]
    if 'Популярное' in df.columns:
        columns_to_include.append('Популярное')
    
    df_filtered = df_filtered[columns_to_include]
    df_filtered = df_filtered.rename(columns={
        'Категория': 'category',
        'Название': 'nameAAA',
        'Описание': 'description',
        'Цена': 'price',
        'ФОТО': 'picture',
        'Популярное': 'popular'
    })
    # Нормализация столбца popular
    df_filtered['popular'] = df_filtered['popular'].str.strip().str.lower()
    df_filtered['price_local'] = df_filtered[sell_column]
    
    df_filtered = df_filtered.dropna(subset=['price'])
    
    df_filtered['price'] = pd.to_numeric(df_filtered['price'], errors='coerce')
    df_filtered['price_local'] = pd.to_numeric(df_filtered['price_local'], errors='coerce')
    
    df_filtered = df_filtered.fillna({'category': '', 'nameAAA': '', 'description': '', 'picture': '', 'popular': ''})
    
    logger.info(f"Отфильтровано {len(df_filtered)} записей для локации {location}")
    console.print(f"[bold green]Отфильтровано {len(df_filtered)} записей для локации {location}[/bold green]")
    return df_filtered

def sanitize_filename(filename):
    filename = re.sub(r'[<>:"/\\|?*\n]+', '_', filename)
    filename = re.sub(r'\.+', '.', filename)
    return filename

def encode_url(url):
    """Кодирует URL, заменяя пробелы на %20 и специальные символы."""
    if not url:
        return url
    url = urllib.parse.quote(url, safe=':/?=&')
    url = url.replace('<', '&lt;').replace('>', '&gt;').replace('&', '&amp;').replace("'", '&apos;')
    return url


def generate_xml_files(data, output_folder, currency="RUB"):
    output_folder.mkdir(parents=True, exist_ok=True)
    city_urls = load_city_urls_from_sftp()

    for location, items in data.items():
        if not items:
            console.print(f"[bold yellow]Нет данных для локации {location}. Пропускаем создание XML.[/bold yellow]")
            logger.warning(f"Нет данных для локации {location}")
            continue

        yml_catalog = ET.Element('yml_catalog', date=datetime.now().strftime('%Y-%m-%d_%H-%M-%S'))
        shop = ET.SubElement(yml_catalog, 'shop')
        categories = ET.SubElement(shop, 'categories')
        offers = ET.SubElement(shop, 'offers')
        
        categories_dict = {}
        valid_offers = 0
        for item in items:
            # Проверка обязательных полей (description теперь необязательное)
            missing_fields = []
            if not (pd.notna(item.get('nameAAA')) and item['nameAAA'].strip()):
                missing_fields.append('nameAAA')
            if not (pd.notna(item.get('price_local')) or pd.notna(item.get('price'))):
                missing_fields.append('price')
            if not (pd.notna(item.get('picture')) and item['picture'].strip()):
                missing_fields.append('picture')
            if not (pd.notna(item.get('category')) and item['category'].strip()):
                missing_fields.append('category')
            
            if missing_fields:
                console.print(f"[bold yellow]Пропущена строка для {item.get('nameAAA', 'Неизвестное блюдо')}: отсутствуют поля {missing_fields}[/bold yellow]")
                logger.warning(f"Пропущена строка для {item.get('nameAAA', 'Неизвестное блюдо')}: отсутствуют поля {missing_fields}")
                continue

            category = item['category']
            if category not in categories_dict:
                category_id = str(hash(category))
                category_element = ET.SubElement(categories, 'category', id=category_id)
                category_element.text = category
                categories_dict[category] = category_id
            
            category_id = categories_dict[category]
            offer = ET.SubElement(offers, 'offer', attrib={'id': str(valid_offers + 1)})
            name = ET.SubElement(offer, 'name')
            name.text = item['nameAAA']
            description = ET.SubElement(offer, 'description')
            description.text = item['description'] if pd.notna(item.get('description')) and item['description'].strip() else ''
            price = ET.SubElement(offer, 'price')
            price_value = item['price_local'] if pd.notna(item.get('price_local')) and item['price_local'] != '' else item['price']
            price.text = f"{int(float(price_value))}" if pd.notna(price_value) and price_value != '' else '0'
            currency_id = ET.SubElement(offer, 'currencyId')
            currency_id.text = currency
            picture = ET.SubElement(offer, 'picture')
            picture.text = item['picture']
            category_id_element = ET.SubElement(offer, 'categoryId')
            category_id_element.text = category_id
            # Добавление тега url
            url = city_urls.get(location, '')
            if url:
                url = encode_url(url)
                url_element = ET.SubElement(offer, 'url')
                url_element.text = url
            
            valid_offers += 1
        
        if valid_offers == 0:
            console.print(f"[bold yellow]Нет валидных предложений для локации {location}. Файл не будет создан.[/bold yellow]")
            logger.warning(f"Нет валидных предложений для локации {location}")
            continue
        
        sanitized_location = sanitize_filename(location)
        output_file = str(output_folder / f'{sanitized_location}.xml')
        tree = ET.ElementTree(yml_catalog)
        tree.write(output_file, encoding='utf-8', xml_declaration=True)
        
        # Логирование содержимого файла для проверки
        with open(output_file, 'r', encoding='utf-8') as f:
            content = f.read()
            logger.info(f"Содержимое созданного XML файла {output_file}: {content[:500]}...")
        
        console.print(f"[bold green]XML файл создан: {output_file} (с {valid_offers} предложениями)[/bold green]")
        logger.info(f"Создан XML файл: {output_file} с {valid_offers} предложениями")

def generate_csv_files(data, output_folder, currency="RUB"):
    output_folder.mkdir(parents=True, exist_ok=True)
    city_urls = load_city_urls_from_sftp()

    for location, items in data.items():
        if not items:
            console.print(f"[bold yellow]Нет данных для локации {location}. Пропускаем создание CSV.[/bold yellow]")
            logger.warning(f"Нет данных для локации {location}")
            continue
        
        csv_file_name = f"2GIS_{location.replace('yandex_', '')}.csv"
        csv_file = output_folder / csv_file_name
        with open(csv_file, 'w', newline='', encoding='utf-8-sig') as file:
            writer = csv.writer(file, delimiter=';')
            writer.writerow(['category', 'name', 'description', 'price', 'picture', 'currencyId', 'url'])
            for item in items:
                # Проверка обязательных полей (description теперь необязательное)
                missing_fields = []
                if not (pd.notna(item.get('nameAAA')) and item['nameAAA'].strip()):
                    missing_fields.append('nameAAA')
                if not (pd.notna(item.get('price_local')) or pd.notna(item.get('price'))):
                    missing_fields.append('price')
                if not (pd.notna(item.get('picture')) and item['picture'].strip()):
                    missing_fields.append('picture')
                if not (pd.notna(item.get('category')) and item['category'].strip()):
                    missing_fields.append('category')
                
                if missing_fields:
                    console.print(f"[bold yellow]Пропущена строка для {item.get('nameAAA', 'Неизвестное блюдо')}: отсутствуют поля {missing_fields}[/bold yellow]")
                    logger.warning(f"Пропущена строка для {item.get('nameAAA', 'Неизвестное блюдо')}: отсутствуют поля {missing_fields}")
                    continue
                
                price_local = item['price_local']
                price = item['price']
                if price_local and price_local != '':
                    price_value = int(float(price_local))
                elif price and price != '':
                    price_value = int(float(price))
                else:
                    price_value = ''
                
                url = city_urls.get(location, '')
                if url:
                    url = encode_url(url)
                
                writer.writerow([
                    item['category'],
                    item['nameAAA'],
                    item['description'] if pd.notna(item.get('description')) and item['description'].strip() else '',
                    price_value,
                    item['picture'],
                    currency,
                    url
                ])
        console.print(f"[bold green]CSV файл создан: {csv_file} (с {len(items)} записями)[/bold green]")
        logger.info(f"Создан CSV файл: {csv_file} с {len(items)} записями")

def generate_xls_files(data, output_folder, currency="RUB"):
    output_folder.mkdir(parents=True, exist_ok=True)
    city_urls = load_city_urls_from_sftp()

    for location, items in data.items():
        if not items:
            console.print(f"[bold yellow]Нет данных для локации {location}. Пропускаем создание XLS.[/bold yellow]")
            logger.warning(f"Нет данных для локации {location}")
            continue

        # Создание DataFrame для XLS
        xls_data = []
        for item in items:
            # Проверка обязательных полей (description и popular необязательные)
            missing_fields = []
            if not (pd.notna(item.get('nameAAA')) and item['nameAAA'].strip()):
                missing_fields.append('nameAAA')
            if not (pd.notna(item.get('price_local')) or pd.notna(item.get('price'))):
                missing_fields.append('price')
            if not (pd.notna(item.get('picture')) and item['picture'].strip()):
                missing_fields.append('picture')
            if not (pd.notna(item.get('category')) and item['category'].strip()):
                missing_fields.append('category')
            
            if missing_fields:
                console.print(f"[bold yellow]Пропущена строка для {item.get('nameAAA', 'Неизвестное блюдо')}: отсутствуют поля {missing_fields}[/bold yellow]")
                logger.warning(f"Пропущена строка для {item.get('nameAAA', 'Неизвестное блюдо')}: отсутствуют поля {missing_fields}")
                continue

            price_local = item['price_local']
            price = item['price']
            if price_local and price_local != '':
                price_value = int(float(price_local))
            elif price and price != '':
                price_value = int(float(price))
            else:
                price_value = ''

            popular_value = item.get('popular', '').strip().lower()
            popular_text = 'Да' if popular_value == 'да' else ''

            url = city_urls.get(location, '')
            if url:
                url = encode_url(url)

            xls_data.append({
                'Категория': item['category'],
                'Название': item['nameAAA'],
                'Описание': item['description'] if pd.notna(item.get('description')) and item['description'].strip() else '',
                'Цена': price_value,
                'Фото': item['picture'],
                'Популярный товар': popular_text
            })

        if not xls_data:
            console.print(f"[bold yellow]Нет валидных данных для локации {location}. Файл XLS не будет создан.[/bold yellow]")
            logger.warning(f"Нет валидных данных для локации {location}")
            continue

        # Создание XLS файла
        df_xls = pd.DataFrame(xls_data)
        sanitized_location = sanitize_filename(location)
        output_file = output_folder / f'{sanitized_location}.xls'
        df_xls.to_excel(output_file, index=False, engine='openpyxl')
        
        # Логирование содержимого файла для проверки
        logger.info(f"Создан XLS файл: {output_file} с {len(xls_data)} записями")
        console.print(f"[bold green]XLS файл создан: {output_file} (с {len(xls_data)} записями)[/bold green]")

def upload_to_sftp(local_folder, remote_folder, file_pattern, sftp):
    files = list(local_folder.glob(file_pattern))
    if not files:
        console.print(f"[bold red]Ошибка: файлы с шаблоном {file_pattern} не найдены в {local_folder}[/bold red]")
        logger.error(f"Файлы с шаблоном {file_pattern} не найдены в {local_folder}")
        return

    total_size = sum(os.path.getsize(f) for f in files)

    with Progress(
        TextColumn("[bold blue]{task.description}"),
        BarColumn(bar_width=None, style="green"),
        "[progress.percentage]{task.percentage:>3.1f}%",
        "•",
        TransferSpeedColumn(),
        "•",
        TimeRemainingColumn(),
    ) as progress:
        task = progress.add_task(f"Uploading files to {remote_folder}", total=total_size)
        for local_file in files:
            if not local_file.exists():
                console.print(f"[bold red]Ошибка: файл {local_file} не найден локально![/bold red]")
                logger.error(f"Файл {local_file} не найден локально")
                continue
            
            try:
                file_size = os.path.getsize(local_file)
                with open(local_file, "rb") as file:
                    sftp.putfo(file, f"{remote_folder}/{local_file.name}", callback=lambda sent, total: progress.update(task, advance=sent))
                console.print(f"[bold green]Загружен файл на SFTP: {local_file.name} в {remote_folder}[/bold green]")
                logger.info(f"Загружен файл на SFTP: {local_file.name} в {remote_folder}")
            except Exception as e:
                console.print(f"[bold red]Ошибка при загрузке файла {local_file} в {remote_folder}: {e}[/bold red]")
                logger.error(f"Ошибка при загрузке файла {local_file} в {remote_folder}: {str(e)}")
                continue

def connect_to_sftp(host, port, username, password):
    try:
        transport = paramiko.Transport((host, port))
        transport.connect(username=username, password=password)
        sftp = paramiko.SFTPClient.from_transport(transport)
        console.print(f"[bold green]Успешно подключено к SFTP: {host}:{port} как {username}[/bold green]")
        logger.info(f"Успешное подключение к SFTP: {host}:{port}")
        return sftp
    except Exception as e:
        console.print(f"[bold red]Ошибка при подключении к SFTP: {e}[/bold red]")
        logger.error(f"Ошибка подключения к SFTP: {str(e)}")
        raise

def read_google_sheet(sheet_url):
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name(str(get_local_file_path('mythic-hulling-423914-m7-f53ade191c56.json')), scope)
    client = gspread.authorize(creds)
    sheet = client.open_by_url(sheet_url)
    worksheet = sheet.get_worksheet(0)
    data = worksheet.get_all_records()
    df = pd.DataFrame(data)
    logger.info(f"Загружены данные из Google Sheets: {len(df)} строк")
    return df

def get_yandex_download_link(public_key, path=None):
    base_url = "https://cloud-api.yandex.net/v1/disk/public/resources/download"
    params = {"public_key": public_key}
    if path:
        params["path"] = path
    try:
        response = requests.get(base_url, params=params, timeout=10)
        response.raise_for_status()
        response_data = response.json()
        return response_data['href']
    except (requests.RequestException, KeyError) as e:
        logger.debug("cloud-api: fallback used")
        logger.warning(f"Ошибка получения ссылки через cloud-api: {str(e)}")
        normalized_url = re.sub(r'(&?clckid=\w+)?$', '', public_key)
        download_link = f"https://getfile.dokpub.com/yandex/get/{normalized_url}"
        try:
            response = requests.head(download_link, allow_redirects=True, timeout=10)
            if response.status_code == 200:
                return download_link
            else:
                console.print(f"[bold red]Ошибка при использовании getfile.dokpub.com для {public_key}: код {response.status_code}[/bold red]")
                logger.error(f"Ошибка getfile.dokpub.com: код {response.status_code}")
                return None
        except requests.RequestException as e:
            console.print(f"[bold red]Ошибка при скачивании через getfile.dokpub.com для {public_key}: {e}[/bold red]")
            logger.error(f"Ошибка скачивания через getfile.dokpub.com: {str(e)}")
            return None

def get_final_url_from_apps_script(short_url, script_id):
    creds = None
    token_path = str(get_local_file_path('token.json'))
    
    if os.path.exists(token_path):
        with open(token_path, 'rb') as token:
            creds = pickle.load(token)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                str(get_local_file_path('mythic-hulling-423914-m7-f53ade191c56.json')), 
                ['https://www.googleapis.com/auth/script.projects', 'https://www.googleapis.com/auth/drive'])
            creds = flow.run_local_server(port=0)
        with open(token_path, 'wb') as token:
            pickle.dump(creds, token)

    service = build('script', 'v1', credentials=creds)

    request = {
        'function': 'getFinalUrl',
        'parameters': [short_url],
        'devMode': True
    }
    try:
        response = service.scripts().run(scriptId=script_id, body=request).execute()
        final_url = response['response']['result']
        logger.info(f"Развернута ссылка через Google Apps Script: {final_url}")
        return final_url
    except Exception as e:
        console.print(f"[bold red]Ошибка при вызове Google Apps Script: {e}[/bold red]")
        logger.error(f"Ошибка Google Apps Script: {str(e)}")
        return short_url

def expand_clck_link(short_url):
    if not short_url or not isinstance(short_url, str) or short_url.strip() == "":
        return ""

    try:
        response = requests.get(short_url, allow_redirects=True, timeout=10)
        final_url = response.url
        final_url = re.sub(r'(&?clckid=\w+)?$', '', final_url)
        console.print(f"[bold blue]Развернута ссылка через requests.get: {final_url}[/bold blue]")
        logger.info(f"Развернута ссылка через requests.get: {final_url}")
        
        if 'showcaptcha' in final_url:
            retpath_match = re.search(r'retpath=([^&]+)', final_url)
            if retpath_match:
                decoded_retpath = requests.utils.unquote(retpath_match.group(1))
                cleaned_retpath = decoded_retpath.split(',,')[0].strip()
                cleaned_retpath = cleaned_retpath + '=' * (4 - (len(cleaned_retpath) % 4)) if len(cleaned_retpath) % 4 else cleaned_retpath
                try:
                    decoded_url = base64.b64decode(cleaned_retpath).decode('utf-8')
                    console.print(f"[bold yellow]Декодированный retpath: {decoded_url}[/bold yellow]")
                    logger.info(f"Декодированный retpath: {decoded_url}")
                    if not decoded_url.startswith('http'):
                        raise ValueError("Декодированная ссылка не начинается с 'http'")
                    return decoded_url
                except (base64.binascii.Error, UnicodeDecodeError, ValueError) as e:
                    console.print(f"[bold yellow]Ошибка декодирования retpath: {e}. Пробуем через прямой запрос.[/bold yellow]")
                    logger.warning(f"Ошибка декодирования retpath: {str(e)}")
                    response = requests.get(short_url, allow_redirects=True, timeout=10)
                    final_url = response.url
                    final_url = re.sub(r'(&?clckid=\w+)?$', '', final_url)
                    console.print(f"[bold blue]Развернута ссылка через прямой GET: {final_url}[/bold blue]")
                    return final_url
            else:
                console.print(f"[bold red]Не удалось извлечь retpath из {final_url}. Пробуем прямой GET.[/bold red]")
                logger.error(f"Не удалось извлечь retpath из {final_url}")
                response = requests.get(short_url, allow_redirects=True, timeout=10)
                final_url = response.url
                final_url = re.sub(r'(&?clckid=\w+)?$', '', final_url)
                console.print(f"[bold blue]Развернута ссылка через прямой GET: {final_url}[/bold blue]")
                return final_url
        return final_url
    except requests.RequestException as e:
        console.print(f"[bold red]Ошибка при развертывании ссылки {short_url} через requests: {e}[/bold red]")
        logger.error(f"Ошибка развертывания ссылки через requests: {str(e)}")
        script_id = 'AKfycbxneeMgiOZD8jFh-Mm3Ht5FlayJv1i0IL1ApNOOJRy6pP2zqoszcvq3II41Rx7tZLXv'
        try:
            final_url = get_final_url_from_apps_script(short_url, script_id)
            if final_url.startswith("Ошибка:"):
                console.print(f"[bold red]{final_url}[/bold red]")
                return short_url
            console.print(f"[bold blue]Развернута ссылка через Google Apps Script: {final_url}[/bold blue]")
            return final_url
        except Exception as e:
            console.print(f"[bold red]Ошибка при использовании Google Apps Script для {short_url}: {e}[/bold red]")
            logger.error(f"Ошибка Google Apps Script для {short_url}: {str(e)}")
            console.print(f"[bold yellow]Используем оригинальную ссылку: {short_url}[/bold yellow]")
            return short_url

def process_photos(df, sheet_url, sftp_host, sftp_port, sftp_user, sftp_pass, remote_folder, column='ФОТО'):
    folder_name = 'PHOTO' if column == 'ФОТО' else ('WEBP' if column.upper() == 'WEBP' else str(column))
    temp_dir = Path(tempfile.gettempdir()) / folder_name
    temp_dir.mkdir(parents=True, exist_ok=True)
    
    # Фильтрация строк с непустыми ссылками в указанном столбце
    valid_links = df[df[column].str.contains(r'http', na=False)].copy()

    # Pre-filter: exclude mesh.chicko.me from processing
    from urllib.parse import urlsplit
    def _is_allowed(url: str) -> bool:
        try:
            host = urlsplit((url or '').strip()).netloc.lower()
        except Exception:
            host = ''
        return bool(url) and url.startswith('http') and 'mesh.chicko.me' not in host

    valid_links = valid_links[valid_links[column].apply(_is_allowed)]
    rows_to_process = valid_links

    
    sftp = connect_to_sftp(sftp_host, sftp_port, sftp_user, sftp_pass)
    target_folder = remote_folder if column == 'ФОТО' else f"{remote_folder}/WEBP"
    try:
        ensure_remote_directory_exists(sftp, target_folder)
        existing_sftp_files = get_sftp_files(sftp, target_folder)
    except Exception as e:
        console.print(f"[bold red]Ошибка при работе с SFTP для {target_folder}: {e}[/bold red]")
        logger.error(f"Ошибка SFTP: {str(e)}")
        sftp.close()
        return
    sftp.close()

    # Формирование словаря суффиксов для избежания конфликтов имен файлов
    base_name_suffixes = {}
    for file_name in existing_sftp_files:
        if '_' in file_name and file_name.rsplit('.', 1)[1].lower() in ['png', 'jpg', 'jpeg', 'webp']:
            base_name = file_name.rsplit('_', 1)[0].rsplit('.', 1)[0]
            extension = file_name.rsplit('.', 1)[1].lower()
            try:
                suffix = int(file_name.split('_')[-1].split('.')[0])
                if base_name in base_name_suffixes:
                    base_name_suffixes[base_name] = max(base_name_suffixes[base_name], suffix)
                else:
                    base_name_suffixes[base_name] = suffix
            except ValueError:
                continue

    local_files = []
    duplicate_counters = {}
    url_cache = {}  # Кэш для развернутых ссылок

    pbar = tqdm(rows_to_process.iterrows(), total=rows_to_process.shape[0], desc=f"Downloading {column} images")
    for index, row in pbar:
        photo_url = row[column].strip()
        # Skip internal mesh host
        try:
            from urllib.parse import urlsplit
            host = urlsplit(photo_url).netloc.lower()
        except Exception:
            host = ''
        if 'mesh.chicko.me' in host:
            console.print(f"[bold yellow]Пропуск mesh-хоста: {photo_url}[/bold yellow]")
            logger.info(f"Skip mesh host: {photo_url}")

            continue
        
        # Пропускаем пустые или некорректные ссылки
        if not photo_url or not photo_url.startswith('http'):
            console.print(f"[bold yellow]Пропущена некорректная или пустая ссылка: {photo_url}[/bold yellow]")
            logger.warning(f"Пропущена некорректная или пустая ссылка: {photo_url}")
            continue

        extension = None
        if photo_url.endswith(('.png', '.jpg', '.jpeg', '.webp')):
            extension = photo_url.rsplit('.', 1)[1].lower()

        
        # --- Pre-resolve download method/link ---
        try:
            download_link, used_method = resolve_download_link(photo_url, url_cache)
            if not download_link:
                console.print(f"[bold red]Не удалось определить метод/получить ссылку: {photo_url}[/bold red]")
                logger.error(f"Не удалось определить метод/получить ссылку: {photo_url}")
                continue
            if VERBOSE_METHOD_LOG:
                console.print(f"[bold blue]Метод: {used_method.value} | Ссылка: {download_link}[/bold blue]")
                logger.info(f"Resolved method={used_method.value} url={download_link}")
        except Exception as e:
            console.print(f"[bold red]Ошибка resolve_download_link для {photo_url}: {e}[/bold red]")
            logger.error(f"resolve_download_link error: {e}")
            continue
    
            console.print(f"[bold yellow]Попытка прямого скачивания неизвестной ссылки: {photo_url}[/bold yellow]")
            logger.info(f"Попытка прямого скачивания: {photo_url}")

        if not download_link:
            logger.debug("cloud-api: fallback used")
            logger.error(f"Не удалось получить ссылку: {photo_url}")
            continue

        # Скачивание файла
        try:
            response = requests.get(download_link, timeout=10, stream=True)
            if response.status_code == 200:
                content_type = response.headers.get('Content-Type', '')
                if 'image' in content_type and not extension:
                    extension = content_type.split('/')[-1].lower()
                    if extension not in ['png', 'jpg', 'jpeg', 'webp']:
                        extension = 'webp' if column == 'WEBP' else 'png'
                
                base_name = row['Название'].replace(' ', '_').replace('\n', '_')
                if re.search('[а-яА-Я]', base_name):
                    base_name = transliterate.translit(base_name, reversed=True)
                
                base_name = sanitize_filename(base_name)
                
                if base_name in base_name_suffixes:
                    start_suffix = base_name_suffixes[base_name] + 1
                else:
                    start_suffix = 2
                
                if base_name not in duplicate_counters:
                    duplicate_counters[base_name] = 0
                
                suffix = start_suffix + duplicate_counters[base_name]
                file_name = f"{base_name}_{suffix}.{extension}"
                pbar.set_postfix_str(file_name, refresh=True)
                duplicate_counters[base_name] += 1
                
                local_file_path = temp_dir / file_name
                with open(local_file_path, 'wb') as file:
                    for chunk in response.iter_content(chunk_size=8192):
                        file.write(chunk)
                
                local_files.append((local_file_path, index, file_name))
                console.print(f"[bold green]Скачан файл {column}: {file_name}[/bold green]")
                logger.info(f"Скачан файл {column}: {file_name}")
            else:
                console.print(f"[bold red]Ошибка при скачивании файла {download_link}: код {response.status_code}[/bold red]")
                logger.error(f"Ошибка скачивания файла {download_link}: код {response.status_code}")
        except requests.RequestException as e:
            console.print(f"[bold red]Ошибка при скачивании {download_link}: {e}[/bold red]")
            logger.error(f"Ошибка скачивания {download_link}: {str(e)}")
    
    if not local_files:
        console.print(f"[bold yellow]Нет файлов {column} для загрузки на SFTP.[/bold yellow]")
        logger.info(f"Нет файлов {column} для загрузки на SFTP")
        return
    
    sftp = connect_to_sftp(sftp_host, sftp_port, sftp_user, sftp_pass)
    ensure_remote_directory_exists(sftp, target_folder)
    existing_sftp_files = get_sftp_files(sftp, target_folder)

    total_size = sum(os.path.getsize(f[0]) for f in local_files)

    with Progress(
        TextColumn("[bold blue]{task.description}"),
        BarColumn(bar_width=None, style="green"),
        "[progress.percentage]{task.percentage:>3.1f}%",
        "•",
        TransferSpeedColumn(),
        "•",
        TimeRemainingColumn(),
    ) as progress:
        task = progress.add_task(f"Uploading {column} images", total=total_size)
        for local_file_path, index, file_name in local_files:
            if not local_file_path.exists():
                console.print(f"[bold red]Ошибка: файл {local_file_path} не найден локально![/bold red]")
                logger.error(f"Файл {local_file_path} не найден локально")
                continue
            
            if file_name in existing_sftp_files:
                base_name = file_name.rsplit('_', 1)[0].rsplit('.', 1)[0]
                extension = file_name.rsplit('.', 1)[1].lower()
                max_suffix = 0
                for existing_file in existing_sftp_files:
                    if existing_file.startswith(base_name + '_') and existing_file.endswith(f'.{extension}'):
                        try:
                            suffix = int(existing_file.split('_')[-1].split('.')[0])
                            max_suffix = max(max_suffix, suffix)
                        except ValueError:
                            continue
                new_file_name = f"{base_name}_{max_suffix + 1}.{extension}"
                console.print(f"[bold yellow]Файл {file_name} уже существует на SFTP. Переименован в {new_file_name}[/bold yellow]")
                logger.info(f"Файл {file_name} переименован в {new_file_name}")
                
                new_local_path = local_file_path.with_name(new_file_name)
                try:
                    shutil.move(str(local_file_path), str(new_local_path))
                    local_file_path = new_local_path
                    file_name = new_file_name
                except Exception as e:
                    console.print(f"[bold red]Ошибка при переименовании файла {local_file_path} в {new_local_path}: {e}[/bold red]")
                    logger.error(f"Ошибка переименования файла: {str(e)}")
                    continue
                
                url_path = f"{target_folder}/{file_name}".replace('/var/www/html', '').replace('\\', '/')
                df.at[index, column] = f"https://mesh.chicko.me:8080{url_path}"
            
            try:
                upload_file_to_sftp(local_file_path, target_folder, sftp, progress, task)
                url_path = f"{target_folder}/{file_name}".replace('/var/www/html', '').replace('\\', '/')
                df.at[index, column] = f"https://mesh.chicko.me:8080{url_path}"
                console.print(f"[bold green]Обновлён URL в Google Sheets для {column}: {df.at[index, column]}[/bold green]")
                logger.info(f"Обновлен URL в Google Sheets для {column}: {df.at[index, column]}")
            except Exception as e:
                console.print(f"[bold red]Ошибка загрузки файла {local_file_path} на SFTP: {e}[/bold red]")
                logger.error(f"Ошибка загрузки файла {local_file_path} на SFTP: {str(e)}")
                continue
    
    sftp.close()
    
    # Обновление Google Sheets
    if column == 'WEBP':
        update_google_sheets_webp(sheet_url, df, valid_links.index)
    else:
        update_google_sheets(sheet_url, df, valid_links.index)

def update_google_sheets(sheet_url, df, indexes):
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name(str(get_local_file_path('mythic-hulling-423914-m7-f53ade191c56.json')), scope)
    client = gspread.authorize(creds)
    
    sheet = client.open_by_url(sheet_url)
    worksheet = sheet.get_worksheet(0)
    
    for index in tqdm(indexes, desc="Updating Google Sheets"):
        try:
            worksheet.update_cell(index + 2, df.columns.get_loc('ФОТО') + 1, df.at[index, 'ФОТО'])
            time.sleep(1)
            logger.info(f"Обновлена строка {index + 2} в Google Sheets: ФОТО={df.at[index, 'ФОТО']}")
        except Exception as e:
            console.print(f"[bold red]Ошибка при обновлении Google Sheets для строки {index + 2}: {e}[/bold red]")
            logger.error(f"Ошибка обновления Google Sheets для строки {index + 2}: {str(e)}")

def update_google_sheets_webp(sheet_url, df, indexes):
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name(str(get_local_file_path('mythic-hulling-423914-m7-f53ade191c56.json')), scope)
    client = gspread.authorize(creds)
    
    sheet = client.open_by_url(sheet_url)
    worksheet = sheet.get_worksheet(0)
    
    for index in tqdm(indexes, desc="Updating Google Sheets (WEBP)"):
        try:
            worksheet.update_cell(index + 2, df.columns.get_loc('WEBP') + 1, df.at[index, 'WEBP'])
            time.sleep(1)
            logger.info(f"Обновлена строка {index + 2} в Google Sheets: WEBP={df.at[index, 'WEBP']}")
        except Exception as e:
            console.print(f"[bold red]Ошибка при обновлении Google Sheets для строки {index + 2} (WEBP): {e}[/bold red]")
            logger.error(f"Ошибка обновления Google Sheets для строки {index + 2} (WEBP): {str(e)}")

def upload_to_sftp(local_folder, remote_folder, file_pattern, sftp):
    files = list(local_folder.glob(file_pattern))
    if not files:
        console.print(f"[bold red]Ошибка: файлы с шаблоном {file_pattern} не найдены в {local_folder}[/bold red]")
        logger.error(f"Файлы с шаблоном {file_pattern} не найдены в {local_folder}")
        return

    total_size = sum(os.path.getsize(f) for f in files)

    with Progress(
        TextColumn("[bold blue]{task.description}"),
        BarColumn(bar_width=None, style="green"),
        "[progress.percentage]{task.percentage:>3.1f}%",
        "•",
        TransferSpeedColumn(),
        "•",
        TimeRemainingColumn(),
    ) as progress:
        task = progress.add_task(f"Uploading files to {remote_folder}", total=total_size)
        for local_file in files:
            if not local_file.exists():
                console.print(f"[bold red]Ошибка: файл {local_file} не найден локально![/bold red]")
                logger.error(f"Файл {local_file} не найден локально")
                continue
            
            try:
                file_size = os.path.getsize(local_file)
                with open(local_file, "rb") as file:
                    sftp.putfo(file, f"{remote_folder}/{local_file.name}", callback=lambda sent, total: progress.update(task, advance=sent))
                console.print(f"[bold green]Загружен файл на SFTP: {local_file.name} в {remote_folder}[/bold green]")
                logger.info(f"Загружен файл на SFTP: {local_file.name} в {remote_folder}")
            except Exception as e:
                console.print(f"[bold red]Ошибка при загрузке файла {local_file} в {remote_folder}: {e}[/bold red]")
                logger.error(f"Ошибка при загрузке файла {local_file} в {remote_folder}: {str(e)}")
                continue

def execute_choice(choice, sheet_url, sheet_url_kz, sheet_url_rb, locations, locations_kz, locations_rb):
    # РФ: Пункты 1–9
    if choice == "1":
        console.print("[bold blue]Начало выполнения пункта 1: Сгенерировать файлы для карт (YML - CSV) (РФ)[/bold blue]")
        logger.info("Начало выполнения пункта 1: Сгенерировать файлы для карт (YML - CSV) (РФ)")
        
        csv_path = Path(tempfile.gettempdir()) / 'CSV'
        yandex_yml_path = Path(tempfile.gettempdir()) / 'YANDEX YML'

        if csv_path.exists():
            shutil.rmtree(csv_path)
        if yandex_yml_path.exists():
            shutil.rmtree(yandex_yml_path)

        df = read_google_sheet(sheet_url)
        console.print("[bold blue]Данные из Google Sheets для РФ успешно загружены[/bold blue]")
        
        data = {}
        for file_name in locations:
            filtered_data = filter_excel_data_by_location(df, file_name, locations[file_name][0], currency="RUB")
            if not filtered_data.empty:
                data[file_name] = filtered_data.to_dict('records')
        
        if not data:
            console.print("[bold red]Нет данных для генерации файлов.[/bold red]")
            logger.error("Нет данных для генерации файлов в пункте 1")
            return sheet_url, sheet_url_kz, sheet_url_rb
        
        csv_output_folder = Path(tempfile.gettempdir()) / 'CSV'
        xml_output_folder = Path(tempfile.gettempdir()) / 'YANDEX YML'
        
        generate_csv_files(data, csv_output_folder)
        generate_xml_files(data, xml_output_folder)
        
        console.print("[bold green]Генерация YML и CSV файлов завершена.[/bold green]")
        
        sftp = connect_to_sftp(SFTP_HOST, SFTP_PORT, SFTP_USERNAME, SFTP_PASSWORD)
        
        # Проверка и создание директории для CSV
        ensure_remote_directory_exists(sftp, "/var/www/html/2GIS")
        upload_to_sftp(csv_output_folder, "/var/www/html/2GIS", '2GIS_*.csv', sftp)
        # Проверка и создание директории для YML
        ensure_remote_directory_exists(sftp, "/var/www/html/YANDEX")
        upload_to_sftp(xml_output_folder, "/var/www/html/YANDEX", '*.xml', sftp)
        
        sftp.close()
        console.print("[bold green]Загрузка файлов на SFTP завершена.[/bold green]")
        console.print("[bold blue]Пункт 1 успешно выполнен[/bold blue]")
        logger.info("Пункт 1 успешно выполнен")
        
    elif choice == "2":
        console.print("[bold blue]Начало выполнения пункта 2: Сгенерировать файлы для карт (XLS - CSV) (РФ)[/bold blue]")
        logger.info("Начало выполнения пункта 2: Сгенерировать файлы для карт (XLS - CSV) (РФ)")
        
        csv_path = Path(tempfile.gettempdir()) / 'CSV'
        yandex_xls_path = Path(tempfile.gettempdir()) / 'YANDEX XLS'

        if csv_path.exists():
            shutil.rmtree(csv_path)
        if yandex_xls_path.exists():
            shutil.rmtree(yandex_xls_path)

        df = read_google_sheet(sheet_url)
        console.print("[bold blue]Данные из Google Sheets для РФ успешно загружены[/bold blue]")
        
        data = {}
        for file_name in locations:
            filtered_data = filter_excel_data_by_location(df, file_name, locations[file_name][0], currency="RUB")
            if not filtered_data.empty:
                data[file_name] = filtered_data.to_dict('records')
        
        if not data:
            console.print("[bold red]Нет данных для генерации файлов.[/bold red]")
            logger.error("Нет данных для генерации файлов в пункте 2")
            return sheet_url, sheet_url_kz, sheet_url_rb
        
        csv_output_folder = Path(tempfile.gettempdir()) / 'CSV'
        xls_output_folder = Path(tempfile.gettempdir()) / 'YANDEX XLS'
        
        generate_csv_files(data, csv_output_folder)
        generate_xls_files(data, xls_output_folder)
        
        console.print("[bold green]Генерация XLS и CSV файлов завершена.[/bold green]")
        
        sftp = connect_to_sftp(SFTP_HOST, SFTP_PORT, SFTP_USERNAME, SFTP_PASSWORD)
        
        # Проверка и создание директории для CSV
        ensure_remote_directory_exists(sftp, "/var/www/html/2GIS")
        upload_to_sftp(csv_output_folder, "/var/www/html/2GIS", '2GIS_*.csv', sftp)
        # Проверка и создание директории для XLS
        ensure_remote_directory_exists(sftp, "/var/www/html/YANDEX/XLS")
        upload_to_sftp(xls_output_folder, "/var/www/html/YANDEX/XLS", '*.xls', sftp)
        
        sftp.close()
        console.print("[bold green]Загрузка файлов на SFTP завершена.[/bold green]")
        console.print("[bold blue]Пункт 2 успешно выполнен[/bold blue]")
        logger.info("Пункт 2 успешно выполнен")
        
    elif choice == "3":
        console.print("[bold blue]Начало выполнения пункта 3: Сгенерировать файлы для карт (YML - CSV) с API-ценами (РФ)[/bold blue]")
        logger.info("Начало выполнения пункта 3: Сгенерировать файлы для карт (YML - CSV) с API-ценами (РФ)")
        
        if not check_api_status(API_KEY_RF):
            console.print("[bold red]Прерывание выполнения: API недоступен.[/bold red]")
            logger.error("Прерывание: API недоступен")
            return sheet_url, sheet_url_kz, sheet_url_rb
        
        csv_path = Path(tempfile.gettempdir()) / 'CSV'
        yandex_yml_path = Path(tempfile.gettempdir()) / 'YANDEX YML'

        if csv_path.exists():
            shutil.rmtree(csv_path)
        if yandex_yml_path.exists():
            shutil.rmtree(yandex_yml_path)

        df = read_google_sheet(sheet_url)
        console.print("[bold blue]Данные из Google Sheets успешно загружены[/bold blue]")
        
        data = update_prices_with_api(df, locations, API_KEY_RF, currency="RUB")
        console.print(f"[bold blue]Данные после обработки API: {len(data)} городов[/bold blue]")
        
        if not data:
            console.print("[bold red]Нет данных для генерации файлов.[/bold red]")
            logger.error("Нет данных для генерации файлов в пункте 3")
            return sheet_url, sheet_url_kz, sheet_url_rb
        
        csv_output_folder = Path(tempfile.gettempdir()) / 'CSV'
        xml_output_folder = Path(tempfile.gettempdir()) / 'YANDEX YML'
        
        generate_csv_files(data, csv_output_folder)
        generate_xml_files(data, xml_output_folder)
        
        console.print("[bold green]Генерация YML и CSV файлов завершена.[/bold green]")
        
        sftp = connect_to_sftp(SFTP_HOST, SFTP_PORT, SFTP_USERNAME, SFTP_PASSWORD)
        
        # Проверка и создание директории для CSV
        ensure_remote_directory_exists(sftp, "/var/www/html/2GIS")
        upload_to_sftp(csv_output_folder, "/var/www/html/2GIS", '2GIS_*.csv', sftp)
        # Проверка и создание директории для YML
        ensure_remote_directory_exists(sftp, "/var/www/html/YANDEX")
        upload_to_sftp(xml_output_folder, "/var/www/html/YANDEX", '*.xml', sftp)
        
        sftp.close()
        console.print("[bold green]Загрузка файлов на SFTP завершена.[/bold green]")
        console.print("[bold blue]Пункт 3 успешно выполнен[/bold blue]")
        logger.info("Пункт 3 успешно выполнен")
        
    elif choice == "4":
        console.print("[bold blue]Начало выполнения пункта 4: Просмотр городов (РФ)[/bold blue]")
        logger.info("Начало выполнения пункта 4: Просмотр городов (РФ)")
        view_cities(locations)
        console.print("[bold blue]Пункт 4 успешно выполнен[/bold blue]")
        logger.info("Пункт 4 успешно выполнен")
        
    elif choice == "5":
        console.print("[bold blue]Начало выполнения пункта 5: Просмотр ссылки меню (РФ)[/bold blue]")
        logger.info("Начало выполнения пункта 5: Просмотр ссылки меню (РФ)")
        console.print(f"[bold yellow]Текущая ссылка на меню CHICKO РФ:[/bold yellow] {sheet_url}")
        console.print("[bold blue]Пункт 5 успешно выполнен[/bold blue]")
        logger.info("Пункт 5 успешно выполнен")
        
    elif choice == "6":
        console.print("[bold blue]Начало выполнения пункта 6: Замена ссылки меню (РФ)[/bold blue]")
        logger.info("Начало выполнения пункта 6: Замена ссылки меню (РФ)")
        new_url = input("Введите новую ссылку на меню CHICKO РФ: ")
        update_sheet_url_in_file(get_local_file_path('location_menu.json'), new_url)
        sftp = connect_to_sftp(SFTP_HOST, SFTP_PORT, SFTP_USERNAME, SFTP_PASSWORD)
        upload_file_to_sftp(get_local_file_path('location_menu.json'), REMOTE_PATH, sftp)
        sftp.close()
        console.print("[bold green]Ссылка на меню CHICKO РФ обновлена.[/bold green]")
        console.print("[bold blue]Пункт 6 успешно выполнен[/bold blue]")
        logger.info("Пункт 6 успешно выполнен")
        return new_url, sheet_url_kz, sheet_url_rb
        
    elif choice == "7":
        console.print("[bold blue]Начало выполнения пункта 7: Редактировать URL (РФ)[/bold blue]")
        logger.info("Начало выполнения пункта 7: Редактировать URL (РФ)")
        edit_city_url(locations, "РФ")
        console.print("[bold blue]Пункт 7 успешно выполнен[/bold blue]")
        logger.info("Пункт 7 успешно выполнен")
        
    elif choice == "8":
        console.print("[bold blue]Начало выполнения пункта 8: Обработка фотографий (РФ)[/bold blue]")
        logger.info("Начало выполнения пункта 8: Обработка фотографий (РФ)")
        df = read_google_sheet(sheet_url)
        process_photos(df, sheet_url, SFTP_HOST, SFTP_PORT, SFTP_USERNAME, SFTP_PASSWORD, PHOTO_BASE_PATH, column='ФОТО')
        console.print("[bold green]Обработка фотографий завершена (РФ).[/bold green]")
        console.print("[bold blue]Пункт 8 успешно выполнен[/bold blue]")
        logger.info("Пункт 8 успешно выполнен")
        
    elif choice == "9":
        console.print("[bold blue]Начало выполнения пункта 9: Обработка WEBP фотографий (РФ)[/bold blue]")
        logger.info("Начало выполнения пункта 9: Обработка WEBP фотографий (РФ)")
        df = read_google_sheet(sheet_url)
        process_photos(df, sheet_url, SFTP_HOST, SFTP_PORT, SFTP_USERNAME, SFTP_PASSWORD, PHOTO_BASE_PATH, column='WEBP')
        console.print("[bold green]Обработка WEBP фотографий завершена (РФ).[/bold green]")
        console.print("[bold blue]Пункт 9 успешно выполнен[/bold blue]")
        logger.info("Пункт 9 успешно выполнен")
        
    # КЗ: Пункты 10–18
    elif choice == "10":
        console.print("[bold blue]Начало выполнения пункта 10: Сгенерировать файлы для карт (YML - CSV) (КЗ)[/bold blue]")
        logger.info("Начало выполнения пункта 10: Сгенерировать файлы для карт (YML - CSV) (КЗ)")
        
        csv_path = Path(tempfile.gettempdir()) / 'CSV'
        yandex_yml_path = Path(tempfile.gettempdir()) / 'YANDEX YML'

        if csv_path.exists():
            shutil.rmtree(csv_path)
        if yandex_yml_path.exists():
            shutil.rmtree(yandex_yml_path)

        df = read_google_sheet(sheet_url_kz)
        console.print("[bold blue]Данные из Google Sheets для КЗ успешно загружены[/bold blue]")
        
        data = {}
        for file_name in locations_kz:
            filtered_data = filter_excel_data_by_location(df, file_name, locations_kz[file_name][0], currency="KZT")
            if not filtered_data.empty:
                data[file_name] = filtered_data.to_dict('records')
        
        if not data:
            console.print("[bold red]Нет данных для генерации файлов.[/bold red]")
            logger.error("Нет данных для генерации файлов в пункте 10")
            return sheet_url, sheet_url_kz, sheet_url_rb
        
        csv_output_folder = Path(tempfile.gettempdir()) / 'CSV'
        xml_output_folder = Path(tempfile.gettempdir()) / 'YANDEX YML'
        
        generate_csv_files(data, csv_output_folder, currency="KZT")
        generate_xml_files(data, xml_output_folder, currency="KZT")
        
        console.print("[bold green]Генерация YML и CSV файлов завершена.[/bold green]")
        
        sftp = connect_to_sftp(SFTP_HOST, SFTP_PORT, SFTP_USERNAME, SFTP_PASSWORD)
        
        # Проверка и создание директории для CSV
        ensure_remote_directory_exists(sftp, "/var/www/html/2GIS")
        upload_to_sftp(csv_output_folder, "/var/www/html/2GIS", '2GIS_*.csv', sftp)
        # Проверка и создание директории для YML
        ensure_remote_directory_exists(sftp, "/var/www/html/YANDEX")
        upload_to_sftp(xml_output_folder, "/var/www/html/YANDEX", '*.xml', sftp)
        
        sftp.close()
        console.print("[bold green]Загрузка файлов на SFTP завершена.[/bold green]")
        console.print("[bold blue]Пункт 10 успешно выполнен[/bold blue]")
        logger.info("Пункт 10 успешно выполнен")
        
    elif choice == "11":
        console.print("[bold blue]Начало выполнения пункта 11: Сгенерировать файлы для карт (XLS - CSV) (КЗ)[/bold blue]")
        logger.info("Начало выполнения пункта 11: Сгенерировать файлы для карт (XLS - CSV) (КЗ)")
        
        csv_path = Path(tempfile.gettempdir()) / 'CSV'
        yandex_xls_path = Path(tempfile.gettempdir()) / 'YANDEX XLS'

        if csv_path.exists():
            shutil.rmtree(csv_path)
        if yandex_xls_path.exists():
            shutil.rmtree(yandex_xls_path)

        df = read_google_sheet(sheet_url_kz)
        console.print("[bold blue]Данные из Google Sheets для КЗ успешно загружены[/bold blue]")
        
        data = {}
        for file_name in locations_kz:
            filtered_data = filter_excel_data_by_location(df, file_name, locations_kz[file_name][0], currency="KZT")
            if not filtered_data.empty:
                data[file_name] = filtered_data.to_dict('records')
        
        if not data:
            console.print("[bold red]Нет данных для генерации файлов.[/bold red]")
            logger.error("Нет данных для генерации файлов в пункте 11")
            return sheet_url, sheet_url_kz, sheet_url_rb
        
        csv_output_folder = Path(tempfile.gettempdir()) / 'CSV'
        xls_output_folder = Path(tempfile.gettempdir()) / 'YANDEX XLS'
        
        generate_csv_files(data, csv_output_folder, currency="KZT")
        generate_xls_files(data, xls_output_folder, currency="KZT")
        
        console.print("[bold green]Генерация XLS и CSV файлов завершена.[/bold green]")
        
        sftp = connect_to_sftp(SFTP_HOST, SFTP_PORT, SFTP_USERNAME, SFTP_PASSWORD)
        
        # Проверка и создание директории для CSV
        ensure_remote_directory_exists(sftp, "/var/www/html/2GIS")
        upload_to_sftp(csv_output_folder, "/var/www/html/2GIS", '2GIS_*.csv', sftp)
        # Проверка и создание директории для XLS
        ensure_remote_directory_exists(sftp, "/var/www/html/YANDEX/XLS")
        upload_to_sftp(xls_output_folder, "/var/www/html/YANDEX/XLS", '*.xls', sftp)
        
        sftp.close()
        console.print("[bold green]Загрузка файлов на SFTP завершена.[/bold green]")
        console.print("[bold blue]Пункт 11 успешно выполнен[/bold blue]")
        logger.info("Пункт 11 успешно выполнен")
        
    elif choice == "12":
        console.print("[bold blue]Начало выполнения пункта 12: Сгенерировать файлы для карт (YML - CSV) с API-ценами (КЗ)[/bold blue]")
        logger.info("Начало выполнения пункта 12: Сгенерировать файлы для карт (YML - CSV) с API-ценами (КЗ)")
        
        if not check_api_status(API_KEY_KZ):
            console.print("[bold red]Прерывание выполнения: API недоступен.[/bold red]")
            logger.error("Прерывание: API недоступен")
            return sheet_url, sheet_url_kz, sheet_url_rb
        
        csv_path = Path(tempfile.gettempdir()) / 'CSV'
        yandex_yml_path = Path(tempfile.gettempdir()) / 'YANDEX YML'

        if csv_path.exists():
            shutil.rmtree(csv_path)
        if yandex_yml_path.exists():
            shutil.rmtree(yandex_yml_path)

        df = read_google_sheet(sheet_url_kz)
        console.print("[bold blue]Данные из Google Sheets успешно загружены[/bold blue]")
        
        data = update_prices_with_api(df, locations_kz, API_KEY_KZ, currency="KZT")
        console.print(f"[bold blue]Данные после обработки API: {len(data)} городов[/bold blue]")
        
        if not data:
            console.print("[bold red]Нет данных для генерации файлов.[/bold red]")
            logger.error("Нет данных для генерации файлов в пункте 12")
            return sheet_url, sheet_url_kz, sheet_url_rb
        
        csv_output_folder = Path(tempfile.gettempdir()) / 'CSV'
        xml_output_folder = Path(tempfile.gettempdir()) / 'YANDEX YML'
        
        generate_csv_files(data, csv_output_folder, currency="KZT")
        generate_xml_files(data, xml_output_folder, currency="KZT")
        
        console.print("[bold green]Генерация YML и CSV файлов завершена.[/bold green]")
        
        sftp = connect_to_sftp(SFTP_HOST, SFTP_PORT, SFTP_USERNAME, SFTP_PASSWORD)
        
        # Проверка и создание директории для CSV
        ensure_remote_directory_exists(sftp, "/var/www/html/2GIS")
        upload_to_sftp(csv_output_folder, "/var/www/html/2GIS", '2GIS_*.csv', sftp)
        # Проверка и создание директории для YML
        ensure_remote_directory_exists(sftp, "/var/www/html/YANDEX")
        upload_to_sftp(xml_output_folder, "/var/www/html/YANDEX", '*.xml', sftp)
        
        sftp.close()
        console.print("[bold green]Загрузка файлов на SFTP завершена.[/bold green]")
        console.print("[bold blue]Пункт 12 успешно выполнен[/bold blue]")
        logger.info("Пункт 12 успешно выполнен")
        
    elif choice == "13":
        console.print("[bold blue]Начало выполнения пункта 13: Просмотреть все города (КЗ)[/bold blue]")
        logger.info("Начало выполнения пункта 13: Просмотреть все города (КЗ)")
        view_cities(locations_kz)
        console.print("[bold blue]Пункт 13 успешно выполнен[/bold blue]")
        logger.info("Пункт 13 успешно выполнен")
        
    elif choice == "14":
        console.print("[bold blue]Начало выполнения пункта 14: Просмотреть ссылку меню (КЗ)[/bold blue]")
        logger.info("Начало выполнения пункта 14: Просмотреть ссылку меню (КЗ)")
        console.print(f"[bold yellow]Текущая ссылка на меню CHICKO КЗ:[/bold yellow] {sheet_url_kz}")
        console.print("[bold blue]Пункт 14 успешно выполнен[/bold blue]")
        logger.info("Пункт 14 успешно выполнен")
        
    elif choice == "15":
        console.print("[bold blue]Начало выполнения пункта 15: Заменить ссылку на меню CHICKO (КЗ)[/bold blue]")
        logger.info("Начало выполнения пункта 15: Заменить ссылку на меню CHICKO (КЗ)")
        new_url = input("Введите новую ссылку на меню CHICKO КЗ: ")
        update_sheet_url_in_file(get_local_file_path('location_menu_kz.json'), new_url)
        sftp = connect_to_sftp(SFTP_HOST, SFTP_PORT, SFTP_USERNAME, SFTP_PASSWORD)
        upload_file_to_sftp(get_local_file_path('location_menu_kz.json'), REMOTE_PATH, sftp)
        sftp.close()
        console.print("[bold green]Ссылка на меню CHICKO КЗ обновлена.[/bold green]")
        console.print("[bold blue]Пункт 15 успешно выполнен[/bold blue]")
        logger.info("Пункт 15 успешно выполнен")
        return sheet_url, new_url, sheet_url_rb
        
    elif choice == "16":
        console.print("[bold blue]Начало выполнения пункта 16: Редактировать URL (КЗ)[/bold blue]")
        logger.info("Начало выполнения пункта 16: Редактировать URL (КЗ)")
        edit_city_url(locations_kz, "КЗ")
        console.print("[bold blue]Пункт 16 успешно выполнен[/bold blue]")
        logger.info("Пункт 16 успешно выполнен")
        
    elif choice == "17":
        console.print("[bold blue]Начало выполнения пункта 17: Обработать фотографии с Яндекс.Диск (КЗ)[/bold blue]")
        logger.info("Начало выполнения пункта 17: Обработать фотографии с Яндекс.Диск (КЗ)")
        df = read_google_sheet(sheet_url_kz)
        process_photos(df, sheet_url_kz, SFTP_HOST, SFTP_PORT, SFTP_USERNAME, SFTP_PASSWORD, PHOTO_BASE_PATH_KZ, column='ФОТО')
        console.print("[bold green]Обработка фотографий завершена (КЗ).[/bold green]")
        console.print("[bold blue]Пункт 17 успешно выполнен[/bold blue]")
        logger.info("Пункт 17 успешно выполнен")
        
    elif choice == "18":
        console.print("[bold blue]Начало выполнения пункта 18: Обработать WEBP фото с Яндекс.Диск (КЗ)[/bold blue]")
        logger.info("Начало выполнения пункта 18: Обработать WEBP фото с Яндекс.Диск (КЗ)")
        df = read_google_sheet(sheet_url_kz)
        process_photos(df, sheet_url_kz, SFTP_HOST, SFTP_PORT, SFTP_USERNAME, SFTP_PASSWORD, PHOTO_BASE_PATH_KZ, column='WEBP')
        console.print("[bold green]Обработка WEBP фотографий завершена (КЗ).[/bold green]")
        console.print("[bold blue]Пункт 18 успешно выполнен[/bold blue]")
        logger.info("Пункт 18 успешно выполнен")
        
    # РБ: Пункты 19–27
    elif choice == "19":
        console.print("[bold blue]Начало выполнения пункта 19: Сгенерировать файлы для карт (YML - CSV) (РБ)[/bold blue]")
        logger.info("Начало выполнения пункта 19: Сгенерировать файлы для карт (YML - CSV) (РБ)")
        
        csv_path = Path(tempfile.gettempdir()) / 'CSV'
        yandex_yml_path = Path(tempfile.gettempdir()) / 'YANDEX YML'

        if csv_path.exists():
            shutil.rmtree(csv_path)
        if yandex_yml_path.exists():
            shutil.rmtree(yandex_yml_path)

        df = read_google_sheet(sheet_url_rb)
        console.print("[bold blue]Данные из Google Sheets для РБ успешно загружены[/bold blue]")
        
        data = {}
        for file_name in locations_rb:
            filtered_data = filter_excel_data_by_location(df, file_name, locations_rb[file_name][0], currency="BYN")
            if not filtered_data.empty:
                data[file_name] = filtered_data.to_dict('records')
        
        if not data:
            console.print("[bold red]Нет данных для генерации файлов.[/bold red]")
            logger.error("Нет данных для генерации файлов в пункте 19")
            return sheet_url, sheet_url_kz, sheet_url_rb
        
        csv_output_folder = Path(tempfile.gettempdir()) / 'CSV'
        xml_output_folder = Path(tempfile.gettempdir()) / 'YANDEX YML'
        
        generate_csv_files(data, csv_output_folder, currency="BYN")
        generate_xml_files(data, xml_output_folder, currency="BYN")
        
        console.print("[bold green]Генерация YML и CSV файлов завершена.[/bold green]")
        
        sftp = connect_to_sftp(SFTP_HOST, SFTP_PORT, SFTP_USERNAME, SFTP_PASSWORD)
        
        # Проверка и создание директории для CSV
        ensure_remote_directory_exists(sftp, "/var/www/html/2GIS")
        upload_to_sftp(csv_output_folder, "/var/www/html/2GIS", '2GIS_*.csv', sftp)
        # Проверка и создание директории для YML
        ensure_remote_directory_exists(sftp, "/var/www/html/YANDEX")
        upload_to_sftp(xml_output_folder, "/var/www/html/YANDEX", '*.xml', sftp)
        
        sftp.close()
        console.print("[bold green]Загрузка файлов на SFTP завершена.[/bold green]")
        console.print("[bold blue]Пункт 19 успешно выполнен[/bold blue]")
        logger.info("Пункт 19 успешно выполнен")
        
    elif choice == "20":
        console.print("[bold blue]Начало выполнения пункта 20: Сгенерировать файлы для карт (XLS - CSV) (РБ)[/bold blue]")
        logger.info("Начало выполнения пункта 20: Сгенерировать файлы для карт (XLS - CSV) (РБ)")
        
        csv_path = Path(tempfile.gettempdir()) / 'CSV'
        yandex_xls_path = Path(tempfile.gettempdir()) / 'YANDEX XLS'

        if csv_path.exists():
            shutil.rmtree(csv_path)
        if yandex_xls_path.exists():
            shutil.rmtree(yandex_xls_path)

        df = read_google_sheet(sheet_url_rb)
        console.print("[bold blue]Данные из Google Sheets для РБ успешно загружены[/bold blue]")
        
        data = {}
        for file_name in locations_rb:
            filtered_data = filter_excel_data_by_location(df, file_name, locations_rb[file_name][0], currency="BYN")
            if not filtered_data.empty:
                data[file_name] = filtered_data.to_dict('records')
        
        if not data:
            console.print("[bold red]Нет данных для генерации файлов.[/bold red]")
            logger.error("Нет данных для генерации файлов в пункте 20")
            return sheet_url, sheet_url_kz, sheet_url_rb
        
        csv_output_folder = Path(tempfile.gettempdir()) / 'CSV'
        xls_output_folder = Path(tempfile.gettempdir()) / 'YANDEX XLS'
        
        generate_csv_files(data, csv_output_folder, currency="BYN")
        generate_xls_files(data, xls_output_folder, currency="BYN")
        
        console.print("[bold green]Генерация XLS и CSV файлов завершена.[/bold green]")
        
        sftp = connect_to_sftp(SFTP_HOST, SFTP_PORT, SFTP_USERNAME, SFTP_PASSWORD)
        
        # Проверка и создание директории для CSV
        ensure_remote_directory_exists(sftp, "/var/www/html/2GIS")
        upload_to_sftp(csv_output_folder, "/var/www/html/2GIS", '2GIS_*.csv', sftp)
        # Проверка и создание директории для XLS
        ensure_remote_directory_exists(sftp, "/var/www/html/YANDEX/XLS")
        upload_to_sftp(xls_output_folder, "/var/www/html/YANDEX/XLS", '*.xls', sftp)
        
        sftp.close()
        console.print("[bold green]Загрузка файлов на SFTP завершена.[/bold green]")
        console.print("[bold blue]Пункт 20 успешно выполнен[/bold blue]")
        logger.info("Пункт 20 успешно выполнен")
        
    elif choice == "21":
        console.print("[bold blue]Начало выполнения пункта 21: Сгенерировать файлы для карт (YML - CSV) с API-ценами (РБ)[/bold blue]")
        logger.info("Начало выполнения пункта 21: Сгенерировать файлы для карт (YML - CSV) с API-ценами (РБ)")
        
        if not check_api_status(API_KEY_RB):
            console.print("[bold red]Прерывание выполнения: API недоступен.[/bold red]")
            logger.error("Прерывание: API недоступен")
            return sheet_url, sheet_url_kz, sheet_url_rb
        
        csv_path = Path(tempfile.gettempdir()) / 'CSV'
        yandex_yml_path = Path(tempfile.gettempdir()) / 'YANDEX YML'

        if csv_path.exists():
            shutil.rmtree(csv_path)
        if yandex_yml_path.exists():
            shutil.rmtree(yandex_yml_path)

        df = read_google_sheet(sheet_url_rb)
        console.print("[bold blue]Данные из Google Sheets успешно загружены[/bold blue]")
        
        data = update_prices_with_api(df, locations_rb, API_KEY_RB, currency="BYN")
        console.print(f"[bold blue]Данные после обработки API: {len(data)} городов[/bold blue]")
        
        if not data:
            console.print("[bold red]Нет данных для генерации файлов.[/bold red]")
            logger.error("Нет данных для генерации файлов в пункте 21")
            return sheet_url, sheet_url_kz, sheet_url_rb
        
        csv_output_folder = Path(tempfile.gettempdir()) / 'CSV'
        xml_output_folder = Path(tempfile.gettempdir()) / 'YANDEX YML'
        
        generate_csv_files(data, csv_output_folder, currency="BYN")
        generate_xml_files(data, xml_output_folder, currency="BYN")
        
        console.print("[bold green]Генерация YML и CSV файлов завершена.[/bold green]")
        
        sftp = connect_to_sftp(SFTP_HOST, SFTP_PORT, SFTP_USERNAME, SFTP_PASSWORD)
        
        # Проверка и создание директории для CSV
        ensure_remote_directory_exists(sftp, "/var/www/html/2GIS")
        upload_to_sftp(csv_output_folder, "/var/www/html/2GIS", '2GIS_*.csv', sftp)
        # Проверка и создание директории для YML
        ensure_remote_directory_exists(sftp, "/var/www/html/YANDEX")
        upload_to_sftp(xml_output_folder, "/var/www/html/YANDEX", '*.xml', sftp)
        
        sftp.close()
        console.print("[bold green]Загрузка файлов на SFTP завершена.[/bold green]")
        console.print("[bold blue]Пункт 21 успешно выполнен[/bold blue]")
        logger.info("Пункт 21 успешно выполнен")
        
    elif choice == "22":
        console.print("[bold blue]Начало выполнения пункта 22: Просмотреть все города (РБ)[/bold blue]")
        logger.info("Начало выполнения пункта 22: Просмотреть все города (РБ)")
        view_cities(locations_rb)
        console.print("[bold blue]Пункт 22 успешно выполнен[/bold blue]")
        logger.info("Пункт 22 успешно выполнен")
        
    elif choice == "23":
        console.print("[bold blue]Начало выполнения пункта 23: Просмотреть ссылку меню (РБ)[/bold blue]")
        logger.info("Начало выполнения пункта 23: Просмотреть ссылку меню (РБ)")
        console.print(f"[bold yellow]Текущая ссылка на меню CHICKO РБ:[/bold yellow] {sheet_url_rb}")
        console.print("[bold blue]Пункт 23 успешно выполнен[/bold blue]")
        logger.info("Пункт 23 успешно выполнен")
        
    elif choice == "24":
        console.print("[bold blue]Начало выполнения пункта 24: Заменить ссылку на меню CHICKO (РБ)[/bold blue]")
        logger.info("Начало выполнения пункта 24: Заменить ссылку на меню CHICKO (РБ)")
        new_url = input("Введите новую ссылку на меню CHICKO РБ: ")
        update_sheet_url_in_file(get_local_file_path('location_menu_rb.json'), new_url)
        sftp = connect_to_sftp(SFTP_HOST, SFTP_PORT, SFTP_USERNAME, SFTP_PASSWORD)
        upload_file_to_sftp(get_local_file_path('location_menu_rb.json'), REMOTE_PATH, sftp)
        sftp.close()
        console.print("[bold green]Ссылка на меню CHICKO РБ обновлена.[/bold green]")
        console.print("[bold blue]Пункт 24 успешно выполнен[/bold blue]")
        logger.info("Пункт 24 успешно выполнен")
        return sheet_url, sheet_url_kz, new_url
        
    elif choice == "25":
        console.print("[bold blue]Начало выполнения пункта 25: Редактировать URL (РБ)[/bold blue]")
        logger.info("Начало выполнения пункта 25: Редактировать URL (РБ)")
        edit_city_url(locations_rb, "РБ")
        console.print("[bold blue]Пункт 25 успешно выполнен[/bold blue]")
        logger.info("Пункт 25 успешно выполнен")
        
    elif choice == "26":
        console.print("[bold blue]Начало выполнения пункта 26: Обработать фотографии с Яндекс.Диск (РБ)[/bold blue]")
        logger.info("Начало выполнения пункта 26: Обработать фотографии с Яндекс.Диск (РБ)")
        df = read_google_sheet(sheet_url_rb)
        process_photos(df, sheet_url_rb, SFTP_HOST, SFTP_PORT, SFTP_USERNAME, SFTP_PASSWORD, PHOTO_BASE_PATH_RB, column='ФОТО')
        console.print("[bold green]Обработка фотографий завершена (РБ).[/bold green]")
        console.print("[bold blue]Пункт 26 успешно выполнен[/bold blue]")
        logger.info("Пункт 26 успешно выполнен")
        
    elif choice == "27":
        console.print("[bold blue]Начало выполнения пункта 27: Обработать WEBP фото с Яндекс.Диск (РБ)[/bold blue]")
        logger.info("Начало выполнения пункта 27: Обработать WEBP фото с Яндекс.Диск (РБ)")
        df = read_google_sheet(sheet_url_rb)
        process_photos(df, sheet_url_rb, SFTP_HOST, SFTP_PORT, SFTP_USERNAME, SFTP_PASSWORD, PHOTO_BASE_PATH_RB, column='WEBP')
        console.print("[bold green]Обработка WEBP фотографий завершена (РБ).[/bold green]")
        console.print("[bold blue]Пункт 27 успешно выполнен[/bold blue]")
        logger.info("Пункт 27 успешно выполнен")
        
    # Общие пункты
    elif choice == "28":
        console.print("[bold blue]Начало выполнения пункта 28: Добавить задачу в планировщик[/bold blue]")
        logger.info("Начало выполнения пункта 28: Добавить задачу в планировщик")
        add_scheduled_task()
        console.print("[bold green]Пункт 28 успешно выполнен[/bold green]")
        logger.info("Пункт 28 успешно выполнен")
        
    elif choice == "29":
        console.print("[bold blue]Выход из программы[/bold blue]")
        logger.info("Выход из программы")
        sys.exit()
        
    else:
        console.print("[bold red]Неверный выбор. Пожалуйста, выберите действие из меню.[/bold red]")
        logger.warning(f"Неверный выбор: {choice}")
    
    return sheet_url, sheet_url_kz, sheet_url_rb

def add_scheduled_task():
    task_name = "CHICKO UPDATE MENU"
    commands = input("Введите команды через пробел: ")
    frequency_choice = input("Выберите частоту (1 - ежедневно, 2 - еженедельно, 3 - ежемесячно): ").strip()
    time_input = input("Введите время (в формате 24 часа, например, 14:30): ").strip()

    frequency_map = {
        "1": "daily",
        "2": "weekly",
        "3": "monthly"
    }
    frequency = frequency_map.get(frequency_choice)

    if not frequency:
        console.print("[bold red]Неверный выбор частоты.[/bold red]")
        logger.error("Неверный выбор частоты")
        return

    exe_path = sys.executable

    cmd = f'schtasks /create /tn "{task_name}" /tr "{exe_path} -c {commands}" /sc {frequency} /st {time_input}'

    try:
        subprocess.run(cmd, check=True, shell=True)
        console.print("[bold green]Задача успешно добавлена в планировщик.[/bold green]")
        logger.info(f"Задача '{task_name}' успешно добавлена в планировщик с частотой {frequency} в {time_input}")
    except subprocess.CalledProcessError as e:
        console.print(f"[bold red]Ошибка при добавлении задачи в планировщик: {e}[/bold red]")
        logger.error(f"Ошибка добавления задачи в планировщик: {str(e)}")

def main(commands=None):
    try:
        creds_file = download_from_sftp('mythic-hulling-423914-m7-f53ade191c56.json')
        locations_file = download_from_sftp('locations.json')
        menu_file = download_from_sftp('location_menu.json')
        locations_kz_file = download_from_sftp('locations_kz.json')
        menu_kz_file = download_from_sftp('location_menu_kz.json')
        locations_rb_file = download_from_sftp('locations_rb.json')
        menu_rb_file = download_from_sftp('location_menu_rb.json')

        if creds_file is None:
            console.print("[bold red]Не удалось скачать файл учетных данных. Скрипт не может продолжить работу.[/bold red]")
            logger.error("Не удалось скачать файл учетных данных")
            sys.exit(1)

        sheet_url = load_sheet_url_from_file(get_local_file_path('location_menu.json')) if menu_file else ""
        sheet_url_kz = load_sheet_url_from_file(get_local_file_path('location_menu_kz.json')) if menu_kz_file else ""
        sheet_url_rb = load_sheet_url_from_file(get_local_file_path('location_menu_rb.json')) if menu_rb_file else ""
        locations = load_locations_from_file(get_local_file_path('locations.json')) if locations_file else {}
        locations_kz = load_locations_from_file(get_local_file_path('locations_kz.json')) if locations_kz_file else {}
        locations_rb = load_locations_from_file(get_local_file_path('locations_rb.json')) if locations_rb_file else {}

        # Проверка новых городов
        locations = check_new_cities(sheet_url, get_local_file_path('locations.json'))
        locations_kz = check_new_cities(sheet_url_kz, get_local_file_path('locations_kz.json'))
        locations_rb = check_new_cities(sheet_url_rb, get_local_file_path('locations_rb.json'))

        if commands:
            for choice in commands:
                try:
                    sheet_url, sheet_url_kz, sheet_url_rb = execute_choice(choice, sheet_url, sheet_url_kz, sheet_url_rb, locations, locations_kz, locations_rb)
                except Exception as e:
                    console.print(f"[bold red]Ошибка при выполнении команды {choice}: {e}[/bold red]")
                    logger.error(f"Ошибка при выполнении команды {choice}: {str(e)}")
        else:
            while True:
                console.print("+-----------------------------------------+")
                console.print("| Меню CHICKO РФ:                         |")
                console.print("+-----------------------------------------+")
                console.print("| 1.  Сгенерировать файлы для карт (YML - CSV)     |")
                console.print("| 2.  Сгенерировать файлы для карт (XLS - CSV)     |")
                console.print("| 3.  Сгенерировать файлы (YML - CSV) с API-ценами |")
                console.print("| 4.  Просмотреть все города                       |")
                console.print("| 5.  Посмотреть ссылку меню CHICKO               |")
                console.print("| 6.  Заменить ссылку на меню CHICKO              |")
                console.print("| 7.  Редактировать URL                           |")
                console.print("| 8.  Обработать фотографии с Яндекс.Диск         |")
                console.print("| 9.  Обработать WEBP фото с Яндекс.Диск          |")
                console.print("+-----------------------------------------+")
                console.print("| Меню CHICKO КЗ:                         |")
                console.print("+-----------------------------------------+")
                console.print("| 10. Сгенерировать файлы для карт (YML - CSV)     |")
                console.print("| 11. Сгенерировать файлы для карт (XLS - CSV)     |")
                console.print("| 12. Сгенерировать файлы (YML - CSV) с API-ценами |")
                console.print("| 13. Просмотреть все города                       |")
                console.print("| 14. Посмотреть ссылку меню CHICKO               |")
                console.print("| 15. Заменить ссылку на меню CHICKO              |")
                console.print("| 16. Редактировать URL                           |")
                console.print("| 17. Обработать фотографии с Яндекс.Диск         |")
                console.print("| 18. Обработать WEBP фото с Яндекс.Диск          |")
                console.print("+-----------------------------------------+")
                console.print("| Меню CHICKO РБ:                         |")
                console.print("+-----------------------------------------+")
                console.print("| 19. Сгенерировать файлы для карт (YML - CSV)     |")
                console.print("| 20. Сгенерировать файлы для карт (XLS - CSV)     |")
                console.print("| 21. Сгенерировать файлы (YML - CSV) с API-ценами |")
                console.print("| 22. Просмотреть все города                       |")
                console.print("| 23. Посмотреть ссылку меню CHICKO               |")
                console.print("| 24. Заменить ссылку на меню CHICKO              |")
                console.print("| 25. Редактировать URL                           |")
                console.print("| 26. Обработать фотографии с Яндекс.Диск         |")
                console.print("| 27. Обработать WEBP фото с Яндекс.Диск          |")
                console.print("+-----------------------------------------+")
                console.print("| 28. Добавить планировщик                        |")
                console.print("| 29. Выход                                       |")
                console.print("+-----------------------------------------+")

                choices = input("Введите номера опций через пробел: ").split()

                for choice in choices:
                    try:
                        sheet_url, sheet_url_kz, sheet_url_rb = execute_choice(choice, sheet_url, sheet_url_kz, sheet_url_rb, locations, locations_kz, locations_rb)
                    except Exception as e:
                        console.print(f"[bold red]Ошибка при выполнении команды {choice}: {e}[/bold red]")
                        logger.error(f"Ошибка при выполнении команды {choice}: {str(e)}")
    except Exception as e:
        console.print(f"[bold red]Общая ошибка в main: {e}[/bold red]")
        logger.error(f"Общая ошибка в main: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Execute CHICKO script commands.")
    parser.add_argument(
        '-c', '--commands', 
        nargs='+', 
        help="List of commands to execute"
    )
    args = parser.parse_args()
    main(args.commands)
