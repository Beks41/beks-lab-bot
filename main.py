"""
BEKS Lab — Telegram Bot + API Server
aiogram 3.x + aiohttp + SQLite + Gemini API
"""
import os, logging, sqlite3, json, hashlib, hmac
from urllib.parse import parse_qs
from aiohttp import web
import aiohttp as aio_client
from aiogram import Bot, Dispatcher, types, F
from aiogram.types import (
    InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo, MenuButtonWebApp,
)
from aiogram.filters import CommandStart, Command
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

BOT_TOKEN = os.environ["BOT_TOKEN"]
WEBAPP_URL = os.environ["WEBAPP_URL"]
GEMINI_KEY = os.environ.get("GEMINI_KEY", "")
CHANNEL_ID = os.environ.get("CHANNEL_ID", "")
CHAT_ID = os.environ.get("CHAT_ID", "")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))
WEBHOOK_HOST = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "")
WEBHOOK_PATH = f"/webhook/{BOT_TOKEN}"
PORT = int(os.environ.get("PORT", 8080))

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("beks")

# ---- DB ----
def get_db():
    c = sqlite3.connect("beks.db"); c.row_factory = sqlite3.Row; return c

def init_db():
    c = get_db()
    c.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            tg_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT,
            is_pro INTEGER DEFAULT 0, settings TEXT DEFAULT '{}',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    c.commit(); c.close()

def save_user(u):
    c = get_db(); c.execute("INSERT OR IGNORE INTO users(tg_id,username,first_name) VALUES(?,?,?)",(u.id,u.username,u.first_name)); c.commit(); c.close()

def set_pro(tid, v=1):
    c = get_db(); c.execute("INSERT OR IGNORE INTO users(tg_id) VALUES(?)",(tid,)); c.execute("UPDATE users SET is_pro=? WHERE tg_id=?",(v,tid)); c.commit(); c.close()

def is_pro(tid):
    c = get_db(); r = c.execute("SELECT is_pro FROM users WHERE tg_id=?",(tid,)).fetchone(); c.close(); return bool(r and r[0])

def get_settings(tid):
    c = get_db(); r = c.execute("SELECT settings FROM users WHERE tg_id=?",(tid,)).fetchone(); c.close()
    if r and r[0]:
        try: return json.loads(r[0])
        except: pass
    return {}

def save_settings(tid, data):
    c = get_db(); c.execute("INSERT OR IGNORE INTO users(tg_id) VALUES(?)",(tid,)); c.execute("UPDATE users SET settings=? WHERE tg_id=?",(json.dumps(data),tid)); c.commit(); c.close()

def user_count():
    c = get_db(); n = c.execute("SELECT COUNT(*) FROM users").fetchone()[0]; c.close(); return n

def pro_count():
    c = get_db(); n = c.execute("SELECT COUNT(*) FROM users WHERE is_pro=1").fetchone()[0]; c.close(); return n

# ---- Telegram initData ----
def parse_init(init_data):
    try:
        p = parse_qs(init_data); h = p.get("hash",[None])[0]
        if not h: return None
        dc = sorted((k,v[0]) for k,v in p.items() if k!="hash")
        s = "\n".join(f"{k}={v}" for k,v in dc)
        sec = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        if hmac.new(sec, s.encode(), hashlib.sha256).hexdigest() != h: return None
        u = p.get("user",[None])[0]
        return json.loads(u) if u else None
    except: return None

def get_tid(req):
    a = req.headers.get("Authorization","")
    if a:
        u = parse_init(a)
        if u and "id" in u: return u["id"]
    t = req.query.get("tg_id")
    if t:
        try: return int(t)
        except: pass
    return None

# ---- Gemini ----
async def gemini(prompt, img_b64=None, mime="image/jpeg"):
    if not GEMINI_KEY:
        log.error("GEMINI_KEY не задан в переменных окружения")
        return "Gemini API ключ не настроен."
    parts = []
    if img_b64: parts.append({"inline_data":{"mime_type":mime,"data":img_b64}})
    parts.append({"text":prompt})
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_KEY}"
    try:
        async with aio_client.ClientSession() as s:
            async with s.post(url, json={"contents":[{"parts":parts}]}) as r:
                body_text = await r.text()
                if r.status != 200:
                    log.error(f"Gemini HTTP {r.status}: {body_text[:1000]}")
                    return f"Ошибка AI (HTTP {r.status}): {body_text[:300]}"
                try:
                    d = json.loads(body_text)
                except Exception as e:
                    log.error(f"Gemini: не удалось разобрать JSON ответа: {e} | raw: {body_text[:500]}")
                    return "AI вернул нечитаемый ответ."
                try:
                    return d["candidates"][0]["content"]["parts"][0]["text"]
                except Exception as e:
                    log.error(f"Gemini: неожиданная структура ответа: {e} | raw: {body_text[:800]}")
                    finish_reason = None
                    try: finish_reason = d["candidates"][0].get("finishReason")
                    except Exception: pass
                    if finish_reason:
                        return f"AI не смог обработать (finishReason={finish_reason})."
                    return "AI не смог обработать."
    except Exception as e:
        log.error(f"Gemini: исключение при запросе: {repr(e)}")
        return f"Ошибка сети при обращении к AI: {repr(e)[:200]}"

