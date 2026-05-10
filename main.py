# импорты библиотек
import asyncio
import json
import logging
import os
import re
import shutil
import zipfile
import requests
import random
from datetime import datetime, date
from pathlib import Path
from typing import Optional

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import (
    FSInputFile, ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from playwright.async_api import async_playwright, Page, Browser
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

# ---------- настройки бота ----------
BOT_TOKEN = os.getenv("BOT_TOKEN", "8686276466:AAEYgo-bdiu5nmbdtE6Gcyjo0JVK9AbL7BY")
TARGET_URL = "https://web.max.ru"
QR_SELECTOR = "div.qr, .qr-container, div[data-testid='qr-code']" 
LOGIN_TIMEOUT = 60_000
QR_LOAD_TIMEOUT = 15_000

# === НАСТРОЙКИ ПОДКЛЮЧЕНИЯ К ПРОКСИ ===
PROXY_SERVER = "http://185.234.59.13:11163"
PROXY_USERNAME = "ec9VEM"
PROXY_PASSWORD = "er6ruQ7uD2yd"

# === НАСТРОЙКИ УПРАВЛЕНИЯ API ПРОКСИ ===
MP_API_KEY = "8014d1af370af202bf8c5681f891a594"
MP_PROXY_ID = 493322
MP_PROXY_KEY = "82fd4b143c6a0b2901dd9b9d7f6f9498"

# ---------- ПРЯМЫЕ ФУНКЦИИ API (БЕЗ СТОРОННИХ ФАЙЛОВ) ----------
def sync_change_ip_link() -> bool:
    """Смена IP-адреса по прямой ссылке"""
    try:
        url = f"https://changeip.mobileproxy.space/?proxy_key={MP_PROXY_KEY}&format=json"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        response = requests.get(url, headers=headers, timeout=15)
        data = response.json()
        
        if str(data.get('status', '')).upper() == 'OK':
            logging.info(f"✅ Смена IP успешна. Новый IP: {data.get('new_ip', 'неизвестно')}")
            return True
        else:
            logging.error(f"⚠️ Ошибка смены IP: {data}")
            return False
    except Exception as e:
        logging.error(f"❌ Критический сбой при запросе смены IP: {e}")
        return False

def sync_change_equipment() -> tuple[bool, str]:
    """Смена оборудования (Локации) через API"""
    try:
        # Для примера берем Москву (ты можешь добавить нужные ID гео из документации)
        # В API mobileproxy: id_country=1 (РФ), id_city=1 (Москва)
        url = f"https://mobileproxy.space/api.html?command=change_equipment&api_key={MP_API_KEY}&proxy_id={MP_PROXY_ID}&id_country=1&id_city=1&add_to_black_list=0"
        
        response = requests.get(url, timeout=15)
        data = response.json()
        
        status = str(data.get("status", "")).lower()
        if status == "ok":
            return True, "Смена оборудования (Локации) успешно выполнена!"
            
        elif status == "err" or "error" in data:
            error_msg = data.get("error", "Неизвестная ошибка")
            if isinstance(error_msg, dict):
                error_msg = list(error_msg.values())[0]
                
            if "меньше 10 минут" in str(error_msg):
                return False, "Ожидание: менять оборудование можно только раз в 10 минут."
            return False, f"Ошибка API: {error_msg}"
            
        return False, f"Неизвестный ответ: {data}"
    except Exception as e:
        return False, f"Критическая ошибка при обращении к API: {e}"

# ---------- файлы (база данных) ----------
BASE_DATA_DIR = Path("user_data")
BASE_DATA_DIR.mkdir(exist_ok=True)

def get_user_dir(user_id: int) -> Path:
    user_dir = BASE_DATA_DIR / str(user_id)
    user_dir.mkdir(exist_ok=True)
    return user_dir

def get_accounts_dir(user_id: int) -> Path:
    acc_dir = get_user_dir(user_id) / "accounts"
    acc_dir.mkdir(exist_ok=True)
    return acc_dir

def get_stats_path(user_id: int) -> Path:
    return get_user_dir(user_id) / "stats.json"

def load_stats(user_id: int) -> dict:
    path = get_stats_path(user_id)
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {"total": 0, "today": 0, "exports": 0, "last_date": str(date.today())}

def save_stats(user_id: int, stats: dict):
    try:
        with open(get_stats_path(user_id), "w", encoding="utf-8") as f:
            json.dump(stats, f, ensure_ascii=False, indent=2)
    except IOError as e:
        logging.error(f"Ошибка сохранения статистики для {user_id}: {e}")

def update_stats_on_login(user_id: int, count: int = 1):
    stats = load_stats(user_id)
    today = str(date.today())
    if stats.get("last_date") != today:
        stats["today"] = 0
        stats["last_date"] = today
    stats["total"] += count
    stats["today"] += count
    save_stats(user_id, stats)

def update_stats_on_export(user_id: int):
    stats = load_stats(user_id)
    stats["exports"] += 1
    save_stats(user_id, stats)

def make_zip_archive(files: list, tmp_dir: Path, zip_path: Path):
    tmp_dir.mkdir(exist_ok=True)
    for f in files:
        try: shutil.copy(f, tmp_dir / f.name)
        except: pass
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in tmp_dir.iterdir():
            zf.write(f, arcname=f.name)

def cleanup_dirs(*paths):
    for p in paths:
        if isinstance(p, Path) and p.exists():
            if p.is_dir(): shutil.rmtree(p, ignore_errors=True)
            else:
                try: p.unlink()
                except: pass

def clear_all_accounts(acc_dir: Path):
    count, errors = 0, 0
    for f in acc_dir.glob("*.*"):
        try:
            f.unlink()
            count += 1
        except: errors += 1
    return count, errors

user_sessions = {}
user_locks = {}

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

class ClearConfirm(StatesGroup):
    first = State()
    second = State()

main_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🔐 Войти в аккаунт")],
        [KeyboardButton(text="🔄 Сменить оборудование")],
        [KeyboardButton(text="📊 База аккаунтов"), KeyboardButton(text="📦 Выгрузить базу")],
        [KeyboardButton(text="🗑 Очистить базу")],
    ],
    resize_keyboard=True,
    input_field_placeholder="Выберите действие"
)

