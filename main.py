"""
BEKS Lab — Telegram Bot Server
Стек: aiogram 3.x + aiohttp + SQLite
"""

import os
import logging
import sqlite3
from aiohttp import web
from aiogram import Bot, Dispatcher, types, F
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    WebAppInfo,
    MenuButtonWebApp,
)
from aiogram.filters import CommandStart, Command
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

# ---------- Конфиг ----------

BOT_TOKEN = os.environ["BOT_TOKEN"]
WEBAPP_URL = os.environ["WEBAPP_URL"]
CHANNEL_ID = os.environ.get("CHANNEL_ID", "")
CHAT_ID = os.environ.get("CHAT_ID", "")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))
WEBHOOK_HOST = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "")
WEBHOOK_PATH = f"/webhook/{BOT_TOKEN}"
PORT = int(os.environ.get("PORT", 8080))

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("beks-bot")

# ---------- База данных ----------

def init_db():
    conn = sqlite3.connect("beks.db")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            tg_id      INTEGER PRIMARY KEY,
            username   TEXT,
            first_name TEXT,
            is_pro     INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

def save_user(user: types.User):
    conn = sqlite3.connect("beks.db")
    conn.execute(
        "INSERT OR IGNORE INTO users (tg_id, username, first_name) VALUES (?, ?, ?)",
        (user.id, user.username, user.first_name),
    )
    conn.commit()
    conn.close()

def set_pro(tg_id: int, status: int = 1):
    conn = sqlite3.connect("beks.db")
    conn.execute("UPDATE users SET is_pro = ? WHERE tg_id = ?", (status, tg_id))
    conn.commit()
    conn.close()

def is_pro(tg_id: int) -> bool:
    conn = sqlite3.connect("beks.db")
    cur = conn.execute("SELECT is_pro FROM users WHERE tg_id = ?", (tg_id,))
    row = cur.fetchone()
    conn.close()
    return bool(row and row[0])

def get_user_count() -> int:
    conn = sqlite3.connect("beks.db")
    cur = conn.execute("SELECT COUNT(*) FROM users")
    count = cur.fetchone()[0]
    conn.close()
    return count

def get_pro_count() -> int:
    conn = sqlite3.connect("beks.db")
    cur = conn.execute("SELECT COUNT(*) FROM users WHERE is_pro = 1")
    count = cur.fetchone()[0]
    conn.close()
    return count

# ---------- Проверка подписки на канал ----------

async def check_subscription(user_id: int) -> bool:
    if not CHANNEL_ID:
        return True
    try:
        member = await bot.get_chat_member(CHANNEL_ID, user_id)
        if member.status in ("left", "kicked"):
            return False
    except Exception:
        return True

    if CHAT_ID:
        try:
            member = await bot.get_chat_member(CHAT_ID, user_id)
            if member.status in ("left", "kicked"):
                return False
        except Exception:
            pass
    return True

# ---------- Хендлеры ----------

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    save_user(message.from_user)
    is_subscribed = await check_subscription(message.from_user.id)

    if not is_subscribed:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="📢 Канал", url=f"https://t.me/{CHANNEL_ID.replace('@', '')}"),
                InlineKeyboardButton(text="💬 Чат", url=f"https://t.me/{CHAT_ID.replace('@', '')}"),
            ],
            [
                InlineKeyboardButton(text="✅ Проверить подписку", callback_data="check_sub"),
            ],
        ])
        await message.answer(
            "🔒 **Доступ закрыт**\n\n"
            "Чтобы открыть BEKS Lab, подпишись на канал и чат. "
            "После подписки нажми «Проверить подписку».",
            reply_markup=kb,
            parse_mode="Markdown",
        )
    else:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🚀 Открыть BEKS Lab",
                    web_app=WebAppInfo(url=WEBAPP_URL),
                ),
            ],
        ])
        pro_status = "⭐ PRO активен" if is_pro(message.from_user.id) else ""
        await message.answer(
            f"✅ Добро пожаловать в **BEKS Lab**!\n{pro_status}\n\n"
            "Нажми кнопку ниже, чтобы открыть приложение.",
            reply_markup=kb,
            parse_mode="Markdown",
        )


@dp.callback_query(F.data == "check_sub")
async def check_sub_callback(callback: types.CallbackQuery):
    is_subscribed = await check_subscription(callback.from_user.id)
    if is_subscribed:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🚀 Открыть BEKS Lab",
                    web_app=WebAppInfo(url=WEBAPP_URL),
                ),
            ],
        ])
        await callback.message.edit_text(
            "✅ Подписка подтверждена! Теперь можно открыть BEKS Lab.",
            reply_markup=kb,
        )
    else:
        await callback.answer("Ты ещё не подписался на канал и чат.", show_alert=True)


# ---------- Админ-команды ----------

@dp.message(Command("myid"))
async def cmd_myid(message: types.Message):
    """Показывает Telegram ID пользователя."""
    await message.answer(f"Твой Telegram ID: `{message.from_user.id}`", parse_mode="Markdown")


@dp.message(Command("pro"))
async def cmd_pro(message: types.Message):
    """Админ выдаёт PRO себе или другому пользователю."""
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔ Нет доступа.")
        return

    args = message.text.split()
    if len(args) > 1:
        try:
            target_id = int(args[1])
        except ValueError:
            await message.answer("Используй: /pro 123456789")
            return
    else:
        target_id = message.from_user.id

    set_pro(target_id, 1)
    await message.answer(f"✅ PRO активирован для `{target_id}`", parse_mode="Markdown")


@dp.message(Command("unpro"))
async def cmd_unpro(message: types.Message):
    """Админ забирает PRO."""
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔ Нет доступа.")
        return

    args = message.text.split()
    if len(args) > 1:
        try:
            target_id = int(args[1])
        except ValueError:
            await message.answer("Используй: /unpro 123456789")
            return
    else:
        target_id = message.from_user.id

    set_pro(target_id, 0)
    await message.answer(f"❌ PRO деактивирован для `{target_id}`", parse_mode="Markdown")


@dp.message(Command("stats"))
async def cmd_stats(message: types.Message):
    """Админ смотрит статистику."""
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔ Нет доступа.")
        return

    total = get_user_count()
    pro = get_pro_count()
    await message.answer(
        f"📊 **Статистика BEKS Lab**\n\n"
        f"Всего пользователей: {total}\n"
        f"PRO подписчиков: {pro}",
        parse_mode="Markdown",
    )


# ---------- Запуск ----------

async def on_startup(app: web.Application):
    init_db()
    webhook_url = f"https://{WEBHOOK_HOST}{WEBHOOK_PATH}"
    await bot.set_webhook(webhook_url)
    try:
        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(
                text="BEKS Lab",
                web_app=WebAppInfo(url=WEBAPP_URL),
            )
        )
    except Exception as e:
        log.warning(f"Не удалось установить menu button: {e}")
    log.info(f"Webhook set: {webhook_url}")
    log.info(f"Users in DB: {get_user_count()}")


async def on_shutdown(app: web.Application):
    await bot.delete_webhook()
    await bot.session.close()


def main():
    app = web.Application()
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)

    webhook_handler = SimpleRequestHandler(dispatcher=dp, bot=bot)
    webhook_handler.register(app, path=WEBHOOK_PATH)
    setup_application(app, dp, bot=bot)

    async def health(request):
        return web.json_response({"status": "ok", "users": get_user_count()})

    app.router.add_get("/", health)
    web.run_app(app, host="0.0.0.0", port=PORT)


if __name__ == "__main__":
    main()