# ---- CORS ----
CH = {"Access-Control-Allow-Origin":"*","Access-Control-Allow-Methods":"GET,POST,OPTIONS","Access-Control-Allow-Headers":"Content-Type,Authorization"}

@web.middleware
async def cors_mw(req, handler):
    if req.method == "OPTIONS": return web.Response(headers=CH)
    r = await handler(req); r.headers.update(CH); return r

# ---- API ----
async def api_profile(req):
    tid = get_tid(req)
    if not tid: return web.json_response({"error":"no tg_id"},status=400,headers=CH)
    return web.json_response({"tg_id":tid,"is_pro":is_pro(tid),"settings":get_settings(tid)},headers=CH)

async def api_settings(req):
    tid = get_tid(req)
    if not tid: return web.json_response({"error":"no tg_id"},status=400,headers=CH)
    try: body = await req.json()
    except: return web.json_response({"error":"bad json"},status=400,headers=CH)
    save_settings(tid, body)
    return web.json_response({"ok":True},headers=CH)

async def api_analyze(req):
    try: body = await req.json(); img = body.get("image"); mime = body.get("mime","image/jpeg")
    except: return web.json_response({"error":"bad"},status=400,headers=CH)
    if not img: return web.json_response({"error":"no image"},status=400,headers=CH)
    prompt = ("Проанализируй внешность по фото для приложения BEKS Lab. "
        "Дай структурированный разбор по 4 зонам: Кожа, Брови/взгляд, Волосы/причёска, Общий стиль. "
        "Для каждой: что видно (1-2 предложения, прямо, уважительно) и рекомендацию. "
        "В конце — 'Главный приоритет'. Формат: заголовки зон жирным, затем 'Главный приоритет'.")
    r = await gemini(prompt, img, mime)
    return web.json_response({"report":r},headers=CH)

async def api_calories(req):
    try: body = await req.json(); meal = body.get("meal","")
    except: return web.json_response({"error":"bad"},status=400,headers=CH)
    if not meal: return web.json_response({"error":"no meal"},status=400,headers=CH)
    prompt = f'Оцени КБЖУ для порции: "{meal}". Ответь СТРОГО JSON: {{"kcal":число,"protein":число,"fat":число,"carbs":число}}'
    r = await gemini(prompt)
    try:
        clean = r.replace("```json","").replace("```","").strip()
        return web.json_response(json.loads(clean),headers=CH)
    except:
        return web.json_response({"error":"parse","raw":r},status=500,headers=CH)

async def api_chat(req):
    tid = get_tid(req)
    if not tid:
        return web.json_response({"error":"no tg_id"},status=400,headers=CH)
    if not is_pro(tid):
        return web.json_response({"error":"PRO required"},status=403,headers=CH)
    try:
        body = await req.json()
        message = body.get("message","")
        history = body.get("history",[])
    except:
        return web.json_response({"error":"bad json"},status=400,headers=CH)
    if not message:
        return web.json_response({"error":"no message"},status=400,headers=CH)

    convo = ""
    for m in history[-10:]:
        role = "Пользователь" if m.get("role") == "user" else "BEKS AI"
        convo += f"{role}: {m.get('text','')}\n"

    prompt = (
        "Ты — BEKS AI, ассистент приложения BEKS Lab по уходу за внешностью и self-improvement для мужчин. "
        "Отвечай прямо, по делу, без числовых оценок внешности (никаких баллов X/10, рейтингов привлекательности). "
        "Давай конкретные практические советы по коже, волосам, бровям, стилю, осанке. "
        "При серьёзных проблемах со здоровьем или кожей рекомендуй обратиться к врачу/дерматологу.\n\n"
        f"История диалога:\n{convo}\n"
        f"Пользователь: {message}\nBEKS AI:"
    )
    r = await gemini(prompt)
    return web.json_response({"reply": r}, headers=CH)