async def close_user_session(user_id: int):
    session = user_sessions.pop(user_id, None)
    if session:
        try:
            if session.get("browser"):
                await session["browser"].close()
        except: pass

async def extract_account_data(page: Page) -> Optional[dict]:
    try:
        await asyncio.sleep(2)
        local_storage = await page.evaluate("() => JSON.stringify(localStorage)")
        ls = json.loads(local_storage)
        device_id = ls.get("__oneme_device_id", "")
        auth_data = ls.get("__oneme_auth", "")
        
        if not auth_data: return None

        phone = None
        phone_regex = r'(?:\+7|8|7)[\s\-]?\(?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}'

        match = re.search(phone_regex, local_storage)
        if match: phone = match.group(0)

        if not phone:
            body_text = await page.inner_text("body")
            match = re.search(phone_regex, body_text)
            if match: phone = match.group(0)

        if not phone:
            selectors = ["div.avatar", ".profile-btn", "button[aria-label='Профиль']", "text='Настройки'", "text='Settings'", ".settings-btn"]
            for sel in selectors:
                try:
                    el = page.locator(sel).first
                    if await el.is_visible(timeout=1000):
                        await el.click(timeout=1000)
                        await asyncio.sleep(1)
                        body_text = await page.inner_text("body")
                        match = re.search(phone_regex, body_text)
                        if match:
                            phone = match.group(0)
                            break
                except: continue

        if phone:
            phone = re.sub(r'\D', '', phone)
            if phone.startswith('8') and len(phone) == 11: phone = '7' + phone[1:]
            elif len(phone) == 10: phone = '7' + phone
        else:
            try:
                auth_json = json.loads(auth_data)
                viewer_id = auth_json.get("viewerId")
                phone = f"id{viewer_id}" if viewer_id else f"unknown_{int(datetime.now().timestamp())}"
            except:
                phone = f"unknown_{int(datetime.now().timestamp())}"

        return {"phone": phone, "device_id": device_id, "auth_data": auth_data}
    except Exception as e:
        logging.error(f"Ошибка извлечения данных: {e}")
        return None

