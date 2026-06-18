"""
BEKS Lab — Telegram Bot + API Server
aiogram 3.x + aiohttp + SQLite + Gemini API
"""
import os, logging, sqlite3, json, hashlib, hmac, asyncio
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
        CREATE TABLE IF NOT EXISTS analyses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tg_id INTEGER NOT NULL,
            report TEXT NOT NULL,
            summary TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS meals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tg_id INTEGER NOT NULL,
            day TEXT NOT NULL,
            name TEXT NOT NULL,
            kcal INTEGER DEFAULT 0,
            protein INTEGER DEFAULT 0,
            fat INTEGER DEFAULT 0,
            carbs INTEGER DEFAULT 0,
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

def save_analysis(tid, report, summary):
    c = get_db()
    c.execute("INSERT OR IGNORE INTO users(tg_id) VALUES(?)",(tid,))
    c.execute("INSERT INTO analyses(tg_id, report, summary) VALUES(?,?,?)",(tid, report, summary))
    c.commit(); c.close()

def get_analyses(tid, limit=20):
    c = get_db()
    rows = c.execute(
        "SELECT id, summary, created_at FROM analyses WHERE tg_id=? ORDER BY id DESC LIMIT ?",
        (tid, limit)
    ).fetchall()
    c.close()
    return [{"id": r["id"], "summary": r["summary"], "created_at": r["created_at"]} for r in rows]

def get_analysis(tid, aid):
    c = get_db()
    r = c.execute("SELECT report, created_at FROM analyses WHERE tg_id=? AND id=?", (tid, aid)).fetchone()
    c.close()
    if not r: return None
    return {"report": r["report"], "created_at": r["created_at"]}

def save_meal(tid, day, name, kcal, protein, fat, carbs):
    c = get_db()
    c.execute("INSERT OR IGNORE INTO users(tg_id) VALUES(?)", (tid,))
    c.execute("INSERT INTO meals(tg_id,day,name,kcal,protein,fat,carbs) VALUES(?,?,?,?,?,?,?)",
              (tid, day, name, kcal, protein, fat, carbs))
    c.commit(); c.close()

def delete_meal(tid, meal_id):
    c = get_db()
    c.execute("DELETE FROM meals WHERE id=? AND tg_id=?", (meal_id, tid))
    c.commit(); c.close()

def get_meals(tid, day):
    c = get_db()
    rows = c.execute(
        "SELECT id, name, kcal, protein, fat, carbs FROM meals WHERE tg_id=? AND day=? ORDER BY id ASC",
        (tid, day)
    ).fetchall()
    c.close()
    return [{"id": r["id"], "name": r["name"], "kcal": r["kcal"],
             "protein": r["protein"], "fat": r["fat"], "carbs": r["carbs"]} for r in rows]