# ---- Bot ----
async def check_sub(uid):
    if not CHANNEL_ID: return True
    try:
        m = await bot.get_chat_member(CHANNEL_ID, uid)
        if m.status in ("left","kicked"): return False
    except: return True
    if CHAT_ID:
        try:
            m = await bot.get_chat_member(CHAT_ID, uid)
            if m.status in ("left","kicked"): return False
        except: pass
    return True

@dp.message(CommandStart())
async def cmd_start(msg):
    save_user(msg.from_user)
    if not await check_sub(msg.from_user.id):
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="📢 Канал",url=f"https://t.me/{CHANNEL_ID.replace('@','')}"),
            InlineKeyboardButton(text="💬 Чат",url=f"https://t.me/{CHAT_ID.replace('@','')}")
        ],[InlineKeyboardButton(text="✅ Проверить",callback_data="check_sub")]])
        await msg.answer("🔒 Подпишись на канал и чат.",reply_markup=kb,parse_mode="Markdown")
    else:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🚀 Открыть BEKS Lab",web_app=WebAppInfo(url=WEBAPP_URL))]])
        p = "⭐ PRO" if is_pro(msg.from_user.id) else ""
        await msg.answer(f"✅ **BEKS Lab** {p}\n\nНажми кнопку.",reply_markup=kb,parse_mode="Markdown")

@dp.callback_query(F.data=="check_sub")
async def cb_sub(cb):
    if await check_sub(cb.from_user.id):
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🚀 Открыть BEKS Lab",web_app=WebAppInfo(url=WEBAPP_URL))]])
        await cb.message.edit_text("✅ Подписка ОК!",reply_markup=kb)
    else: await cb.answer("Не подписался.",show_alert=True)

@dp.message(Command("myid"))
async def cmd_myid(msg): await msg.answer(f"ID: `{msg.from_user.id}`",parse_mode="Markdown")

@dp.message(Command("pro"))
async def cmd_pro(msg):
    if msg.from_user.id!=ADMIN_ID: return await msg.answer("⛔")
    a=msg.text.split(); tid=int(a[1]) if len(a)>1 else msg.from_user.id
    set_pro(tid,1); await msg.answer(f"✅ PRO → `{tid}`",parse_mode="Markdown")

@dp.message(Command("unpro"))
async def cmd_unpro(msg):
    if msg.from_user.id!=ADMIN_ID: return await msg.answer("⛔")
    a=msg.text.split(); tid=int(a[1]) if len(a)>1 else msg.from_user.id
    set_pro(tid,0); await msg.answer(f"❌ PRO снят `{tid}`",parse_mode="Markdown")

@dp.message(Command("stats"))
async def cmd_stats(msg):
    if msg.from_user.id!=ADMIN_ID: return await msg.answer("⛔")
    await msg.answer(f"📊 Users: {user_count()} | PRO: {pro_count()}",parse_mode="Markdown")

# ---- Start ----
async def on_startup(app):
    init_db(); url=f"https://{WEBHOOK_HOST}{WEBHOOK_PATH}"
    await bot.set_webhook(url)
    try: await bot.set_chat_menu_button(menu_button=MenuButtonWebApp(text="BEKS Lab",web_app=WebAppInfo(url=WEBAPP_URL)))
    except: pass
    log.info(f"OK {url}")

async def on_shutdown(app):
    await bot.delete_webhook(); await bot.session.close()

def main():
    app = web.Application(middlewares=[cors_mw])
    app.on_startup.append(on_startup); app.on_shutdown.append(on_shutdown)
    wh = SimpleRequestHandler(dispatcher=dp, bot=bot); wh.register(app, path=WEBHOOK_PATH)
    setup_application(app, dp, bot=bot)
    app.router.add_get("/", lambda r: web.json_response({"ok":True}))
    app.router.add_get("/api/profile", api_profile)
    app.router.add_post("/api/settings", api_settings)
    app.router.add_post("/api/analyze", api_analyze)
    app.router.add_post("/api/calories", api_calories)
    app.router.add_post("/api/chat", api_chat)
    web.run_app(app, host="0.0.0.0", port=PORT)

if __name__=="__main__": main()    c.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            tg_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT,
            is_pro INTEGER DEFAULT 0, settings TEXT DEFAULT '{}',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    c.commit(); c.close()

def save_user(u):
    c = get_db(); c.execute("INSERT OR IGNORE INTO users(tg_id,username,first_name) VALUES(?,?,?)",(u.id,u.username,u.first_name)); c.commit(); c.close()

