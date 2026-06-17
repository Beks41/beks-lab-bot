"""
BEKS Lab — Telegram Bot Server
Стек: aiogram 3.x + aiohttp + SQLite
Хостинг: Railway.app (бесплатно)
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
from aiogram.filters import CommandStart
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

# ---------- Конфиг ----------

BOT_TOKEN = os.environ["BOT_TOKEN"]                # токен от @BotFather
WEBAPP_URL = os.environ["WEBAPP_URL"]               # URL фронтенда на Vercel (например https://beks-lab.vercel.app)
CHANNEL_ID = os.environ.get("CHANNEL_ID", "")       # @твой_канал (опционально, для проверки подписки)
CHAT_ID = os.environ.get("CHAT_ID", "")             # @твой_чат (опционально)
WEBHOOK_HOST = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "")  # Railway сам подставит
WEBHOOK_PATH = f"/webhook/{BOT_TOKEN}"
PORT = int(os.environ.get("PORT", 8080))

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("beks-bot")

# ---------- База данных (SQLite) ----------

def init_db():
    """Создаёт таблицу пользователей если её нет."""
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
    """Сохраняет или обновляет пользователя."""
    conn = sqlite3.connect("beks.db")
    conn.execute(
        "INSERT OR REPLACE INTO users (tg_id, username, first_name) VALUES (?, ?, ?)",
        (user.id, user.username, user.first_name),
    )
    conn.commit()
    conn.close()

def get_user_count() -> int:
    conn = sqlite3.connect("beks.db")
    cur = conn.execute("SELECT COUNT(*) FROM users")
    count = cur.fetchone()[0]
    conn.close()
    return count

# ---------- Проверка подписки на канал ----------

async def check_subscription(user_id: int) -> bool:
    """Проверяет, подписан ли пользователь на канал и чат."""
    if not CHANNEL_ID:
        return True  # если канал не задан — пропускаем

    try:
        member = await bot.get_chat_member(CHANNEL_ID, user_id)
        if member.status in ("left", "kicked"):
            return False
    except Exception:
        return True  # если ошибка — пропускаем проверку

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
    """Обработка /start."""
    save_user(message.from_user)

    # Проверяем подписку
    is_subscribed = await check_subscription(message.from_user.id)

    if not is_subscribed:
        # Показываем экран "Доступ закрыт"
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
        # Показываем кнопку открытия mini-app
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🚀 Открыть BEKS Lab",
                    web_app=WebAppInfo(url=WEBAPP_URL),
                ),
            ],
        ])
        await message.answer(
            "✅ Добро пожаловать в **BEKS Lab**!\n\n"
            "Нажми кнопку ниже, чтобы открыть приложение.",
            reply_markup=kb,
            parse_mode="Markdown",
        )


@dp.callback_query(F.data == "check_sub")
async def check_sub_callback(callback: types.CallbackQuery):
    """Повторная проверка подписки."""
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


# ---------- Запуск ----------

async def on_startup(app: web.Application):
    """Устанавливаем webhook при старте."""
    init_db()
    webhook_url = f"https://{WEBHOOK_HOST}{WEBHOOK_PATH}"
    await bot.set_webhook(webhook_url)

    # Устанавливаем кнопку Mini App в меню бота
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
    """Удаляем webhook при остановке."""
    await bot.delete_webhook()
    await bot.session.close()


def main():
    app = web.Application()
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)

    # Подключаем webhook handler
    webhook_handler = SimpleRequestHandler(dispatcher=dp, bot=bot)
    webhook_handler.register(app, path=WEBHOOK_PATH)
    setup_application(app, dp, bot=bot)

    # Простой health-check endpoint (Railway хочет видеть живой HTTP)
    async def health(request):
        return web.json_response({"status": "ok", "users": get_user_count()})

    app.router.add_get("/", health)

    web.run_app(app, host="0.0.0.0", port=PORT)


if __name__ == "__main__":
    main()