# ---- Guides content (Академия) ----
# Категория "Кожа" — полный набор гайдов. Остальные категории (Волосы, Брови, Стиль,
# Осанка, Сон) пока не заполнены — кнопка "Смотреть" вернёт пустой список с понятным сообщением.
GUIDES_CONTENT = {
    "skin": [
        {
            "id": "skin-1",
            "title": "Базовый уход за кожей: 3 шага",
            "desc": "Очищение, увлажнение, SPF",
            "body": (
                "Основа любого ухода — три шага утром и вечером.\n\n"
                "1. Очищение. Мягкий гель или пенка без сульфатов (SLS/SLES в составе — плохой знак). "
                "Умывайся утром и вечером, не чаще — пересушенная кожа начинает вырабатывать больше себума в ответ.\n\n"
                "2. Увлажнение. Даже жирная кожа нуждается в увлажнении — лёгкий гель-крем с гиалуроновой кислотой "
                "подойдёт почти всем типам. Сухая кожа — берёт крем плотнее, с керамидами.\n\n"
                "3. SPF днём. Это самый важный пункт против старения кожи и пигментации. SPF 30-50, каждый день, "
                "независимо от погоды и сезона — UV-лучи проникают и через облака.\n\n"
                "Главный приоритет: если делаешь только одну вещь — делай SPF. Это единственный шаг, который "
                "реально замедляет старение кожи в долгосрочной перспективе."
            ),
        },
        {
            "id": "skin-2",
            "title": "Как понять свой тип кожи",
            "desc": "Сухая, жирная, комбинированная",
            "body": (
                "Простой тест: умойся, ничего не наноси, подожди 30-40 минут.\n\n"
                "Если кожа стянутая, появляются шелушения — у тебя сухая кожа. Нужны более плотные кремы, "
                "избегай спиртосодержащих тоников.\n\n"
                "Если блестит вся поверхность лица, особенно Т-зона (лоб, нос, подбородок) — жирная кожа. "
                "Подойдут лёгкие гели, матирующие средства, не пересушивай — это усилит выработку себума.\n\n"
                "Если блестит только Т-зона, а щёки нормальные или сухие — комбинированный тип, самый частый. "
                "Можно использовать разные средства для разных зон лица.\n\n"
                "Главный приоритет: не покупай универсальные средства 'для всех типов кожи' — это маркетинг. "
                "Подбирай уход под свой реальный тип."
            ),
        },
        {
            "id": "skin-3",
            "title": "Акне: что реально работает",
            "desc": "Активные компоненты и режим",
            "body": (
                "Акне у мужчин чаще всего связано с избытком себума и закупоркой пор, плюс гормональные колебания.\n\n"
                "Работающие компоненты с доказанной эффективностью: салициловая кислота (BHA) — отшелушивает внутри "
                "пор, бензоилперекись — убивает бактерии, вызывающие воспаление, и адапален (ретиноид, продаётся без "
                "рецепта в низкой концентрации) — нормализует обновление клеток кожи.\n\n"
                "Начинай с одного активного компонента, не смешивай всё сразу — кожа привыкает 2-4 недели, "
                "и резкая нагрузка вызовет раздражение и ухудшение.\n\n"
                "Если акне болезненное, глубокое (узлы, кисты) или не проходит 2-3 месяца при правильном уходе — "
                "это повод идти к дерматологу, а не экспериментировать дальше самостоятельно.\n\n"
                "Главный приоритет: дай каждому новому средству минимум месяц до выводов — кожа реагирует медленно."
            ),
        },
        {
            "id": "skin-4",
            "title": "SPF: какой выбрать и как наносить",
            "desc": "Защита от старения и пигментации",
            "body": (
                "SPF — единственный пункт ухода с доказанным эффектом против фотостарения (морщины, пигментные "
                "пятна от солнца).\n\n"
                "Выбирай SPF 30-50 с защитой от UVA и UVB (на упаковке обычно пишут 'broad spectrum' или 'UVA/UVB'). "
                "Текстура имеет значение — если крем оставляет белый налёт или забивает поры, ищи лёгкие "
                "флюид-формулы или версии 'для лица' отдельно от телесных кремов.\n\n"
                "Наносить нужно за 15-20 минут до выхода на улицу, на чистую кожу, после увлажняющего крема. "
                "Стандартная ошибка — слишком мало крема: для лица нужно примерно с горошину размером в 2 пальца.\n\n"
                "Главный приоритет: SPF нужен каждый день, а не только летом или на пляже — это пожизненная привычка, "
                "а не сезонная мера."
            ),
        },
        {
            "id": "skin-5",
            "title": "Пилинги и ретиноиды: с чего начать",
            "desc": "Обновление кожи без раздражения",
            "body": (
                "Ретиноиды (адапален, ретинол) ускоряют обновление клеток кожи, выравнивают тон, уменьшают акне "
                "и со временем сглаживают морщины — но требуют осторожного старта.\n\n"
                "Начинай с низкой концентрации 2-3 раза в неделю, только вечером (ретиноиды разрушаются от "
                "ультрафиолета и повышают чувствительность кожи к солнцу — SPF днём становится обязательным).\n\n"
                "Первые 2-4 недели возможна 'ретиноидная реакция' — лёгкое покраснение, шелушение. Это нормально, "
                "если не доходит до сильного жжения или боли. Не наноси на влажную кожу сразу после умывания — "
                "подожди 10-15 минут.\n\n"
                "Пилинги с кислотами (AHA/BHA) делают похожую работу мягче и подходят как альтернатива или "
                "дополнение, но не используй пилинг и ретиноид в один день — это перегружает кожу.\n\n"
                "Главный приоритет: терпение. Видимый эффект от ретиноидов проявляется через 2-3 месяца "
                "регулярного использования, не раньше."
            ),
        },
        {
            "id": "skin-6",
            "title": "Диета и кожа",
            "desc": "Что есть, чтобы кожа выглядела лучше",
            "body": (
                "Связь между питанием и состоянием кожи реальна, хотя и не такая прямая, как в мифах.\n\n"
                "Что усиливает воспаления и акне у части людей: избыток быстрых углеводов и сахара (резкие "
                "скачки инсулина стимулируют выработку себума), молочные продукты в больших количествах у "
                "склонных к этому людей.\n\n"
                "Что помогает: достаточное количество воды, омега-3 (рыба, льняное масло) снижают воспаление, "
                "антиоксиданты из овощей и ягод поддерживают регенерацию кожи, цинк (тыквенные семечки, "
                "морепродукты) связан с уменьшением акне у некоторых людей.\n\n"
                "Важно: диета — это поддерживающий фактор, не замена базового ухода (очищение, увлажнение, SPF, "
                "активные компоненты). Не жди, что одна диета решит акне без остального ухода.\n\n"
                "Главный приоритет: убери из рациона явные крайности (фастфуд каждый день, литры сладкой газировки) "
                "и не строй вокруг еды лишнюю тревожность — баланс важнее запретов."
            ),
        },
        {
            "id": "skin-7",
            "title": "Автозагар: безопасно и без полос",
            "desc": "Здоровый тон без солнца",
            "body": (
                "Автозагар — способ получить более тёплый тон кожи без UV-повреждения, в отличие от загара на "
                "солнце или в солярии.\n\n"
                "Перед нанесением: сделай скраб за день до процедуры (не в день нанесения — это раздражит кожу), "
                "удали волосы заранее, средство лучше наносить на чистую, сухую, нежирную кожу.\n\n"
                "Наноси тонким равномерным слоем, особое внимание — суше зоны (брови, линия роста волос, "
                "запястья) дают более тёмный, неравномерный результат, поэтому туда наносят меньше средства или "
                "разбавляют его увлажняющим кремом.\n\n"
                "Не мочи кожу и не потей активно первые 6-8 часов после нанесения — средству нужно время "
                "закрепиться.\n\n"
                "Главный приоритет: автозагар не защищает от солнца — SPF всё равно нужен сверху, это два "
                "разных, не взаимозаменяемых шага."
            ),
        },
        {
            "id": "skin-8",
            "title": "Когда идти к дерматологу",
            "desc": "Когда домашний уход не справляется",
            "body": (
                "Многие проблемы кожи невозможно решить самостоятельно. Дерматолог — это не крайний случай, а "
                "специалист, который сэкономит месяцы неудачных экспериментов с косметикой.\n\n"
                "Когда точно пора записаться на приём: высыпания не проходят более 1-2 месяцев несмотря на "
                "правильный домашний уход, домашний уход и аптечная косметика не дают результата в течение "
                "2-3 месяцев, появляются болезненные глубокие воспаления — узлы и кисты, есть подозрение на "
                "розацеа, экзему или другое хроническое состояние, остаются заметные постакне-рубцы или "
                "стойкая пигментация после прошедших воспалений.\n\n"
                "Дерматолог может назначить рецептурные средства (более сильные ретиноиды, антибиотики при "
                "тяжёлом акне, гормональную диагностику при подозрении на эндокринные причины), которые "
                "недоступны в обычной аптеке без рецепта.\n\n"
                "Главный приоритет: обратиться к дерматологу можно с любой формой акне, если она тебя "
                "беспокоит — не нужно дожидаться 'достаточно серьёзной' проблемы."
            ),
        },
    ],
}

