import os
import json
import time
import tkinter as tk
from tkinter import ttk, messagebox
import paramiko
import logging
import subprocess
import pyautogui
import requests
import socket
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.edge.options import Options as EdgeOptions
from selenium.webdriver.edge.service import Service
from selenium.common.exceptions import TimeoutException, NoSuchElementException, NoSuchWindowException
import pywinauto
import keyboard
import webbrowser
import zipfile

# Настройка логов
log_file = 'automation.log'
if os.path.exists(log_file):
    with open(log_file, 'w') as f:
        f.truncate(0)
logger = logging.getLogger(__name__)
logging.basicConfig(filename=log_file, level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
logger.info("Лог-файл инициализирован")

# Глобальная конфигурация SFTP
sftp_config = {
    "sftp_host": os.getenv("SFTP_HOST", "mesh.chicko.me"),
    "sftp_port": int(os.getenv("SFTP_PORT", 22)),
    "sftp_user": os.getenv("SFTP_USER", "test"),
    "sftp_pass": os.getenv("SFTP_PASS", "test"),
    "sftp_remote_path": os.getenv("SFTP_REMOTE_PATH", "/var/www/html/YANDEX/XLS/"),
    "sftp_mapping_path": os.getenv("SFTP_MAPPING_PATH", "/var/www/html/auth_menu/")
}

# Автоматическая установка библиотек
def install_requirements():
    required_libs = ["selenium", "webdriver-manager", "paramiko", "pyautogui", "pywinauto", "keyboard", "requests"]
    for lib in required_libs:
        try:
            __import__(lib)
            logger.debug(f"Библиотека {lib} уже установлена")
        except ImportError as e:
            logger.info(f"Установка библиотеки {lib}...")
            try:
                subprocess.check_call(["pip", "install", lib], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                logger.info(f"Библиотека {lib} успешно установлена")
            except subprocess.CalledProcessError as e:
                logger.error(f"Не удалось установить {lib}: {str(e)}")
                messagebox.showerror("Ошибка", f"Не удалось установить {lib}. Выполните 'pip install {lib}' вручную.")
                return False
    return True

if not install_requirements():
    exit(1)

class AutomationApp:
    def __init__(self, root):
        logger.debug("Инициализация класса AutomationApp")
        self.root = root
        logger.debug(f"Установлен корневой виджет: {root}")
        self.root.title("Автоматизация Яндекс.Карты")
        logger.debug("Установлено название окна")
        self.root.geometry("1200x900")  # Начальный размер окна
        logger.debug("Установлены начальные размеры окна: 1200x900")
        self.root.configure(bg="#f0f4f8")  # Современный светлый фон
        logger.debug("Настроен фон окна")

        # Состояние проверки драйвера
        self.driver_checked = False
        logger.debug("Инициализировано состояние проверки драйвера: False")

        # Загрузка настроек и файлов из yabussines.json с SFTP
        self.timing_settings, self.pause_hotkey, self.files = self.load_timing_settings_from_sftp()
        logger.debug(f"Загружены настройки: {self.timing_settings}, горячая клавиша: {self.pause_hotkey}, файлов: {len(self.files)}")

        # Фрейм для статуса драйвера
        self.driver_frame = ttk.Frame(self.root)
        logger.debug("Создан фрейм для статуса драйвера")
        self.driver_frame.pack(pady=5, fill=tk.X)
        logger.debug("Фрейм для статуса драйвера упакован с pady=5, fill=tk.X")
        self.driver_status_label = ttk.Label(self.driver_frame, text="Статус драйвера:", foreground="black")
        logger.debug("Создан лейбл статуса драйвера")
        self.driver_status_label.pack(side=tk.LEFT, padx=5)
        logger.debug("Лейбл статуса драйвера упакован")
        self.driver_status_value = ttk.Label(self.driver_frame, text="Проверка...", foreground="black")
        logger.debug("Создано значение статуса драйвера")
        self.driver_status_value.pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        logger.debug("Значение статуса драйвера упаковано")
        self.driver_buttons_frame = ttk.Frame(self.driver_frame)
        logger.debug("Создан фрейм для кнопок драйвера")
        self.driver_buttons_frame.pack(pady=5, fill=tk.X)
        logger.debug("Фрейм для кнопок драйвера упакован")
        self.check_driver_button = ttk.Button(self.driver_buttons_frame, text="Проверить", command=self.check_driver_status)
        logger.debug("Создано кнопка 'Проверить' драйвер")
        self.check_driver_button.pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        logger.debug("Кнопка 'Проверить' упакована")
        self.install_driver_button = ttk.Button(self.driver_buttons_frame, text="Установить", command=self.install_driver)
        logger.debug("Создано кнопка 'Установить' драйвер")
        self.install_driver_button.pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        logger.debug("Кнопка 'Установить' упакована")
        if not self.check_driver():
            self.driver_status_value.config(text="Отсутствует", foreground="red")
            logger.warning("Драйвер не найден при запуске")

        # Основной панельный контейнер для двух половин
        self.main_pane = ttk.PanedWindow(root, orient=tk.HORIZONTAL)
        logger.debug("Создан основной панельный контейнер ttk.PanedWindow")
        self.main_pane.pack(fill=tk.BOTH, expand=True)
        logger.debug("Панельный контейнер упакован")

        # Левая половина (статусы, кнопка Старт и настройки)
        self.left_frame = ttk.Frame(self.main_pane)
        logger.debug("Создан левый фрейм")
        self.main_pane.add(self.left_frame)
        logger.debug("Левый фрейм добавлен в панельный контейнер")

        # Фрейм для статусов
        self.status_frame = ttk.Frame(self.left_frame, padding="5")
        logger.debug("Создан фрейм для статусов")
        self.status_frame.pack(pady=5, fill=tk.BOTH, expand=True)
        logger.debug("Фрейм для статусов упакован")
        self.status_frame.columnconfigure(0, weight=1)
        self.status_frame.columnconfigure(1, weight=3)
        self.status_frame.columnconfigure(2, weight=1)
        logger.debug("Настроены веса колонок фрейма статусов")

        self.file_status = ttk.Label(self.status_frame, text="Загрузка файлов: Ожидание", foreground="black")
        logger.debug("Создан лейбл статуса загрузки файлов")
        self.file_status.grid(row=0, column=0, padx=5, pady=2, sticky="nsew")
        logger.debug("Лейбл статуса загрузки файлов размещён")
        self.file_progress = ttk.Progressbar(self.status_frame, mode="determinate", maximum=100)
        logger.debug("Создан прогресс-бар для загрузки файлов")
        self.file_progress.grid(row=0, column=1, padx=5, pady=2, sticky="nsew")
        logger.debug("Прогресс-бар для загрузки файлов размещён")
        self.file_status_ok = ttk.Label(self.status_frame, text="", foreground="green")
        logger.debug("Создан индикатор OK для загрузки файлов")
        self.file_status_ok.grid(row=0, column=2, padx=5, pady=2, sticky="nsew")
        logger.debug("Индикатор OK для загрузки файлов размещён")

        self.auth_status = ttk.Label(self.status_frame, text="Авторизация: Ожидание", foreground="black")
        logger.debug("Создан лейбл статуса авторизации")
        self.auth_status.grid(row=1, column=0, padx=5, pady=2, sticky="nsew")
        logger.debug("Лейбл статуса авторизации размещён")
        self.auth_progress = ttk.Progressbar(self.status_frame, mode="determinate", maximum=100)
        logger.debug("Создан прогресс-бар для авторизации")
        self.auth_progress.grid(row=1, column=1, padx=5, pady=2, sticky="nsew")
        logger.debug("Прогресс-бар для авторизации размещён")
        self.auth_status_ok = ttk.Label(self.status_frame, text="", foreground="green")
        logger.debug("Создан индикатор OK для авторизации")
        self.auth_status_ok.grid(row=1, column=2, padx=5, pady=2, sticky="nsew")
        logger.debug("Индикатор OK для авторизации размещён")

        self.upload_status = ttk.Label(self.status_frame, text="Статус загрузки файлов: Ожидание", foreground="black")
        logger.debug("Создан лейбл статуса загрузки файлов")
        self.upload_status.grid(row=2, column=0, padx=5, pady=2, sticky="nsew")
        logger.debug("Лейбл статуса загрузки файлов размещён")
        self.upload_progress = ttk.Progressbar(self.status_frame, mode="determinate", maximum=100)
        logger.debug("Создан прогресс-бар для загрузки файлов")
        self.upload_progress.grid(row=2, column=1, padx=5, pady=2, sticky="nsew")
        logger.debug("Прогресс-бар для загрузки файлов размещён")
        self.upload_status_ok = ttk.Label(self.status_frame, text="", foreground="green")
        logger.debug("Создан индикатор OK для загрузки файлов")
        self.upload_status_ok.grid(row=2, column=2, padx=5, pady=2, sticky="nsew")
        logger.debug("Индикатор OK для загрузки файлов размещён")

        # Кнопки управления
        self.button_frame = ttk.Frame(self.left_frame)
        logger.debug("Создан фрейм для кнопок управления")
        self.button_frame.pack(pady=5, fill=tk.X)
        logger.debug("Фрейм для кнопок управления упакован")
        self.start_button = ttk.Button(self.button_frame, text="Старт", command=self.start_automation, state=tk.DISABLED)
        logger.debug("Создано кнопка 'Старт'")
        self.start_button.pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        logger.debug("Кнопка 'Старт' упакована")
        self.confirm_button = ttk.Button(self.button_frame, text="Подтвердить авторизацию", command=self.confirm_authorization, state=tk.DISABLED)
        logger.debug("Создано кнопка 'Подтвердить авторизацию'")
        self.confirm_button.pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        logger.debug("Кнопка 'Подтвердить авторизацию' упакована")

        # Фрейм для настроек таймингов и файлов
        self.settings_frame = ttk.LabelFrame(self.left_frame, text="Настройки", padding="10", style="TLabelframe")
        logger.debug("Создан фрейм для настроек")
        self.settings_frame.pack(pady=5, padx=5, fill=tk.BOTH, expand=True)
        logger.debug("Фрейм для настроек упакован")
        settings_canvas = tk.Canvas(self.settings_frame, bg="#f0f4f8")
        logger.debug("Создан холст для настроек")
        scrollbar = ttk.Scrollbar(self.settings_frame, orient="vertical", command=settings_canvas.yview)
        logger.debug("Создано ползунок для настроек")
        self.settings_inner = ttk.Frame(settings_canvas)
        logger.debug("Создан внутренний фрейм для настроек")
        settings_canvas.configure(yscrollcommand=scrollbar.set)
        logger.debug("Настроен холст с ползунком")

        settings_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        logger.debug("Холст упакован")
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        logger.debug("Ползунок упакован")
        settings_canvas.create_window((0, 0), window=self.settings_inner, anchor="nw")
        logger.debug("Внутренний фрейм добавлен в холст")
        self.settings_inner.bind("<Configure>", lambda e: settings_canvas.configure(scrollregion=settings_canvas.bbox("all")))
        logger.debug("Настроена привязка изменения размера внутреннего фрейма")

        # Тайминги
        self.timing_vars = {}
        logger.debug("Инициализирован словарь для таймингов")
        timings = [
            ("sleep_after_open_tab", "Задержка после открытия вкладки"),
            ("sleep_after_scroll", "Задержка после прокрутки"),
            ("sleep_after_button_click", "Задержка после клика"),
            ("sleep_after_file_select", "Задержка после выбора файла"),
            ("sleep_after_retry", "Задержка при повторе"),
            ("sleep_after_edge_kill", "Задержка после закрытия Edge"),
            ("sleep_progress_simulation", "Задержка симуляции прогресса")
        ]
        for i, (key, label) in enumerate(timings):
            ttk.Label(self.settings_inner, text=label, background="#f0f4f8", width=25, anchor="e").grid(row=i, column=0, padx=5, pady=2, sticky="e")
            logger.debug(f"Создан лейбл для тайминга: {label}")
            value = int(self.timing_settings[key]) if self.timing_settings[key].is_integer() else self.timing_settings[key]
            var = tk.StringVar(value=str(value))
            self.timing_vars[key] = var
            logger.debug(f"Инициализирован тайминг {key} со значением {value}")
            ttk.Entry(self.settings_inner, textvariable=var, width=10).grid(row=i, column=1, padx=5, pady=2, sticky="w")
            logger.debug(f"Создано поле ввода для тайминга {key}")
        self.save_timing_button = ttk.Button(self.settings_inner, text="Сохранить тайминги", command=self.save_timing_settings, style="Custom.TButton")
        logger.debug("Создано кнопка 'Сохранить тайминги'")
        self.save_timing_button.grid(row=len(timings), column=0, columnspan=2, pady=5, sticky="nsew")
        logger.debug("Кнопка 'Сохранить тайминги' размещена")
        self.settings_inner.columnconfigure(0, weight=2)
        self.settings_inner.columnconfigure(1, weight=1)
        logger.debug("Настроены веса колонок фрейма настроек")

        # Горячая клавиша паузы
        ttk.Label(self.settings_inner, text="Горячая клавиша паузы:", background="#f0f4f8", width=25, anchor="e").grid(row=len(timings) + 1, column=0, padx=5, pady=2, sticky="e")
        logger.debug("Создан лейбл для горячей клавиши паузы")
        self.pause_hotkey_var = tk.StringVar(value=self.pause_hotkey)
        logger.debug(f"Инициализирована переменная для горячей клавиши: {self.pause_hotkey}")
        ttk.Entry(self.settings_inner, textvariable=self.pause_hotkey_var, width=10).grid(row=len(timings) + 1, column=1, padx=5, pady=2, sticky="w")
        logger.debug("Создано поле ввода для горячей клавиши")
        self.save_hotkey_button = ttk.Button(self.settings_inner, text="Сохранить клавишу", command=self.save_pause_hotkey, style="Custom.TButton")
        logger.debug("Создано кнопка 'Сохранить клавишу'")
        self.save_hotkey_button.grid(row=len(timings) + 2, column=0, columnspan=2, pady=5, sticky="nsew")
        logger.debug("Кнопка 'Сохранить клавишу' размещена")

        # Редактирование файлов
        self.file_edit_frame = ttk.LabelFrame(self.settings_inner, text="Управление файлами", padding="10")
        logger.debug("Создан фрейм для управления файлами")
        self.file_edit_frame.grid(row=len(timings) + 3, column=0, columnspan=2, pady=5, sticky="nsew")
        logger.debug("Фрейм для управления файлами размещён")
        self.file_list_frame = ttk.Frame(self.file_edit_frame)
        logger.debug("Создан фрейм для списка файлов")
        self.file_list_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        logger.debug("Фрейм для списка файлов упакован")
        self.file_list = tk.Listbox(self.file_list_frame, height=8, bg="#ffffff", fg="#333333", highlightthickness=1, highlightbackground="#1976D2")
        logger.debug("Создан список файлов")
        self.file_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        logger.debug("Список файлов упакован")
        scrollbar_files = ttk.Scrollbar(self.file_list_frame, orient="vertical", command=self.file_list.yview)
        logger.debug("Создано ползунок для списка файлов")
        scrollbar_files.pack(side=tk.RIGHT, fill=tk.Y)
        logger.debug("Ползунок для списка файлов упакован")
        self.file_list.config(yscrollcommand=scrollbar_files.set)
        logger.debug("Настроен ползунок для списка файлов")

        edit_frame = ttk.Frame(self.file_edit_frame)
        logger.debug("Создан фрейм для редактирования файлов")
        edit_frame.pack(fill=tk.X, padx=5, pady=5)
        logger.debug("Фрейм для редактирования файлов упакован")
        ttk.Label(edit_frame, text="URL:").grid(row=0, column=0, padx=5, pady=2, sticky="e")
        logger.debug("Создан лейбл для URL")
        self.new_url_var = tk.StringVar()
        logger.debug("Инициализирована переменная для нового URL")
        self.url_entry = ttk.Entry(edit_frame, textvariable=self.new_url_var)
        logger.debug("Создано поле ввода для URL")
        self.url_entry.grid(row=0, column=1, padx=5, pady=2, sticky="ew")
        logger.debug("Поле ввода для URL размещено")
        ttk.Label(edit_frame, text="Файл:").grid(row=1, column=0, padx=5, pady=2, sticky="e")
        logger.debug("Создан лейбл для файла")
        self.new_file_var = tk.StringVar()
        logger.debug("Инициализирована переменная для нового файла")
        self.file_entry = ttk.Entry(edit_frame, textvariable=self.new_file_var)
        logger.debug("Создано поле ввода для файла")
        self.file_entry.grid(row=1, column=1, padx=5, pady=2, sticky="ew")
        logger.debug("Поле ввода для файла размещено")
        button_frame = ttk.Frame(edit_frame)
        logger.debug("Создан фрейм для кнопок редактирования")
        button_frame.grid(row=2, column=0, columnspan=2, pady=5, sticky="nsew")
        logger.debug("Фрейм для кнопок редактирования размещён")
        ttk.Button(button_frame, text="Добавить", command=self.add_file, style="Custom.TButton").pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        logger.debug("Создано кнопка 'Добавить'")
        ttk.Button(button_frame, text="Редактировать", command=self.edit_file, style="Custom.TButton").pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        logger.debug("Создано кнопка 'Редактировать'")
        ttk.Button(button_frame, text="Удалить", command=self.delete_file, style="Custom.TButton").pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        logger.debug("Создано кнопка 'Удалить'")
        ttk.Button(button_frame, text="Сохранить изменения", command=self.save_files, style="Custom.TButton").pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        logger.debug("Создано кнопка 'Сохранить изменения'")
        edit_frame.columnconfigure(1, weight=1)
        logger.debug("Настроен вес колонки фрейма редактирования")

        # Правая половина (города)
        self.right_frame = ttk.Frame(self.main_pane)
        logger.debug("Создан правый фрейм")
        self.main_pane.add(self.right_frame)
        logger.debug("Правый фрейм добавлен в панельный контейнер")

        # Фрейм для городов с скроллингом
        self.city_frame = ttk.LabelFrame(self.right_frame, text="Выбор городов", padding="10", style="TLabelframe")
        logger.debug("Создан фрейм для выбора городов")
        self.city_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        logger.debug("Фрейм для выбора городов упакован")
        city_canvas = tk.Canvas(self.city_frame, bg="#f0f4f8")
        logger.debug("Создан холст для городов")
        city_scrollbar = ttk.Scrollbar(self.city_frame, orient="vertical", command=city_canvas.yview)
        logger.debug("Создано ползунок для городов")
        self.city_inner = ttk.Frame(city_canvas)
        logger.debug("Создан внутренний фрейм для городов")
        city_canvas.configure(yscrollcommand=city_scrollbar.set)
        logger.debug("Настроен холст с ползунком для городов")

        city_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        logger.debug("Холст для городов упакован")
        city_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        logger.debug("Ползунок для городов упакован")
        city_canvas.create_window((0, 0), window=self.city_inner, anchor="nw")
        logger.debug("Внутренний фрейм добавлен в холст городов")
        self.city_inner.bind("<Configure>", lambda e: city_canvas.configure(scrollregion=city_canvas.bbox("all")))
        logger.debug("Настроена привязка изменения размера внутреннего фрейма городов")
        city_canvas.bind("<MouseWheel>", lambda e: city_canvas.yview_scroll(int(-1*(e.delta/120)), "units"))
        logger.debug("Настроена прокрутка колесом мыши для холста городов")

        self.city_inner.columnconfigure(0, weight=1)
        self.city_inner.columnconfigure(1, weight=1)
        self.city_inner.columnconfigure(2, weight=1)
        self.city_inner.columnconfigure(3, weight=1)
        logger.debug("Настроены веса колонок фрейма городов")

        self.city_vars = {}
        self.city_checkboxes = []
        logger.debug("Инициализированы словари для чекбоксов")
        self.error_label = ttk.Label(self.city_inner, text="", foreground="red")
        logger.debug("Создан лейбл для ошибок")
        self.error_label.grid(row=0, column=0, columnspan=4, pady=5, sticky="nsew")
        logger.debug("Лейбл для ошибок размещён")
        self.btn_frame = ttk.Frame(self.city_inner)
        logger.debug("Создан фрейм для кнопок городов")
        self.btn_frame.grid(row=1, column=0, columnspan=4, pady=5, sticky="nsew")
        logger.debug("Фрейм для кнопок городов размещён")
        ttk.Button(self.btn_frame, text="Снять все", command=self.unselect_all, style="Custom.TButton").pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        logger.debug("Создано кнопка 'Снять все'")
        ttk.Button(self.btn_frame, text="Выделить все", command=self.select_all, style="Custom.TButton").pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        logger.debug("Создано кнопка 'Выделить все'")
        self.load_cities()
        logger.debug("Загружены города")
        self.load_file_list()  # Вызов после создания всех виджетов
        logger.debug("Загружен список файлов")

        self.is_authorized = tk.BooleanVar(value=False)
        logger.debug("Инициализирована переменная авторизации")
        self.driver = None
        self.mapping = None
        self.windows = {}
        self.total_files = 0
        self.current_file = 0
        self.paused = False
        self.current_stage = None
        self.current_index = 0
        logger.debug("Инициализированы основные переменные приложения")

        # Настройка горячей клавиши паузы
        self.setup_hotkey()
        logger.debug("Настроена горячая клавиша паузы")

        # Адаптивность окна
        self.root.bind("<Configure>", self.on_resize)
        logger.debug("Настроена адаптивность окна")

    def on_resize(self, event):
        logger.debug(f"Изменение размера окна: ширина={event.width}, высота={event.height}")
        # Адаптация размеров при изменении окна
        self.status_frame.columnconfigure(0, weight=1)
        self.status_frame.columnconfigure(1, weight=3)
        self.status_frame.columnconfigure(2, weight=1)
        logger.debug("Обновлены веса колонок фрейма статусов")
        self.settings_inner.columnconfigure(0, weight=2)
        self.settings_inner.columnconfigure(1, weight=1)
        logger.debug("Обновлены веса колонок фрейма настроек")
        self.city_inner.columnconfigure(0, weight=1)
        self.city_inner.columnconfigure(1, weight=1)
        self.city_inner.columnconfigure(2, weight=1)
        self.city_inner.columnconfigure(3, weight=1)
        logger.debug("Обновлены веса колонок фрейма городов")

    def check_driver(self):
        default_path = r"C:\Drivers\msedgedriver.exe"
        logger.info(f"Проверка наличия драйвера в {default_path}")
        if os.path.exists(default_path):
            logger.info(f"Драйвер найден: {default_path}")
            return default_path
        custom_path = messagebox.askquestion("Драйвер", "Драйвер не найден в стандартной директории. Укажите кастомный путь?")
        logger.debug(f"Ответ пользователя на запрос пути драйвера: {custom_path}")
        if custom_path == "yes":
            from tkinter import filedialog
            path = filedialog.askopenfilename(title="Выберите msedgedriver.exe", filetypes=[("Executable files", "*.exe")])
            logger.debug(f"Выбранный путь драйвера: {path}")
            if os.path.exists(path) and os.path.basename(path).lower() == "msedgedriver.exe":
                logger.info(f"Драйвер найден по кастомному пути: {path}")
                return path
        logger.warning("Драйвер не найден")
        return None

    def check_driver_status(self):
        logger.debug("Проверка статуса драйвера")
        driver_path = self.check_driver()
        if driver_path:
            self.driver_path = driver_path
            self.driver_status_value.config(text="ОК", foreground="green")
            self.driver_checked = True
            self.start_button.config(state=tk.NORMAL)  # Разблокируем кнопку "Старт"
            logger.info(f"Драйвер проверен: найден по пути {driver_path}")
        else:
            self.driver_status_value.config(text="Отсутствует", foreground="red")
            self.driver_checked = False
            self.start_button.config(state=tk.DISABLED)  # Блокируем кнопку "Старт"
            logger.warning("Драйвер не найден")

    def install_driver(self):
        logger.debug("Начало установки драйвера")
        try:
            # Use latest driver; change URL if needed, or use webdriver-manager for auto
            from webdriver_manager.microsoft import EdgeChromiumDriverManager
            driver_path = EdgeChromiumDriverManager().install()
            self.driver_path = driver_path
            logger.info(f"Драйвер установлен автоматически: {driver_path}")
            self.driver_status_value.config(text="ОК", foreground="green")
            self.driver_checked = True
            self.start_button.config(state=tk.NORMAL)
            messagebox.showinfo("Успех", "Драйвер успешно установлен.")
        except Exception as e:
            logger.error(f"Неизвестная ошибка при установке драйвера: {str(e)}")
            messagebox.showerror("Ошибка", f"Неизвестная ошибка: {str(e)}. Попробуйте ручную установку.")

    def load_timing_settings_from_sftp(self):
        logger.debug("Начало загрузки настроек с SFTP")
        default_settings = {
            "sleep_after_open_tab": 2.0,
            "sleep_after_scroll": 2.0,
            "sleep_after_button_click": 2.0,
            "sleep_after_file_select": 2.0,
            "sleep_after_retry": 2.0,
            "sleep_after_edge_kill": 5.0,
            "sleep_progress_simulation": 0.1
        }
        default_hotkey = "F10"
        default_files = []
        try:
            logger.info("Пытаемся подключиться к SFTP для загрузки yabussines.json")
            transport = paramiko.Transport((sftp_config["sftp_host"], sftp_config["sftp_port"]))
            logger.debug(f"Подключение к SFTP: хост={sftp_config['sftp_host']}, порт={sftp_config['sftp_port']}")
            transport.connect(username=sftp_config["sftp_user"], password=sftp_config["sftp_pass"])
            logger.debug("Успешное подключение к SFTP")
            sftp = paramiko.SFTPClient.from_transport(transport)
            logger.debug("Создан клиент SFTP")
            remote_mapping = os.path.join(sftp_config["sftp_mapping_path"], "yabussines.json")
            logger.debug(f"Путь к удалённому файлу: {remote_mapping}")
            sftp.get(remote_mapping, "yabussines.json")
            logger.debug("Файл yabussines.json загружен локально")
            with open("yabussines.json", 'r', encoding='utf-8') as f:
                content = f.read().strip()
                logger.debug(f"Содержимое файла: {content[:100]}...")  # Логируем первые 100 символов
                if not content:
                    logger.warning("yabussines.json пустой после загрузки с SFTP")
                    return default_settings, default_hotkey, default_files
                data = json.loads(content)
                logger.debug(f"Разобранный JSON: {data}")
                if isinstance(data, list):
                    data = {"files": data}
                    logger.debug("JSON преобразован в словарь с ключом 'files'")
                elif not isinstance(data, dict):
                    logger.error(f"yabussines.json содержит некорректный формат: {type(data)}")
                    return default_settings, default_hotkey, default_files
                settings = data.get("timing_settings", {})
                hotkey = data.get("pause_hotkey", default_hotkey)
                files = data.get("files", default_files)
                logger.info("Успешно загружены настройки и файлы из yabussines.json")
                return {k: float(v) for k, v in {**default_settings, **settings}.items()}, hotkey, files
            sftp.close()
            transport.close()
        except Exception as e:
            logger.error(f"Не удалось загрузить yabussines.json с SFTP: {str(e)}")
            return default_settings, default_hotkey, default_files

    def save_timing_settings(self):
        logger.debug("Начало сохранения настроек")
        try:
            logger.info("Подготовка к сохранению данных в yabussines.json")
            data = {"files": self.files, "timing_settings": self.timing_settings, "pause_hotkey": self.pause_hotkey}
            logger.debug(f"Данные для сохранения: {data}")
            with open("yabussines.json", 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=4)
            logger.info("Данные сохранены локально в yabussines.json")
            logger.info("Пытаемся подключиться к SFTP для выгрузки yabussines.json")
            transport = paramiko.Transport((sftp_config["sftp_host"], sftp_config["sftp_port"]))
            logger.debug(f"Подключение к SFTP: хост={sftp_config['sftp_host']}, порт={sftp_config['sftp_port']}")
            transport.connect(username=sftp_config["sftp_user"], password=sftp_config["sftp_pass"])
            logger.debug("Успешное подключение к SFTP")
            sftp = paramiko.SFTPClient.from_transport(transport)
            logger.debug("Создан клиент SFTP для выгрузки")
            remote_path = os.path.join(sftp_config["sftp_mapping_path"], "yabussines.json")
            logger.debug(f"Путь для выгрузки: {remote_path}")
            sftp.put("yabussines.json", remote_path)
            logger.info("Файл yabussines.json успешно выгружен на SFTP")
            sftp.close()
            transport.close()
            messagebox.showinfo("Успех", "Настройки и файлы успешно сохранены и загружены на SFTP.")
        except Exception as e:
            logger.error(f"Не удалось сохранить yabussines.json на SFTP: {str(e)}")
            messagebox.showerror("Ошибка", f"Не удалось сохранить настройки на SFTP: {str(e)}")

    def save_pause_hotkey(self):
        logger.debug("Начало сохранения горячей клавиши")
        new_hotkey = self.pause_hotkey_var.get().strip()
        logger.debug(f"Новая горячая клавиша: {new_hotkey}")
        if new_hotkey:
            self.pause_hotkey = new_hotkey
            self.save_timing_settings()  # Сохраняем все настройки, включая новую клавишу
        else:
            messagebox.showerror("Ошибка", "Введите корректную горячую клавишу.")

    def setup_hotkey(self):
        logger.debug("Настройка горячей клавиши паузы")
        if hasattr(self, 'hotkey_listener'):
            self.hotkey_listener.stop()
            logger.debug("Остановлен предыдущий слушатель горячей клавиши")
        self.hotkey_listener = keyboard.on_press_key(self.pause_hotkey, lambda _: self.toggle_pause())
        logger.debug(f"Настроен новый слушатель для клавиши {self.pause_hotkey}")

    def toggle_pause(self):
        logger.debug("Переключение состояния паузы")
        if not self.paused and self.current_stage:
            self.paused = True
            logger.info(f"Приложение приостановлено (нажмите {self.pause_hotkey} для возобновления)")
            messagebox.showinfo("Пауза", f"Приложение приостановлено. Нажмите {self.pause_hotkey} для возобновления.")
        elif self.paused:
            self.paused = False
            logger.info(f"Приложение возобновлено с индекса {self.current_index}")
            messagebox.showinfo("Возобновление", "Приложение возобновлено.")
            if self.current_stage == "upload" and self.driver:
                self.resume_upload()

    def check_internet_connection(self):
        logger.debug("Проверка интернет-соединения")
        try:
            logger.info("Проверка интернет-соединения через Google")
            response = requests.get("https://www.google.com", timeout=5)
            if response.status_code == 200:
                logger.info(f"Интернет-соединение подтверждено через Google (статус: {response.status_code})")
                return True
            logger.info("Проверка интернет-соединения через DNS (8.8.8.8)")
            socket.create_connection(("8.8.8.8", 53), timeout=5)
            logger.info("Интернет-соединение подтверждено через DNS")
            return True
        except (requests.ConnectionError, socket.error) as e:
            logger.error(f"Ошибка соединения: {str(e)}")
            return False

    def download_files_from_sftp(self, local_folder):
        logger.debug(f"Начало загрузки файлов с SFTP в {local_folder}")
        if not os.path.exists(local_folder):
            try:
                os.makedirs(local_folder, exist_ok=True)
                logger.info(f"Создан каталог: {local_folder}")
            except PermissionError as e:
                logger.error(f"Нет прав для создания {local_folder}: {str(e)}. Запустите скрипт от имени администратора.")
                messagebox.showerror("Ошибка", f"Нет прав для создания {local_folder}. Запустите от имени администратора.")
                return []

        try:
            logger.info("Пытаемся подключиться к SFTP для загрузки файлов")
            transport = paramiko.Transport((sftp_config["sftp_host"], sftp_config["sftp_port"]))
            logger.debug(f"Подключение к SFTP: хост={sftp_config['sftp_host']}, порт={sftp_config['sftp_port']}")
            transport.connect(username=sftp_config["sftp_user"], password=sftp_config["sftp_pass"])
            logger.debug("Успешное подключение к SFTP")
            sftp = paramiko.SFTPClient.from_transport(transport)
            logger.debug("Создан клиент SFTP")
        except Exception as e:
            logger.error(f"Не удалось подключиться к SFTP: {str(e)}")
            messagebox.showerror("Ошибка", f"Не удалось подключиться к SFTP: {str(e)}")
            return []

        local_files = []
        self.total_files = len(self.files)
        logger.debug(f"Общее количество файлов для обработки: {self.total_files}")
        self.current_file = 0
        for i, item in enumerate(self.files):
            self.current_file = i + 1
            local_file = os.path.join(local_folder, item["file"])
            logger.debug(f"Обработка файла {self.current_file}: локальный путь {local_file}")
            if os.path.exists(local_file):
                try:
                    os.remove(local_file)
                    logger.info(f"Удалён старый файл: {local_file}")
                except Exception as e:
                    logger.warning(f"Не удалось удалить старый файл {local_file}: {str(e)}")
            self.update_progress("download", self.current_file, self.total_files)
            self.root.update_idletasks()  # Обновляем интерфейс
            time.sleep(self.timing_settings["sleep_progress_simulation"] / 2)  # Уменьшаем задержку

        selected_files = [item for item in self.files if self.city_vars.get(os.path.splitext(item["file"])[0], tk.BooleanVar(value=False)).get()]
        logger.debug(f"Выбрано файлов для загрузки: {len(selected_files)}")
        self.total_files = len(selected_files)
        self.current_file = 0
        for i, item in enumerate(selected_files):
            self.current_file = i + 1
            logger.debug(f"Обработка файла {self.current_file} из {self.total_files}: {item['file']}")
            if not item["file"].lower().endswith('.xls'):
                logger.warning(f"Пропущен файл {item['file']}: ожидается расширение .xls")
                messagebox.showwarning("Предупреждение", f"Файл {item['file']} пропущен из-за некорректного расширения.")
                continue
            remote_file = os.path.join(sftp_config["sftp_remote_path"], item["file"])
            logger.debug(f"Удалённый путь файла: {remote_file}")
            local_file = os.path.join(local_folder, item["file"])
            try:
                logger.info(f"Проверяем наличие файла {remote_file} на SFTP")
                sftp.stat(remote_file)
                logger.debug(f"Файл {remote_file} найден на сервере")
                for attempt in range(3):
                    try:
                        logger.info(f"Пытаемся скачать {remote_file} (попытка {attempt + 1}/3)")
                        sftp.get(remote_file, local_file)
                        logger.info(f"Успешно скачан файл: {remote_file} -> {local_file}")
                        local_files.append({"url": item["url"], "file": local_file})
                        self.update_progress("download", self.current_file, self.total_files)
                        self.root.update_idletasks()  # Обновляем интерфейс
                        break
                    except Exception as e:
                        logger.error(f"Не удалось скачать {remote_file} (попытка {attempt + 1}/3): {str(e)}")
                        if attempt < 2:
                            time.sleep(self.timing_settings["sleep_after_retry"])
                            continue
                        raise
            except IOError as e:
                logger.error(f"Файл {remote_file} не найден на сервере: {str(e)}")
                continue
            time.sleep(self.timing_settings["sleep_progress_simulation"] / 2)  # Уменьшаем задержку

        self.set_status_ok("download")
        logger.info("Завершена загрузка файлов с SFTP")
        sftp.close()
        transport.close()
        logger.debug(f"Загружено файлов: {len(local_files)}")
        return local_files

    def start_automation(self):
        logger.debug("Начало автоматизации")
        if not self.driver_checked:
            messagebox.showwarning("Предупреждение", "Пожалуйста, проверьте драйвер перед запуском автоматизации.")
            logger.warning("Попытка запуска без проверки драйвера")
            return
        logger.info("Запуск автоматизации начат")
        self.file_status.config(text="Загрузка файлов: В процессе")
        self.auth_status.config(text="Авторизация: Ожидание")
        self.upload_status.config(text="Статус загрузки файлов: Ожидание")
        self.update_progress("download", 0, 1)

        if not self.check_internet_connection():
            logger.error("Нет интернет-соединения. Автоматизация прервана.")
            messagebox.showerror("Ошибка", "Нет интернет-соединения. Проверьте подключение и попробуйте снова.")
            return

        if not self.driver_path:
            logger.error("Драйвер Edge не найден. Автоматизация прервана.")
            messagebox.showerror("Ошибка", "Драйвер Edge не найден. Пожалуйста, установите его и нажмите 'Проверить'.")
            return

        local_folder = r"C:\Temp"
        logger.debug(f"Используем локальную папку: {local_folder}")
        try:
            logger.info("Пытаемся загрузить yabussines.json с SFTP для автоматизации")
            transport = paramiko.Transport((sftp_config["sftp_host"], sftp_config["sftp_port"]))
            logger.debug(f"Подключение к SFTP: хост={sftp_config['sftp_host']}, порт={sftp_config['sftp_port']}")
            transport.connect(username=sftp_config["sftp_user"], password=sftp_config["sftp_pass"])
            logger.debug("Успешное подключение к SFTP")
            sftp = paramiko.SFTPClient.from_transport(transport)
            logger.debug("Создан клиент SFTP")
            remote_mapping = os.path.join(sftp_config["sftp_mapping_path"], "yabussines.json")
            logger.debug(f"Путь к удалённому файлу: {remote_mapping}")
            sftp.get(remote_mapping, "yabussines.json")
            logger.debug("Файл yabussines.json загружен локально")
            with open("yabussines.json", 'r', encoding='utf-8') as f:
                content = f.read().strip()
                logger.debug(f"Содержимое файла: {content[:100]}...")  # Логируем первые 100 символов
                if not content:
                    logger.warning("yabussines.json пустой после загрузки с SFTP")
                    self.files = []
                else:
                    self.files = json.loads(content).get("files", [])
            logger.info("Успешно загружен yabussines.json с SFTP для автоматизации")
            sftp.close()
            transport.close()
        except Exception as e:
            logger.error(f"Не удалось загрузить yabussines.json с SFTP: {str(e)}")
            messagebox.showerror("Ошибка", f"Не удалось загрузить yabussines.json с SFTP: {str(e)}")
            return

        self.mapping = self.download_files_from_sftp(local_folder)
        logger.debug(f"Сопоставление файлов после загрузки: {len(self.mapping)}")
        self.load_file_list()  # Обновляем список файлов после загрузки
        if not self.mapping:
            logger.warning("Нет файлов для обработки!")
            messagebox.showerror("Ошибка", "Нет файлов для обработки или ошибка парсинга JSON!")
            self.set_status_ok("download")
            return

        urls = [item["url"] for item in self.mapping]
        files = [item["file"] for item in self.mapping]
        logger.info(f"Найдено файлов для обработки: {len(files)}")

        try:
            # Kill all existing Edge processes to avoid conflicts
            logger.info("Закрытие всех процессов Edge перед запуском")
            subprocess.call(['taskkill', '/f', '/im', 'msedge.exe', '/t'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            time.sleep(2)  # Wait for processes to terminate

            options = EdgeOptions()
            options.use_chromium = True
            # Removed user data dir to avoid crashes
            # user_data_dir = r"C:\Users\root\AppData\Local\Microsoft\Edge\User Data"
            # if not os.path.exists(user_data_dir):
            #     logger.error(f"Папка профиля не найдена: {user_data_dir}")
            #     messagebox.showerror("Ошибка", f"Папка профиля не найдена: {user_data_dir}")
            #     return
            # options.add_argument(f"--user-data-dir={user_data_dir}")
            # options.add_argument("--profile-directory=Default")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--remote-debugging-port=9222")
            options.add_argument("--disable-gpu")
            options.add_argument("--ignore-certificate-errors")
            options.add_argument("--disable-extensions")

            service = Service(self.driver_path)
            self.driver = webdriver.Edge(service=service, options=options)
            logger.info(f"Браузер успешно запущен с драйвером: {self.driver_path}")
            self.driver.maximize_window()

            self.driver.get("https://yandex.ru/business/priority")
            logger.info("Открыта страница для авторизации: https://yandex.ru/business/priority")
            self.auth_status.config(text="Авторизация: В процессе")
            self.update_progress("auth", 50, 100)
            messagebox.showinfo("Инструкция", "Пожалуйста, авторизуйтесь в Яндекс. После авторизации нажмите 'Подтвердить авторизацию'.")
            self.start_button.config(state=tk.DISABLED)
            self.confirm_button.config(state=tk.NORMAL)
            self.root.wait_variable(self.is_authorized)

            if self.is_authorized.get():
                self.set_status_ok("auth")
                logger.info("Начинаем автоматизацию после подтверждения авторизации")
                self.upload_status.config(text="Статус загрузки файлов: В процессе")
                self.update_progress("upload", 0, len(urls))
                total_urls = len(urls)
                self.current_stage = "upload"
                self.current_index = 0
                while self.current_index < total_urls and not self.paused and self.driver:
                    url = urls[self.current_index]
                    file = files[self.current_index]
                    self.update_progress("upload", self.current_index + 1, total_urls)
                    self.open_sequentially(url, file)
                    self.current_index += 1
                if self.paused:
                    logger.info(f"Автоматизация приостановлена на файле {self.current_index} из {total_urls}")
                else:
                    logger.info("Автоматизация завершена, вкладки оставлены открытыми")
                    self.set_status_ok("upload")
            else:
                logger.warning("Авторизация не подтверждена, вкладки оставлены открытыми")

        except Exception as e:
            logger.error(f"Произошла ошибка во время автоматизации: {str(e)}")
            messagebox.showerror("Ошибка", f"Произошла ошибка: {str(e)}")
        finally:
            if self.driver:
                logger.info("Автоматизация завершена, вкладки оставлены открытыми")
            self.save_timing_settings()

    def confirm_authorization(self):
        logger.debug("Подтверждение авторизации")
        self.is_authorized.set(True)
        self.confirm_button.config(state=tk.DISABLED)
        self.start_button.config(state=tk.NORMAL)
        self.update_progress("auth", 100, 100)
        self.set_status_ok("auth")
        logger.info("Авторизация подтверждена пользователем")

    def resume_upload(self):
        logger.debug("Возобновление загрузки")
        if self.current_stage == "upload" and self.current_index < len(self.mapping) and self.driver:
            urls = [item["url"] for item in self.mapping]
            files = [item["file"] for item in self.mapping]
            total_urls = len(urls)
            logger.info(f"Возобновление загрузки с файла {self.current_index} из {total_urls}")
            if self.current_index > 0 and self.windows:
                last_url = urls[self.current_index - 1]
                if last_url in self.windows:
                    self.driver.switch_to.window(self.windows[last_url])
                    logger.info(f"Возобновление с последней вкладки: {last_url}")
            while self.current_index < total_urls and not self.paused and self.driver:
                url = urls[self.current_index]
                file = files[self.current_index]
                self.update_progress("upload", self.current_index + 1, total_urls)
                self.open_sequentially(url, file)
                self.current_index += 1
            if self.paused:
                logger.info(f"Автоматизация приостановлена на файле {self.current_index} из {total_urls}")
            else:
                logger.info("Автоматизация завершена, вкладки оставлены открытыми")
                self.set_status_ok("upload")

    def open_sequentially(self, url, file):
        logger.debug(f"Открытие вкладки для URL: {url}, файл: {file}")
        if not self.driver:
            logger.error(f"Драйвер не инициализирован для обработки URL: {url}")
            return
        try:
            logger.info(f"Открытие новой вкладки для URL: {url}")
            self.driver.execute_script("window.open('');")
            time.sleep(self.timing_settings["sleep_after_open_tab"])
            all_windows = self.driver.window_handles
            logger.debug(f"Найдено окон браузера: {len(all_windows)}")
            if len(all_windows) > 0:
                new_window = all_windows[-1]
                self.windows[url] = new_window
                self.driver.switch_to.window(new_window)
                logger.info(f"Переключение на новую вкладку: {url}")
            else:
                raise NoSuchWindowException("Не удалось открыть новую вкладку")
            self.driver.get(url)
            logger.info(f"Загрузка страницы: {url}")
            self.driver.execute_script("window.scrollTo(0, 0);")
            time.sleep(self.timing_settings["sleep_after_scroll"])
            logger.info(f"Страница прокручена вверх для {url}")
            WebDriverWait(self.driver, 30).until(EC.presence_of_element_located((By.TAG_NAME, "body")))

            upload_xls_yml_button = WebDriverWait(self.driver, 30).until(
                EC.element_to_be_clickable((By.XPATH, "//span[contains(text(), 'Загрузить XLS/YML')]"))
            )
            actions = ActionChains(self.driver)
            actions.move_to_element(upload_xls_yml_button).pause(1).click().perform()
            logger.info(f"Нажата кнопка 'Загрузить XLS/YML' для {url}")
            time.sleep(self.timing_settings["sleep_after_button_click"])

            upload_price_list_button = WebDriverWait(self.driver, 30).until(
                EC.element_to_be_clickable((By.XPATH, "//button[contains(@class, 'UploadPriceListForm-Button_type_submit') and contains(span/text(), 'Загрузить прайс-лист')]"))
            )
            actions.move_to_element(upload_price_list_button).pause(1).click().perform()
            logger.info(f"Нажата кнопка 'Загрузить прайс-лист' для {url}")
            time.sleep(self.timing_settings["sleep_after_button_click"])

            if os.path.exists(file):
                logger.info(f"Файл найден: {file}")
                time.sleep(self.timing_settings["sleep_after_file_select"])
                full_path = os.path.normpath(file)
                logger.info(f"Полный путь для ввода: {full_path}")

                try:
                    app = pywinauto.Application().connect(title_re="Открытие", timeout=10)
                    dlg = app.top_window()
                    dlg.wait('visible', timeout=10)
                    logger.info(f"Диалог 'Открытие' обнаружен")
                    pywinauto.keyboard.send_keys(full_path)
                    time.sleep(0.5)
                    pywinauto.keyboard.send_keys('{ENTER}')
                    logger.info(f"Выбран файл: {os.path.basename(file)} для {url}")
                except pywinauto.findwindows.ElementNotFoundError as e:
                    logger.warning(f"Диалог 'Открытие' не найден для {url}: {str(e)}")
                    pyautogui.typewrite(full_path, interval=0.2)
                    time.sleep(0.5)
                    pyautogui.press('enter')
                    logger.info(f"Резервный ввод пути: {full_path} для {url}")
                time.sleep(self.timing_settings["sleep_after_file_select"])
            else:
                logger.error(f"Файл {file} не найден")
        except (TimeoutException, NoSuchElementException, NoSuchWindowException) as e:
            logger.error(f"Ошибка при обработке вкладки {url}: {str(e)}")
        except Exception as e:
            logger.error(f"Неожиданная ошибка при обработке вкладки {url}: {str(e)}")

    def load_file_list(self):
        logger.debug("Загрузка списка файлов")
        if not hasattr(self, 'file_list'):  # Проверка существования атрибута
            logger.warning("Атрибут file_list не существует")
            return
        self.file_list.delete(0, tk.END)
        logger.debug("Очистка списка файлов")
        if self.files:
            for i, file_entry in enumerate(self.files):
                self.file_list.insert(tk.END, f"{file_entry['file']} - {file_entry['url']}")
                logger.debug(f"Добавлен файл в список: {file_entry['file']} - {file_entry['url']}")
        else:
            self.file_list.insert(tk.END, "Нет доступных файлов")
            logger.debug("Добавлено сообщение: Нет доступных файлов")
        logger.info(f"Список файлов загружен: {len(self.files)} записей")

    def add_file(self):
        logger.debug("Добавление нового файла")
        url = self.new_url_var.get().strip()
        file_name = self.new_file_var.get().strip()
        logger.debug(f"Получены данные: URL={url}, Файл={file_name}")
        if url and file_name and file_name.lower().endswith('.xls'):
            self.files.append({"url": url, "file": file_name})
            logger.info(f"Добавлен новый файл: {file_name} - {url}")
            self.load_file_list()
            self.new_url_var.set("")
            self.new_file_var.set("")
            logger.debug("Поля ввода очищены")
            self.load_cities()  # Обновляем список городов
            self.save_timing_settings()  # Сохраняем и выгружаем на SFTP
        else:
            messagebox.showerror("Ошибка", "Введите корректный URL и имя файла с расширением .xls")

    def edit_file(self):
        logger.debug("Редактирование файла")
        selected_index = self.file_list.curselection()
        logger.debug(f"Выбранный индекс: {selected_index}")
        if selected_index:
            index = selected_index[0]
            if 0 <= index < len(self.files):
                file_entry = self.files[index]
                self.new_url_var.set(file_entry["url"])
                self.new_file_var.set(file_entry["file"])
                self.file_list.delete(index)
                self.files.pop(index)
                logger.info(f"Выбран файл для редактирования: {file_entry['file']} - {file_entry['url']}")

    def delete_file(self):
        logger.debug("Удаление файла")
        selected_index = self.file_list.curselection()
        logger.debug(f"Выбранный индекс: {selected_index}")
        if selected_index:
            index = selected_index[0]
            del self.files[index]
            self.load_file_list()
            logger.info(f"Удалён файл с индексом {index}")
            self.load_cities()  # Обновляем список городов
            self.save_timing_settings()  # Сохраняем и выгружаем на SFTP

    def save_files(self):
        logger.debug("Сохранение изменений файлов")
        url = self.new_url_var.get().strip()
        file_name = self.new_file_var.get().strip()
        logger.debug(f"Получены данные: URL={url}, Файл={file_name}")
        if url and file_name and file_name.lower().endswith('.xls'):
            self.files.append({"url": url, "file": file_name})
            self.load_file_list()
            self.new_url_var.set("")
            self.new_file_var.set("")
            logger.info(f"Обновлён файл: {file_name} - {url}")
            self.load_cities()  # Обновляем список городов
            self.save_timing_settings()  # Сохраняем и выгружаем на SFTP
        else:
            messagebox.showerror("Ошибка", "Введите корректный URL и имя файла с расширением .xls")

    def load_cities(self):
        logger.debug("Загрузка списка городов")
        self.city_vars.clear()
        self.city_checkboxes.clear()
        logger.debug("Очищены словари и списки чекбоксов")
        for widget in self.city_inner.winfo_children():
            if widget != self.btn_frame and widget != self.error_label:
                widget.destroy()
                logger.debug(f"Удалён виджет: {widget}")
        self.error_label.config(text="")
        logger.debug("Очищен лейбл ошибок")
        num_columns = 4  # Увеличено до 4 колонок для лучшего распределения
        for i, item in enumerate(self.files):
            full_city = os.path.splitext(item["file"])[0]
            logger.debug(f"Создание чекбокса для файла {item['file']}: полный ключ = {full_city}")
            display_city = full_city
            if len(display_city) > 15:
                display_city = display_city[:12] + "..."
                logger.debug(f"Обрезано имя для отображения: {display_city}")
            var = tk.BooleanVar(value=True)
            self.city_vars[full_city] = var  # Ключ полный, без обрезки
            logger.debug(f"Инициализирована переменная для чекбокса: {full_city}")
            column = i % num_columns
            row = i // num_columns + 2
            cb = ttk.Checkbutton(self.city_inner, text=display_city, variable=var, style="Custom.TCheckbutton")
            logger.debug(f"Создан чекбокс для {full_city} (отображение: {display_city})")
            cb.grid(row=row, column=column, padx=2, pady=2, sticky="nsew")
            logger.debug(f"Чекбокс размещён: ряд={row}, колонка={column}")
            self.city_checkboxes.append(cb)
            logger.debug(f"Чекбокс добавлен в список: {display_city}")
        logger.info(f"Чекбоксы для городов успешно созданы: {len(self.city_checkboxes)} записей")
        self.select_all()  # Автоматически выделяем все города
        logger.debug("Все чекбоксы отмечены")

    def update_progress(self, stage, current, total, percent=None):
        logger.debug(f"Обновление прогресса: стадия={stage}, текущий={current}, общий={total}, процент={percent}")
        if percent is None:
            percent = (current / total * 100) if total > 0 else 0
        percent = min(100, max(0, percent))  # Ограничиваем от 0 до 100
        if stage == "download":
            self.file_progress["value"] = percent
            self.file_status.config(text=f"Загрузка файлов: Файл {current} из {total}, осталось {max(0, total - current)} ({percent:.0f}%)")
            self.root.update_idletasks()
            logger.debug(f"Прогресс загрузки обновлён: {percent:.0f}%")
        elif stage == "auth":
            self.auth_progress["value"] = percent
            self.auth_status.config(text=f"Авторизация: {percent:.0f}%")
            self.root.update_idletasks()
            logger.debug(f"Прогресс авторизации обновлён: {percent:.0f}%")
        elif stage == "upload":
            self.upload_progress["value"] = percent
            self.upload_status.config(text=f"Статус загрузки файлов: Файл {current} из {total}, осталось {max(0, total - current)} ({percent:.0f}%)")
            self.root.update_idletasks()
            logger.debug(f"Прогресс загрузки обновлён: {percent:.0f}%")

    def set_status_ok(self, stage):
        logger.debug(f"Установка статуса OK для стадии: {stage}")
        if stage == "download":
            self.file_status_ok.config(text="ОК ✓")
            self.file_status.config(foreground="green")
        elif stage == "auth":
            self.auth_status_ok.config(text="ОК ✓")
            self.auth_status.config(text="Авторизация: Завершено", foreground="green")
        elif stage == "upload":
            self.upload_status_ok.config(text="ОК ✓")
            self.upload_status.config(foreground="green")
        self.root.update_idletasks()
        logger.info(f"Статус {stage} установлен как OK")

    def select_all(self):
        logger.debug("Выделение всех чекбоксов")
        for city in self.city_vars:
            self.city_vars[city].set(True)
        logger.info("Все чекбоксы отмечены (select_all вызван)")

    def unselect_all(self):
        logger.debug("Снятие выделения со всех чекбоксов")
        for city in self.city_vars:
            self.city_vars[city].set(False)

if __name__ == "__main__":
    logger.debug("Запуск основного цикла приложения")
    root = tk.Tk()
    logger.debug("Создан корневой виджет Tk")
    app = AutomationApp(root)
    logger.debug("Инициализировано приложение")
    style = ttk.Style()
    style.configure("TButton", padding=6, font=("Helvetica", 10), background="#4CAF50", foreground="#333")
    logger.debug("Настроен стиль TButton")
    style.configure("Custom.TButton", padding=6, font=("Helvetica", 10), background="#1976D2", foreground="#333")
    logger.debug("Настроен стиль Custom.TButton")
    style.configure("Custom.TCheckbutton", background="#f0f4f8", foreground="#333")
    logger.debug("Настроен стиль Custom.TCheckbutton")
    style.configure("TLabelframe", background="#f0f4f8", foreground="#333")
    logger.debug("Настроен стиль TLabelframe")
    root.mainloop()
    logger.debug("Основной цикл приложения завершен")