def set_pro(tid, v=1):
    c = get_db(); c.execute("INSERT OR IGNORE INTO users(tg_id) VALUES(?)",(tid,)); c.execute("UPDATE users SET is_pro=? WHERE tg_id=?",(v,tid)); c.commit(); c.close()

def is_pro(tid):
    c = get_db(); r = c.execute("SELECT is_pro FROM users WHERE tg_id=?",(tid,)).fetchone(); c.close(); return bool(r and r[0])

def get_settings(tid):
    c = get_db(); r = c.execute("SELECT settings FROM users WHERE tg_id=?",(tid,)).fetchone(); c.close()
    if r and r[0]:
        try: return json.loads(r[0])
        except: pass
    return {}

def save_settings(tid, data):
    c = get_db(); c.execute("INSERT OR IGNORE INTO users(tg_id) VALUES(?)",(tid,)); c.execute("UPDATE users SET settings=? WHERE tg_id=?",(json.dumps(data),tid)); c.commit(); c.close()

def user_count():
    c = get_db(); n = c.execute("SELECT COUNT(*) FROM users").fetchone()[0]; c.close(); return n

def pro_count():
    c = get_db(); n = c.execute("SELECT COUNT(*) FROM users WHERE is_pro=1").fetchone()[0]; c.close(); return n

# ---- Telegram initData ----
def parse_init(init_data):
    try:
        p = parse_qs(init_data); h = p.get("hash",[None])[0]
        if not h: return None
        dc = sorted((k,v[0]) for k,v in p.items() if k!="hash")
        s = "\n".join(f"{k}={v}" for k,v in dc)
        sec = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        if hmac.new(sec, s.encode(), hashlib.sha256).hexdigest() != h: return None
        u = p.get("user",[None])[0]
        return json.loads(u) if u else None
    except: return None

def get_tid(req):
    a = req.headers.get("Authorization","")
    if a:
        u = parse_init(a)
        if u and "id" in u: return u["id"]
    t = req.query.get("tg_id")
    if t:
        try: return int(t)
        except: pass
    return None

# ---- Gemini ----
async def gemini(prompt, img_b64=None, mime="image/jpeg"):
    if not GEMINI_KEY: return "Gemini API ключ не настроен."
    parts = []
    if img_b64: parts.append({"inline_data":{"mime_type":mime,"data":img_b64}})
    parts.append({"text":prompt})
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_KEY}"
    async with aio_client.ClientSession() as s:
        async with s.post(url, json={"contents":[{"parts":parts}]}) as r:
            if r.status != 200: return "Ошибка AI."
            d = await r.json()
            try: return d["candidates"][0]["content"]["parts"][0]["text"]
            except: return "AI не смог обработать."

# ---- CORS ----
CH = {"Access-Control-Allow-Origin":"*","Access-Control-Allow-Methods":"GET,POST,OPTIONS","Access-Control-Allow-Headers":"Content-Type,Authorization"}

@web.middleware
async def cors_mw(req, handler):
    if req.method == "OPTIONS": return web.Response(headers=CH)
    r = await handler(req); r.headers.update(CH); return r

# ---- API ----
async def api_profile(req):
    tid = get_tid(req)
    if not tid: return web.json_response({"error":"no tg_id"},status=400,headers=CH)
    return web.json_response({"tg_id":tid,"is_pro":is_pro(tid),"settings":get_settings(tid)},headers=CH)

async def api_settings(req):
    tid = get_tid(req)
    if not tid: return web.json_response({"error":"no tg_id"},status=400,headers=CH)
    try: body = await req.json()
    except: return web.json_response({"error":"bad json"},status=400,headers=CH)
    save_settings(tid, body)
    return web.json_response({"ok":True},headers=CH)

async def api_analyze(req):
    try: body = await req.json(); img = body.get("image"); mime = body.get("mime","image/jpeg")
    except: return web.json_response({"error":"bad"},status=400,headers=CH)
    if not img: return web.json_response({"error":"no image"},status=400,headers=CH)
    prompt = ("Проанализируй внешность по фото для приложения BEKS Lab. "
        "Дай структурированный разбор по 4 зонам: Кожа, Брови/взгляд, Волосы/причёска, Общий стиль. "
        "Для каждой: что видно (1-2 предложения, прямо, уважительно) и рекомендацию. "
        "В конце — 'Главный приоритет'. Формат: заголовки зон жирным, затем 'Главный приоритет'.")
    r = await gemini(prompt, img, mime)
    return web.json_response({"report":r},headers=CH)