GUIDES_META = {
    "skin": {"title": "Кожа", "desc": "Тон, акне, SPF"},
    "hair": {"title": "Волосы", "desc": "Стрижка и укладка"},
    "brows": {"title": "Брови", "desc": "Форма и плотность"},
    "style": {"title": "Стиль", "desc": "Одежда и образ"},
    "posture": {"title": "Осанка", "desc": "Привычки и упражнения"},
    "sleep": {"title": "Сон и восстановление", "desc": "Влияние на внешний вид"},
}

async def api_guides_list(req):
    cat = req.query.get("category", "")
    if cat not in GUIDES_META:
        return web.json_response({"error": "unknown category"}, status=400, headers=CH)
    guides = GUIDES_CONTENT.get(cat, [])
    items = [{"id": g["id"], "title": g["title"], "desc": g["desc"]} for g in guides]
    return web.json_response({"category": cat, "meta": GUIDES_META[cat], "guides": items}, headers=CH)

async def api_guide_detail(req):
    tid = get_tid(req)
    gid = req.query.get("id", "")
    for cat_guides in GUIDES_CONTENT.values():
        for g in cat_guides:
            if g["id"] == gid:
                if not tid or not is_pro(tid):
                    return web.json_response({"error": "PRO required", "locked": True}, status=403, headers=CH)
                return web.json_response({"id": g["id"], "title": g["title"], "body": g["body"]}, headers=CH)
    return web.json_response({"error": "not found"}, status=404, headers=CH)

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
GEMINI_MODEL = "gemini-2.5-flash"

