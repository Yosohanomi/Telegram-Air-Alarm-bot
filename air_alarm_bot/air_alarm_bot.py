import asyncio
import json
import os
import time
import sqlite3

from aiogram import Bot, Dispatcher, types, F
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, \
    Message, CallbackQuery
from config import *
import aiohttp


# =========================================================
# BOT
# =========================================================

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


# =========================================================
# КЕШ
# =========================================================

REGIONS_CACHE_FILE = "regions_cache.json"
DB_FILE = "bot_users.db"

# Кеш тривог (у пам'яті, на 60 секунд)
_alerts_cache = {
    "data": None,
    "timestamp": 0
}
CACHE_TTL = 60

# Розшифровка типів загроз
ALERT_TYPES = {
    "air_raid": "Повітряна тривога",
    "artillery_shelling": "Артобстріл",
    "urban_fights": "Вуличні бої",
    "chemical": "Хімічна загроза",
    "nuclear": "Ядерна загроза",
    "radiological": "Радіаційна загроза",
}


# =========================================================
# БАЗА ДАНИХ
# =========================================================

def init_db():
    conn = sqlite3.connect(DB_FILE)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            region_id INTEGER,
            region_name TEXT
        )
    """)
    conn.commit()
    conn.close()


def save_user_region(user_id, region_id, region_name):
    conn = sqlite3.connect(DB_FILE)
    conn.execute("""
        INSERT INTO users (user_id, region_id, region_name)
        VALUES (?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            region_id = excluded.region_id,
            region_name = excluded.region_name
    """, (user_id, region_id, region_name))
    conn.commit()
    conn.close()


def get_user_region(user_id):
    conn = sqlite3.connect(DB_FILE)
    row = conn.execute(
        "SELECT region_id, region_name FROM users WHERE user_id = ?",
        (user_id,)
    ).fetchone()
    conn.close()
    return {"id": row[0], "name": row[1]} if row else None


# =========================================================
# API REQUEST
# =========================================================

async def api_get(endpoint):

    url = f"{API_URL}{endpoint}"

    headers = {
        "Accept": "application/json",
        "Authorization": API_KEY
    }

    try:

        async with aiohttp.ClientSession() as session:

            async with session.get(
                url,
                headers=headers
            ) as response:

                print(f"API {endpoint} -> {response.status}")

                if response.status == 200:

                    data = await response.json()

                    return data

                if response.status == 401:

                    print("❌ API KEY неправильний або недійсний (можливо, перевищено ліміт запитів)")

                    return None

                text = await response.text()

                print(
                    f"❌ API error: {response.status}"
                )

                print(text)

                return None

    except Exception as e:

        print(
            f"❌ Помилка підключення до API: {e}"
        )

        return None


# =========================================================
# ОТРИМАННЯ ОБЛАСТЕЙ (З КЕШЕМ)
# =========================================================

async def get_regions(force_refresh=False):

    if not force_refresh and os.path.exists(REGIONS_CACHE_FILE):

        with open(REGIONS_CACHE_FILE, "r", encoding="utf-8") as f:

            data = json.load(f)

            regions = data.get("states", [])

            print("📦 Області з кешу:", len(regions))

            return regions

    data = await api_get("/regions")

    if data is None:
        return []

    if not isinstance(data, dict):
        print("❌ API повернув некоректний формат")
        print("Тип:", type(data))
        print("Дані:", data)
        return []

    with open(REGIONS_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    regions = data.get("states", [])

    print("🌐 Області завантажено з API та закешовано:", len(regions))

    return regions


# =========================================================
# ОТРИМАННЯ ТРИВОГ (З КЕШЕМ НА 60 СЕКУНД)
# =========================================================

async def get_alerts_cached():

    now = time.time()

    # Якщо кеш свіжий — повертаємо його
    if _alerts_cache["data"] is not None and (now - _alerts_cache["timestamp"]) < CACHE_TTL:

        print("📦 Тривоги з кешу")

        return _alerts_cache["data"]

    # Інакше — запит до API
    data = await api_get("/alerts")

    if data is not None:

        _alerts_cache["data"] = data
        _alerts_cache["timestamp"] = now

        print("🌐 Тривоги завантажено з API")

    else:

        print("⚠️ API не відповів, використовуємо старий кеш")

    return _alerts_cache["data"]


# =========================================================
# КЛАВІАТУРА ОБЛАСТЕЙ
# =========================================================

async def create_regions_keyboard():

    regions = await get_regions()

    buttons = []

    row = []

    for region in regions:

        if not isinstance(region, dict):
            continue

        # Нас цікавлять тільки області / міста
        if region.get("regionType") != "State":
            continue

        region_id = region.get("regionId")
        region_name = region.get("regionName")

        if not region_id or not region_name:
            continue

        button = InlineKeyboardButton(
            text=region_name,
            callback_data=f"region_{region_id}"
        )

        row.append(button)

        if len(row) == 2:

            buttons.append(row)

            row = []

    if row:
        buttons.append(row)

    return InlineKeyboardMarkup(
        inline_keyboard=buttons
    )


# =========================================================
# ГОЛОВНЕ МЕНЮ (ДЛЯ ЗБЕРЕЖЕНОЇ ОБЛАСТІ)
# =========================================================

def get_main_keyboard():

    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🔴 Моя область")],
            [KeyboardButton(text="🗺 Всі тривоги")],
            [KeyboardButton(text="🔄 Змінити область")],
        ],
        resize_keyboard=True
    )

    return keyboard


# =========================================================
# /START
# =========================================================

@dp.message(Command("start"))
async def start(message: Message):

    await info(message)
    await message.answer(
        "📍 <b>Оберіть область:</b>",
        parse_mode=ParseMode.HTML
    )

    keyboard = await create_regions_keyboard()

    if not keyboard.inline_keyboard:
        await message.answer(
            "❌ Не вдалося отримати список областей.\n\n"
            "Перевір API ключ."
        )
        return

    await message.answer(
        "📍 <b>Оберіть область:</b>",
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML
    )

# =========================================================
# /INFO
# =========================================================
@dp.message(Command("info"))
async def info(message: types.Message):
    await message.answer(
        "ℹ️ <b>Про бота</b>\n\n"
        "🚨 <b>Бот повітряних тривог</b> — твій помічник "
        "для відстеження небезпек в Україні.\n\n"
        "<b>Що вміє:</b>\n"
        "📍 Обирати область і запам'ятовувати її\n"
        "🔴 Показувати статус тривоги у твоїй області\n"
        "🗺 Показувати всі активні тривоги по Україні\n"
        "⚠️ Розпізнавати тип загрози (ракети, обстріл, хімія тощо)\n\n"
        "<b>Джерело даних:</b>\n"
        "🇺🇦 Офіційне API <code>api.ukrainealarm.com</code>\n\n"
        "💙💛 Бережіть себе та рідних!",
        parse_mode="HTML"
    )

# =========================================================
# ВИБІР ОБЛАСТІ
# =========================================================

def find_region_by_id(regions, region_id):
    for region in regions:
        if int(region.get("regionId", -1)) == region_id:
            return region
        children = region.get("regionChildIds", [])
        found = find_region_by_id(children, region_id)

        if found:
          return found
    return None

@dp.callback_query(F.data.startswith("region_"))
async def select_region(callback: CallbackQuery):

    region_id = int(callback.data.replace("region_", ""))

    await callback.answer()

    print("==================")
    print("Обрана область", region_id)

    regions = await get_regions()
    region = find_region_by_id(regions, region_id)
    if region:
        region_name = region.get("regionName", "Невідома")
    else:
        region_name = "Невідома"

    save_user_region(callback.from_user.id, region_id, region_name)

    print("✅ Збережено:", region_name)

    await callback.message.edit_text(
        f"✅ Область <b>{region_name}</b> збережена!\n\n"
        f"Тепер я надсилатиму сповіщення про тривоги."
        , parse_mode=ParseMode.HTML
    )

    # Показуємо нове меню
    await callback.message.answer(
        "Оберіть дію:",
        reply_markup=get_main_keyboard()
    )


# =========================================================
# "МОЯ ОБЛАСТЬ"
# =========================================================

@dp.message(F.text == "🔴 Моя область")
async def my_region(message: Message):

    saved = get_user_region(message.from_user.id)

    if not saved:
        await message.answer("Спочатку оберіть область через /start")
        return

    alerts = await get_alerts_cached()

    if alerts is None:
        await message.answer("⚠️ Не вдалося отримати дані. Спробуйте пізніше.")
        return

    region_alert = None

    for a in alerts:
        if a.get("regionId") == saved["id"]:
            region_alert = a
            break

    if region_alert and region_alert.get("activeAlerts"):

        # Є тривога
        types = []

        for alert in region_alert["activeAlerts"]:

            alert_type = alert.get("type", "UNKNOWN")
            types.append(ALERT_TYPES.get(alert_type, alert_type))

        await message.answer(
            f"🚨 <b>ТРИВОГА!</b>\n\n"
            f"📍 {saved['name']}\n"
            f"⚠️ Загроза: {', '.join(types)}"
            , parse_mode=ParseMode.HTML
        )

    else:

        await message.answer(
            f"✅ <b>Відбій</b>\n\n"
            f"📍 {saved['name']}\n"
            f"Тривоги немає."
            , parse_mode=ParseMode.HTML
        )


# =========================================================
# "ВСІ ТРИВОГИ"
# =========================================================

@dp.message(F.text == "🗺 Всі тривоги")
async def all_alerts(message: Message):

    alerts = await get_alerts_cached()

    if alerts is None:
        await message.answer("⚠️ Не вдалося отримати дані. Спробуйте пізніше.")
        return

    if not alerts:
        await message.answer("✅ Наразі тривог немає по всій Україні.")
        return

    lines = []

    for a in alerts:

        if not a.get("activeAlerts"):
            continue

        name = a.get("regionName", "Невідомо")

        types = []

        for alert in a["activeAlerts"]:
            alert_type = alert.get("type", "UNKNOWN")
            types.append(ALERT_TYPES.get(alert_type, alert_type))

        lines.append(f"🔴 <b>{name}</b> — {', '.join(types)}" )

    if not lines:
        await message.answer("✅ Наразі тривог немає по всій Україні.")
        return

    await message.answer(
        f"🗺 <b>Активні тривоги:</b>\n\n" + "\n".join(lines)
        , parse_mode=ParseMode.HTML
    )


# =========================================================
# "ЗМІНИТИ ОБЛАСТЬ"
# =========================================================

@dp.message(F.text == "🔄 Змінити область")
async def change_region(message: Message):

    keyboard = await create_regions_keyboard()

    if not keyboard.inline_keyboard:
        await message.answer("❌ Не вдалося отримати список областей.")
        return

    await message.answer(
        "📍 <b>Оберіть нову область:</b>",
        reply_markup=keyboard
        , parse_mode=ParseMode.HTML
    )

# =========================================================
# /HELP
# =========================================================
@dp.message(Command('help'))
async def help_command(message: types.Message):
    await message.answer(
        "🚨 <b>Довідка по боту</b>\n\n"
        "Доступні команди:\n\n"
        "/start — запустити бота\n"
        "/help — довідка по боту\n"
        "/info — інформація про бота\n"
        "/all_air_alarm — всі тривоги зараз\n"
        "/my_air_alarm — тривоги у моїй області\n\n"
        "Або скористайся кнопками нижче 👇",
        parse_mode="HTML"
    )


# =========================================================
# /ALL_AIR_ALARM — всі тривоги зараз
# =========================================================

@dp.message(Command('all_air_alarm'))
async def cmd_all_air_alarm(message: Message):
    await all_alerts(message)


# =========================================================
# /MY_AIR_ALARM — тривоги у моїй області
# =========================================================

@dp.message(Command('my_air_alarm'))
async def cmd_my_air_alarm(message: Message):
    await my_region(message)


# =========================================================
# MAIN
# =========================================================

async def main():

    init_db()

    await bot.set_my_commands([
        BotCommand(command='start', description='Запустити бота'),
        BotCommand(command='help', description='Довідка'),
        BotCommand(command='info', description='Про бота'),
        BotCommand(command='all_air_alarm', description='Всі тривоги зараз'),
        BotCommand(command='my_air_alarm', description='Тривоги у моїй області'),
    ])

    print('Bot started.')

    await dp.start_polling(bot)


if __name__ == '__main__':
    asyncio.run(main())