async def api_calories(req):
    try: body = await req.json(); meal = body.get("meal","")
    except: return web.json_response({"error":"bad"},status=400,headers=CH)
    if not meal: return web.json_response({"error":"no meal"},status=400,headers=CH)
    prompt = f'Оцени КБЖУ для порции: "{meal}". Ответь СТРОГО JSON: {{"kcal":число,"protein":число,"fat":число,"carbs":число}}'
    r = await gemini(prompt)
    try:
        clean = r.replace("```json","").replace("```","").strip()
        return web.json_response(json.loads(clean),headers=CH)
    except:
        return web.json_response({"error":"parse","raw":r},status=500,headers=CH)

# ---- Bot ----
async def check_sub(uid):
    if not CHANNEL_ID: return True
    try:
        m = await bot.get_chat_member(CHANNEL_ID, uid)
        if m.status in ("left","kicked"): return False
    except: return True
    if CHAT_ID:
        try:
            m = await bot.get_chat_member(CHAT_ID, uid)
            if m.status in ("left","kicked"): return False
        except: pass
    return True

@dp.message(CommandStart())
async def cmd_start(msg):
    save_user(msg.from_user)
    if not await check_sub(msg.from_user.id):
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="📢 Канал",url=f"https://t.me/{CHANNEL_ID.replace('@','')}"),
            InlineKeyboardButton(text="💬 Чат",url=f"https://t.me/{CHAT_ID.replace('@','')}")
        ],[InlineKeyboardButton(text="✅ Проверить",callback_data="check_sub")]])
        await msg.answer("🔒 Подпишись на канал и чат.",reply_markup=kb,parse_mode="Markdown")
    else:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🚀 Открыть BEKS Lab",web_app=WebAppInfo(url=WEBAPP_URL))]])
        p = "⭐ PRO" if is_pro(msg.from_user.id) else ""
        await msg.answer(f"✅ **BEKS Lab** {p}\n\nНажми кнопку.",reply_markup=kb,parse_mode="Markdown")

@dp.callback_query(F.data=="check_sub")
async def cb_sub(cb):
    if await check_sub(cb.from_user.id):
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🚀 Открыть BEKS Lab",web_app=WebAppInfo(url=WEBAPP_URL))]])
        await cb.message.edit_text("✅ Подписка ОК!",reply_markup=kb)
    else: await cb.answer("Не подписался.",show_alert=True)

@dp.message(Command("myid"))
async def cmd_myid(msg): await msg.answer(f"ID: `{msg.from_user.id}`",parse_mode="Markdown")

@dp.message(Command("pro"))
async def cmd_pro(msg):
    if msg.from_user.id!=ADMIN_ID: return await msg.answer("⛔")
    a=msg.text.split(); tid=int(a[1]) if len(a)>1 else msg.from_user.id
    set_pro(tid,1); await msg.answer(f"✅ PRO → `{tid}`",parse_mode="Markdown")

@dp.message(Command("unpro"))
async def cmd_unpro(msg):
    if msg.from_user.id!=ADMIN_ID: return await msg.answer("⛔")
    a=msg.text.split(); tid=int(a[1]) if len(a)>1 else msg.from_user.id
    set_pro(tid,0); await msg.answer(f"❌ PRO снят `{tid}`",parse_mode="Markdown")

@dp.message(Command("stats"))
async def cmd_stats(msg):
    if msg.from_user.id!=ADMIN_ID: return await msg.answer("⛔")
    await msg.answer(f"📊 Users: {user_count()} | PRO: {pro_count()}",parse_mode="Markdown")

# ---- Start ----
async def on_startup(app):
    init_db(); url=f"https://{WEBHOOK_HOST}{WEBHOOK_PATH}"
    await bot.set_webhook(url)
    try: await bot.set_chat_menu_button(menu_button=MenuButtonWebApp(text="BEKS Lab",web_app=WebAppInfo(url=WEBAPP_URL)))
    except: pass
    log.info(f"OK {url}")

async def on_shutdown(app):
    await bot.delete_webhook(); await bot.session.close()

def main():
    app = web.Application(middlewares=[cors_mw])
    app.on_startup.append(on_startup); app.on_shutdown.append(on_shutdown)
    wh = SimpleRequestHandler(dispatcher=dp, bot=bot); wh.register(app, path=WEBHOOK_PATH)
    setup_application(app, dp, bot=bot)
    app.router.add_get("/", lambda r: web.json_response({"ok":True}))
    app.router.add_get("/api/profile", api_profile)
    app.router.add_post("/api/settings", api_settings)
    app.router.add_post("/api/analyze", api_analyze)
    app.router.add_post("/api/calories", api_calories)
    web.run_app(app, host="0.0.0.0", port=PORT)

if __name__=="__main__": main()