async def monitor_single_login(page: Page, index: int, user_id: int, message: types.Message) -> Optional[dict]:
    try:
        await page.wait_for_selector("div.qr svg, canvas", state="detached", timeout=LOGIN_TIMEOUT)
        await message.answer(f"✅ **Аккаунт {index}**: Вход выполнен! Извлекаю данные...", parse_mode="Markdown")
        
        data = await extract_account_data(page)
        if not data:
            await message.answer(f"❌ **Аккаунт {index}**: Не удалось извлечь токен.", parse_mode="Markdown")
            return None
        return data
        
    except (asyncio.TimeoutError, PlaywrightTimeoutError):
        await message.answer(f"⚠️ **Аккаунт {index}**: Время ожидания сканирования истекло.", parse_mode="Markdown")
        return None
    except Exception as e:
        await message.answer(f"❌ **Аккаунт {index}**: Ошибка входа.", parse_mode="Markdown")
        return None

async def multi_login_process(user_id: int, message: types.Message, count: int, state: FSMContext):
    if user_id not in user_locks:
        user_locks[user_id] = asyncio.Lock()
    
    if user_locks[user_id].locked():
        await message.answer("⚠️ Вы уже выполняете вход. Дождитесь завершения.")
        return
    
    async with user_locks[user_id]:
        temp_files = []
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(
                    headless=True,
                    args=["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-gpu"]
                )
                
                proxy_settings = {
                    "server": PROXY_SERVER,
                    "username": PROXY_USERNAME,
                    "password": PROXY_PASSWORD
                }
                
                user_sessions[user_id] = {"browser": browser, "pages": []}
                pages_to_monitor = []

                for i in range(1, count + 1):
                    try:
                        # === СМЕНА IP ПЕРЕД ГЕНЕРАЦИЕЙ QR ===
                        await message.answer(f"🔄 **Аккаунт {i}**: Меняю IP-адрес прокси...")
                        ip_changed = await asyncio.to_thread(sync_change_ip_link)
                        if ip_changed:
                            # Ждем 3 секунды, чтобы прокси успел применить новый IP
                            await asyncio.sleep(3)
                        else:
                            await message.answer(f"⚠️ Не удалось сменить IP по ссылке. Продолжаю со старым...")

                        context = await browser.new_context(
                            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                            viewport={"width": 1000, "height": 800},
                            proxy=proxy_settings
                        )
                        page = await context.new_page()
                        await page.route("**/*.{mp4,webm,gif}", lambda route: route.abort())
                        user_sessions[user_id]["pages"].append(page)

                        await page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=QR_LOAD_TIMEOUT)
                        
                        qr_element = page.locator(QR_SELECTOR).first
                        if not await qr_element.count():
                            qr_element = page.locator("div.qr svg").first
                            
                        await qr_element.wait_for(state="visible", timeout=15000)
                        
                        await qr_element.evaluate('''el => {
                            el.style.backgroundColor = 'white';
                            el.style.padding = '15px';
                            el.style.borderRadius = '10px';
                            el.style.transform = 'scale(1.1)'; 
                        }''')
                        await asyncio.sleep(0.5)

                        temp_file = f"qr_{user_id}_{i}_{int(datetime.now().timestamp())}.jpg"
                        temp_files.append(temp_file)
                        screenshot_bytes = await qr_element.screenshot(type="jpeg", quality=100)
                        await asyncio.to_thread(lambda p=temp_file, b=screenshot_bytes: open(p, "wb").write(b))
                        
                        await message.answer_photo(
                            FSInputFile(temp_file),
                            caption=f"🔐 **Аккаунт {i}**\nОтсканируйте QR-код\n⏱ У вас есть 60 секунд",
                            parse_mode="Markdown"
                        )
                        pages_to_monitor.append((page, i))
                        
                    except Exception as e:
                        logging.error(f"Ошибка получения QR для аккаунта {i}: {e}")
                        await message.answer(f"❌ Ошибка генерации QR для **Аккаунта {i}**.", parse_mode="Markdown")

                if not pages_to_monitor:
                    await message.answer("❌ Ни один QR-код не загрузился. Завершаю сессию.")
                    return

                monitor_tasks = [monitor_single_login(p, idx, user_id, message) for p, idx in pages_to_monitor]
                results = await asyncio.gather(*monitor_tasks, return_exceptions=True)

                valid_data = [res for res in results if isinstance(res, dict) and res is not None]

                if not valid_data:
                    await message.answer("❌ Ни один аккаунт не был авторизован.")
                    return

                acc_dir = get_accounts_dir(user_id)
                saved_files = []
                
                for data in valid_data:
                    phone, device_id, auth_data = data["phone"], data["device_id"], data["auth_data"]
                    
                    for old_file in acc_dir.glob(f"{phone}.*"):
                        try: await asyncio.to_thread(old_file.unlink)
                        except: pass
                    
                    file_path = acc_dir / f"{phone}.txt"
                    js_string = (
                        f"sessionStorage.clear();\n"
                        f"localStorage.clear();\n"
                        f"localStorage.setItem('__oneme_device_id','{device_id}');\n"
                        f"localStorage.setItem('__oneme_auth','{auth_data}');\n"
                        f"window.location.reload();"
                    )

                    try:
                        await asyncio.to_thread(lambda p=file_path, js=js_string: open(p, "w", encoding="utf-8").write(js))
                        saved_files.append((phone, file_path))
                    except IOError: pass

                if saved_files:
                    update_stats_on_login(user_id, len(saved_files))
                    phones_text = "\n".join([f"📱 `{p}`" for p, _ in saved_files])
                    
                    await message.answer(
                        f"✅ **Успешно сохранено аккаунтов: {len(saved_files)} из {count}!**\n\n"
                        f"{phones_text}\n\n"
                        f"📁 Файлы добавлены в базу.\n📤 Отправляю их вам...",
                        parse_mode="Markdown"
                    )
                    
                    for phone, path in saved_files:
                        try: await message.answer_document(FSInputFile(path))
                        except Exception as e: logging.error(f"Ошибка отправки файла {path}: {e}")
                else:
                    await message.answer("❌ Ошибка при сохранении файлов.")

        except Exception as e:
            logging.error(f"Critical error: {e}")
            await message.answer("❌ Произошла критическая ошибка.")
        finally:
            await close_user_session(user_id)
            for f in temp_files:
                if os.path.exists(f):
                    await asyncio.to_thread(cleanup_dirs, Path(f))