async def gemini(prompt, img_b64=None, mime="image/jpeg", max_retries=3):
    if not GEMINI_KEY:
        log.error("GEMINI_KEY не задан в переменных окружения")
        return "Gemini API ключ не настроен."
    parts = []
    if img_b64: parts.append({"inline_data":{"mime_type":mime,"data":img_b64}})
    parts.append({"text":prompt})
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_KEY}"

    last_error = "Ошибка AI."
    for attempt in range(max_retries):
        try:
            async with aio_client.ClientSession() as s:
                async with s.post(url, json={"contents":[{"parts":parts}]}) as r:
                    body_text = await r.text()

                    if r.status == 429:
                        log.warning(f"Gemini 429 (попытка {attempt+1}/{max_retries}): {body_text[:300]}")
                        last_error = f"Ошибка AI (HTTP 429): {body_text[:300]}"
                        if attempt < max_retries - 1:
                            wait = 2 * (attempt + 1)  # 2с, 4с, 6с
                            await asyncio.sleep(wait)
                            continue
                        return "Сервис AI сейчас перегружен (лимит запросов). Подожди немного и попробуй снова."

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
            log.error(f"Gemini: исключение при запросе (попытка {attempt+1}): {repr(e)}")
            last_error = f"Ошибка сети при обращении к AI: {repr(e)[:200]}"
            if attempt < max_retries - 1:
                await asyncio.sleep(2 * (attempt + 1))
                continue

    return last_error

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
    tid = get_tid(req)
    try:
        body = await req.json()
        img = body.get("image")
        mime = body.get("mime","image/jpeg")
    except Exception as e:
        log.error(f"api_analyze: не удалось разобрать тело запроса: {repr(e)}")
        return web.json_response({"error": f"bad request body: {repr(e)[:200]}"}, status=400, headers=CH)
    if not img: return web.json_response({"error":"no image"},status=400,headers=CH)
    prompt = ("Проанализируй внешность по фото для приложения BEKS Lab. "
        "Дай структурированный разбор по 4 зонам: Кожа, Брови/взгляд, Волосы/причёска, Общий стиль. "
        "Для каждой: что видно (1-2 предложения, прямо, уважительно) и рекомендацию. "
        "В конце — 'Главный приоритет'. Формат: заголовки зон жирным, затем 'Главный приоритет'.")
    r = await gemini(prompt, img, mime)
    if tid and r and not r.startswith("Ошибка") and not r.startswith("Сервис AI") and not r.startswith("AI "):
        summary_prompt = f"Сократи этот текст до одной короткой фразы (до 8 слов) — главный итог отчёта:\n\n{r}"
        summary = await gemini(summary_prompt)
        if summary.startswith("Ошибка") or summary.startswith("Сервис AI"):
            summary = "Анализ внешности"
        save_analysis(tid, r, summary.strip()[:150])
    return web.json_response({"report":r},headers=CH)

async def api_analyses_list(req):
    tid = get_tid(req)
    if not tid: return web.json_response({"error":"no tg_id"},status=400,headers=CH)
    return web.json_response({"analyses": get_analyses(tid)}, headers=CH)

async def api_analysis_detail(req):
    tid = get_tid(req)
    if not tid: return web.json_response({"error":"no tg_id"},status=400,headers=CH)
    aid = req.query.get("id", "")
    try: aid = int(aid)
    except: return web.json_response({"error":"bad id"},status=400,headers=CH)
    a = get_analysis(tid, aid)
    if not a: return web.json_response({"error":"not found"},status=404,headers=CH)
    return web.json_response(a, headers=CH)

async def api_calories(req):
    tid = get_tid(req)
    try: body = await req.json(); meal = body.get("meal","")
    except: return web.json_response({"error":"bad"},status=400,headers=CH)
    if not meal: return web.json_response({"error":"no meal"},status=400,headers=CH)
    prompt = f'Оцени КБЖУ для порции: "{meal}". Ответь СТРОГО JSON: {{"kcal":число,"protein":число,"fat":число,"carbs":число}}'
    r = await gemini(prompt)
    try:
        clean = r.replace("```json","").replace("```","").strip()
        data = json.loads(clean)
        if tid:
            from datetime import date
            day = str(date.today())
            save_meal(tid, day, meal, data.get("kcal",0), data.get("protein",0), data.get("fat",0), data.get("carbs",0))
        return web.json_response(data, headers=CH)
    except:
        return web.json_response({"error":"parse","raw":r},status=500,headers=CH)

async def api_meals_get(req):
    tid = get_tid(req)
    if not tid: return web.json_response({"error":"no tg_id"},status=400,headers=CH)
    from datetime import date
    day = req.query.get("day", str(date.today()))
    meals = get_meals(tid, day)
    return web.json_response({"meals": meals, "day": day}, headers=CH)

async def api_meal_delete(req):
    tid = get_tid(req)
    if not tid: return web.json_response({"error":"no tg_id"},status=400,headers=CH)
    try:
        body = await req.json()
        meal_id = int(body.get("id", 0))
    except: return web.json_response({"error":"bad"},status=400,headers=CH)
    delete_meal(tid, meal_id)
    return web.json_response({"ok": True}, headers=CH)

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
        "ТВОЯ ЕДИНСТВЕННАЯ ТЕМА: внешность, уход за кожей/волосами/бровями, стиль и одежда, осанка, сон и "
        "восстановление, питание в контексте внешнего вида. Если пользователь просит решить домашнее задание, "
        "написать код, помочь с учёбой, обсудить новости, политику, отношения не по теме внешности или любую "
        "другую тему, не связанную с уходом за собой и внешностью — вежливо откажись и верни разговор к теме "
        "приложения. Не извиняйся многословно, просто короткое 'Это не по моей части, я помогаю только с "
        "внешностью и уходом за собой' и предложи задать вопрос по теме.\n\n"
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
    app = web.Application(middlewares=[cors_mw], client_max_size=20*1024*1024)  # 20MB — достаточно для фото в base64
    app.on_startup.append(on_startup); app.on_shutdown.append(on_shutdown)
    wh = SimpleRequestHandler(dispatcher=dp, bot=bot); wh.register(app, path=WEBHOOK_PATH)
    setup_application(app, dp, bot=bot)
    app.router.add_get("/", lambda r: web.json_response({"ok":True}))
    app.router.add_get("/api/profile", api_profile)
    app.router.add_post("/api/settings", api_settings)
    app.router.add_post("/api/analyze", api_analyze)
    app.router.add_post("/api/calories", api_calories)
    app.router.add_post("/api/chat", api_chat)
    app.router.add_get("/api/analyses", api_analyses_list)
    app.router.add_get("/api/analysis", api_analysis_detail)
    app.router.add_get("/api/guides", api_guides_list)
    app.router.add_get("/api/guide", api_guide_detail)
    app.router.add_get("/api/meals", api_meals_get)
    app.router.add_delete("/api/meal", api_meal_delete)
    web.run_app(app, host="0.0.0.0", port=PORT)

if __name__=="__main__": main()