# ---------- ОБРАБОТЧИКИ КОМАНД ----------

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "👋 Добро пожаловать в менеджер аккаунтов Max!\n\n"
        "Используйте кнопки ниже для управления.",
        reply_markup=main_kb
    )

# === КНОПКА СМЕНЫ ОБОРУДОВАНИЯ ===
@dp.message(F.text == "🔄 Сменить оборудование")
async def handle_change_equipment(message: types.Message):
    msg = await message.answer("⚙️ Отправляю запрос на смену оборудования (Локации) провайдеру...")
    
    success, response_text = await asyncio.to_thread(sync_change_equipment)
    
    if success:
        await msg.edit_text(f"✅ {response_text}")
    else:
        await msg.edit_text(f"⚠️ {response_text}")

@dp.message(F.text == "🔐 Войти в аккаунт")
async def handle_login_start(message: types.Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="1️⃣", callback_data="login_count_1"),
            InlineKeyboardButton(text="2️⃣", callback_data="login_count_2"),
            InlineKeyboardButton(text="3️⃣", callback_data="login_count_3")
        ]
    ])
    await message.answer("🔢 Сколько аккаунтов хотите добавить за один раз?", reply_markup=kb)

@dp.callback_query(F.data.startswith("login_count_"))
async def process_login_count(callback: CallbackQuery, state: FSMContext):
    count = int(callback.data.split("_")[-1])
    user_id = callback.from_user.id
    await callback.answer()
    
    if user_id in user_sessions:
        await close_user_session(user_id)
        await callback.message.answer("🔄 Предыдущая сессия сброшена.")
    
    await callback.message.edit_text(f"🚀 Начинаю подготовку {count} QR-кода(ов)...\n(Перед каждым QR будет меняться IP прокси)")
    asyncio.create_task(multi_login_process(user_id, callback.message, count, state))

@dp.message(F.text == "📊 База аккаунтов")
async def handle_stats(message: types.Message):
    user_id = message.from_user.id
    stats = load_stats(user_id)
    acc_dir = get_accounts_dir(user_id)
    total_files = len(list(acc_dir.glob("*.*")))
    
    await message.answer(
        "📊 **Статистика аккаунтов**\n\n"
        f"▪️ Всего загружено: `{stats['total']}`\n"
        f"▪️ За сегодня: `{stats['today']}`\n"
        f"▪️ Файлов в базе: `{total_files}`",
        parse_mode="Markdown"
    )

@dp.message(F.text == "📦 Выгрузить базу")
async def handle_export_all(message: types.Message):
    user_id = message.from_user.id
    acc_dir = get_accounts_dir(user_id)

    if not acc_dir.exists() or not any(acc_dir.iterdir()):
        return await message.answer("⚠️ База пуста.")

    await message.answer("📦 Собираю архив со всеми аккаунтами. Ожидайте...")

    tmp_dir = Path(f"tmp_export_{user_id}_{int(datetime.now().timestamp())}")
    zip_path = Path(f"export_{user_id}_{int(datetime.now().timestamp())}.zip")

    try:
        files = list(acc_dir.glob("*.*"))
        if not files: return await message.answer("⚠️ Нет аккаунтов в базе.")

        await asyncio.to_thread(make_zip_archive, files, tmp_dir, zip_path)
        update_stats_on_export(user_id)
        
        await message.answer(f"✅ Архив готов! Найдено файлов: {len(files)}\n\nОтправляю...")
        await bot.send_document(user_id, FSInputFile(zip_path))
            
    except Exception: await message.answer("❌ Ошибка при создании архива.")
    finally: await asyncio.to_thread(cleanup_dirs, tmp_dir, zip_path)

@dp.message(F.text == "🗑 Очистить базу")
async def handle_clear_start(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    acc_dir = get_accounts_dir(user_id)
    total_files = len(list(acc_dir.glob("*.*")))
    
    if total_files == 0: return await message.answer("⚠️ База уже пуста.")

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да", callback_data="clear_confirm_1"), InlineKeyboardButton(text="❌ Нет", callback_data="clear_cancel")]
    ])
    await message.answer(f"⚠️ Точно очистить базу?\n📊 Файлов: `{total_files}`", reply_markup=kb, parse_mode="Markdown")
    await state.set_state(ClearConfirm.first)

@dp.callback_query(F.data == "clear_cancel")
async def clear_cancel(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.edit_text("❌ Очистка отменена.")
    await state.clear()

@dp.callback_query(F.data == "clear_confirm_1", ClearConfirm.first)
async def clear_confirm_first(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, удалить", callback_data="clear_confirm_2"), InlineKeyboardButton(text="❌ Отмена", callback_data="clear_cancel")]
    ])
    await callback.message.edit_text("‼️ **Последнее предупреждение!**\nУверены?", reply_markup=kb, parse_mode="Markdown")
    await state.set_state(ClearConfirm.second)

@dp.callback_query(F.data == "clear_confirm_2", ClearConfirm.second)
async def clear_confirm_second(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    await callback.answer()
    count, _ = await asyncio.to_thread(clear_all_accounts, get_accounts_dir(user_id))
    await callback.message.edit_text(f"✅ База очищена!\n\n📊 Удалено файлов: {count}")
    await state.clear()

async def on_shutdown():
    for user_id in list(user_sessions.keys()): await close_user_session(user_id)

async def main():
    dp.shutdown.register(on_shutdown)
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    import subprocess
    import sys
    
    print("⏳ Начинаю скачивание браузера Chromium (это займет 1-2 минуты)...")
    try:
        # Питон сам скачивает браузер через свои модули
        subprocess.check_call([sys.executable, "-m", "playwright", "install", "chromium"])
        print("✅ Браузер успешно скачан и установлен!")
    except Exception as e:
        print(f"⚠️ Ошибка при скачивании браузера: {e}")

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Бот остановлен")
    except Exception as e:
        logging.error(f"Критическая ошибка: {e}", exc_info=True)