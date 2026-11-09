import io
import gzip
import os
import re
import json
import logging
import asyncio
import calendar
from datetime import date as dt_date, datetime, timedelta, time as dtime, timezone as dt_timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from dotenv import load_dotenv
from groq import AsyncGroq
from finance_agent import looks_like_finance_entry, looks_like_finance_question, parse_finance_response
from project_agent import looks_like_project_intent, parse_project_response
from supabase import create_client, Client

from telegram import (
    Update,
    BotCommand,
    ReplyKeyboardMarkup,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_USER_ID_RAW = os.getenv("TELEGRAM_USER_ID")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_MODEL = os.getenv("GROQ_MODEL")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
# Токен Todoist необязателен: без него бот работает, просто не видит задачи.
TODOIST_TOKEN = os.getenv("TODOIST_API_TOKEN")

if not TELEGRAM_TOKEN or not GROQ_API_KEY or not GROQ_MODEL:
    raise RuntimeError("Не заполнены TELEGRAM_BOT_TOKEN, GROQ_API_KEY или GROQ_MODEL в .env")

if not TELEGRAM_USER_ID_RAW:
    raise RuntimeError("Не задан TELEGRAM_USER_ID в .env")

try:
    ALLOWED_TELEGRAM_USER_ID = int(TELEGRAM_USER_ID_RAW)
except ValueError:
    raise RuntimeError("TELEGRAM_USER_ID должен быть числом")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("Не заполнены SUPABASE_URL или SUPABASE_KEY в .env")

ai = AsyncGroq(api_key=GROQ_API_KEY)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

SYSTEM_PROMPT = """
Ты — личный ИИ-агент пользователя.

Твоя задача — помогать человеку управлять своей жизнью,
целями, привычками, работой, финансами и личными проектами.

Твой характер:
- Честный и требовательный.
- Не поддакивай ради вежливости.
- Не оправдывай постоянное откладывание дел.
- Не унижай и не оскорбляй пользователя.
- Замечай противоречия между словами и действиями.
- Помогай находить реальные причины проблем.
- Предлагай конкретные и выполнимые действия.

Ты пока находишься в тестовом режиме.
Не выдумывай сведения о пользователе.
Если информации недостаточно — задавай вопросы.
Отвечай на русском языке.
"""

VALID_CATEGORIES = {
    "goal", "project", "habit", "preference",
    "commitment", "fact", "observation",
}

CATEGORY_NAMES = {
    "goal": "Цель",
    "project": "Проект",
    "habit": "Привычка",
    "preference": "Предпочтение",
    "commitment": "Обязательство",
    "fact": "Факт",
    "observation": "Наблюдение",
}

EXTRACT_PROMPT = """
Ты — модуль памяти личного ИИ-агента.
Тебе дают новое сообщение пользователя и список уже сохранённых записей.
Выдели из сообщения только полезную и относительно устойчивую информацию:
- goal — цель пользователя
- project — проект, который он начал или ведёт
- habit — регулярная привычка
- preference — предпочтение
- commitment — обязательство или договорённость
- fact — важный постоянный факт о пользователе
- observation — устойчивая закономерность в поведении

Правила:
- Не сохраняй случайные вопросы, шутки и разовые мелочи.
- Не сохраняй план на один день и текущие дела: это не память, а дневной отчёт.
- Не сохраняй пароли, токены и API-ключи.
- Не повторяй то, что уже сохранено.
- Не выдумывай: сохраняй только то, что пользователь сказал сам.
- Пиши запись коротко, от третьего лица (например: «Хочет закончить этап 3 до пятницы»).
- importance — число от 1 до 5 (5 — самое важное).

Верни ТОЛЬКО JSON, без пояснений, строго в таком виде:
{"memories": [{"category": "goal", "content": "...", "importance": 3}]}
Если сохранять нечего, верни: {"memories": []}
"""

# ===================== ЕЖЕДНЕВНЫЕ ОПРОСЫ: ТЕКСТЫ =====================

DEFAULT_TIMEZONE = "Europe/Moscow"
DEFAULT_TIMES = {"morning": "07:30", "noon": "13:00", "evening": "20:00", "weekly": "20:30"}
SURVEY_ORDER = ["morning", "noon", "evening"]
SURVEY_WINDOW_HOURS = 2  # сколько часов после вопроса твои сообщения считаются ответом на опрос

SURVEYS = {
    "morning": {
        "title": "Утренний опрос",
        "icon": "☀️",
        "question": (
            "☀️ Доброе утро! Планируем день.\n\n"
            "1. Как спал и как себя чувствуешь?\n"
            "2. Какие три задачи сегодня самые важные?\n"
            "3. Что обязательно нужно сделать?\n"
            "4. Есть ли препятствия, которые могут помешать?\n\n"
            "Можно ответить одним сообщением."
        ),
        "instruction": (
            "Сейчас идёт УТРЕННИЙ опрос: пользователь планирует день. "
            "Если данных достаточно, составь краткий план дня: до трёх главных задач, "
            "обязательное дело и как обойти названные препятствия. "
            "Если чего-то важного не хватает, задай один уточняющий вопрос. "
            "Не придумывай задачи, которых пользователь не называл."
        ),
    },
    "noon": {
        "title": "Дневной опрос",
        "icon": "🌤",
        "question": (
            "🌤 Проверка по ходу дня.\n\n"
            "1. Что уже выполнено?\n"
            "2. Что осталось?\n"
            "3. Что пошло не по плану?\n"
            "4. Нужно ли поменять приоритеты?\n\n"
            "Можно ответить одним сообщением."
        ),
        "instruction": (
            "Сейчас идёт ДНЕВНОЙ опрос: проверка промежуточного прогресса. "
            "Сравни то, что пользователь сделал, с утренним планом (он ниже, если есть). "
            "Честно скажи, что идёт по плану, а что отстаёт, и предложи, что изменить в приоритетах. "
            "Не считай задачу выполненной, пока пользователь сам этого не сказал."
        ),
    },
    "evening": {
        "title": "Вечерний опрос",
        "icon": "🌙",
        "question": (
            "🌙 Подводим итоги дня.\n\n"
            "1. Что удалось сделать?\n"
            "2. Что не получилось и почему?\n"
            "3. Что было главным достижением?\n"
            "4. Что стоит изменить завтра?\n\n"
            "Можно ответить одним сообщением."
        ),
        "instruction": (
            "Сейчас идёт ВЕЧЕРНИЙ опрос: подведение итогов дня. "
            "Сравни итог с утренним планом и дневной проверкой (они ниже, если есть). "
            "Дай краткий отчёт: что сделано, что нет, почему. Выдели 2–3 честных вывода "
            "и одну конкретную вещь, которую стоит изменить завтра. "
            "Не считай задачу выполненной без слов пользователя и не выдумывай причины."
        ),
    },
}

# ===================== МЕНЮ ВНИЗУ =====================

BTN_MEMORY = "🧠 Память"
BTN_STATS = "📊 Статистика"
BTN_TASKS = "✅ Задачи"
BTN_REPORT = "📈 Отчёты"
BTN_SCHEDULE = "⏰ Расписание"
BTN_STATUS = "📋 Статус"
BTN_HELP = "❓ Помощь"
BTN_FINANCE = "💰 Финансы"
BTN_PROJECTS = "📁 Проекты"

MENU_BUTTONS = (
    BTN_TASKS, BTN_REPORT, BTN_FINANCE, BTN_PROJECTS, BTN_MEMORY, BTN_STATS, BTN_SCHEDULE, BTN_STATUS, BTN_HELP,
)

MAIN_MENU = ReplyKeyboardMarkup(
    [
        [BTN_TASKS, BTN_REPORT, BTN_FINANCE],
        [BTN_PROJECTS, BTN_MEMORY, BTN_STATS],
        [BTN_SCHEDULE, BTN_STATUS, BTN_HELP],
    ],
    resize_keyboard=True,
    is_persistent=True,
    input_field_placeholder="Напиши сообщение или выбери кнопку",
)

HELP_TEXT = (
    "Что я умею:\n\n"
    "Нажми кнопку внизу, и в чате появятся кнопки действий. Команды тоже работают, вот они.\n\n"
    "Просто пиши мне — я отвечу и запомню важное.\n"
    "Три раза в день я сам пишу тебе с вопросами: утром, днём и вечером. "
    "Отвечай на них обычным сообщением.\n"
    "По воскресеньям вечером я присылаю недельный отчёт.\n"
    "Если подключён Todoist, я вижу твои задачи и сверяю с ними план дня.\n\n"
    "Кнопки внизу:\n"
    f"{BTN_TASKS} — задачи из Todoist: просроченные, на сегодня, выполненные\n"
    f"{BTN_REPORT} — отчёт за день, неделю или месяц\n"
    f"{BTN_MEMORY} — что я помню о тебе\n"
    f"{BTN_STATS} — сколько данных в таблицах\n"
    f"{BTN_SCHEDULE} — когда приходят опросы и отчёт\n"
    f"{BTN_STATUS} — состояние бота\n"
    f"{BTN_FINANCE} — доходы и расходы за месяц\n"
    f"{BTN_PROJECTS} — список проектов и их прогресс\n\n"
    "Финансы (пиши сумму цифрами без пробелов):\n"
    "/expense 350 еда обед — записать расход\n"
    "/income 50000 зарплата — записать доход\n"
    "/finance или /finance week — итоги за месяц или неделю\n"
    "/fin_list — последние записи, /fin_del номер — удалить\n"
    "/payment 15000 5 Аренда — платёж каждый месяц 5-го числа\n"
    "/payments — список платежей, /payment_del номер — удалить\n"
    "/buy 80000 Ноутбук — покупка в планах\n"
    "/buys — список покупок; /buy_check, /buy_done, /buy_del с номером\n"
    "Можно написать обычной фразой «потратил 500 рублей на продукты»: я покажу черновик и запишу только после подтверждения.\n"
    "Можно спросить «Сколько потратил на машину в этом месяце?» — отвечу по записям Supabase.\n"
    "/cancel — отменить ожидающее действие или черновик.\n\n"
    "Проекты:\n"
    "/project_new название — создать проект\n"
    "/projects — список, /project 3 — карточка проекта №3\n"
    "/project_set 3 goal текст — цель (ещё: desc, status, deadline, result, title)\n"
    "/stage 3 текст, /task 3 текст, /note 3 текст — этап, задача, заметка (срок: /task 3 15.12 текст)\n"
    "/p_done номер, /p_undo номер, /p_del номер — отметить, вернуть, удалить\n"
    "/project_plan 3 — ИИ предложит этапы, ты решишь, сохранять ли\n"
    "/project_del 3 да — удалить проект\n"
    "/asset 3 — прикрепить следующее фото или документ; /assets 3 — открыть материалы\n"
    "Можно обычным текстом попросить добавить этап, задачу или заметку, создать проект или показать прогресс. Запись — только после подтверждения.\n"
    "/deadlines — ближайшие сроки проектов и платежей\n"
    "/backup — резервная копия всех данных файлом (по воскресеньям приходит сама)\n\n"
    "Команды:\n"
    "/tasks — задачи из Todoist\n"
    "/day, /week, /month — отчёт сразу\n"
    "/times 07:30 13:00 20:00 20:30 — время опросов и недельного отчёта\n"
    "/timezone Europe/Moscow — часовой пояс\n"
    "/surveys on или /surveys off — включить или выключить опросы и отчёты\n"
    "/survey morning — проверить опрос прямо сейчас (morning, noon, evening)\n"
    "/forget номер — удалить запись памяти\n"
    "/reset — удалить историю диалога (память, опросы и отчёты останутся)"
)


# ===================== ДОСТУП И БАЗА =====================

def is_authorized(update: Update) -> bool:
    """Разрешаем работу только владельцу бота."""
    user = update.effective_user
    return user is not None and user.id == ALLOWED_TELEGRAM_USER_ID


async def reject_unauthorized(update: Update) -> None:
    """Не раскрываем посторонним никаких сведений о боте."""
    if update.effective_message:
        await update.effective_message.reply_text("Доступ запрещён.")


async def db_call(method, *args, **kwargs):
    """Выполняем синхронный вызов Supabase вне event loop Telegram."""
    return await asyncio.to_thread(lambda: method(*args, **kwargs))


async def ensure_profile(telegram_id: int, name: str | None = None) -> None:
    row = {"telegram_id": telegram_id}
    if name:
        row["name"] = name

    await db_call(
        supabase.table("profiles").upsert(row, on_conflict="telegram_id").execute
    )


async def load_history(telegram_id: int, limit: int = 12) -> list[dict]:
    result = await db_call(
        supabase.table("conversations")
        .select("role, content")
        .eq("telegram_id", telegram_id)
        .order("created_at", desc=True)
        .limit(limit)
        .execute
    )

    # Запрос идёт от новых сообщений к старым, модели нужен обратный порядок.
    rows = list(reversed(result.data or []))
    return [
        {"role": row["role"], "content": row["content"]}
        for row in rows
        if row.get("role") in ("user", "assistant") and row.get("content")
    ]


async def save_message(telegram_id: int, role: str, content: str) -> None:
    await db_call(
        supabase.table("conversations")
        .insert({
            "telegram_id": telegram_id,
            "role": role,
            "content": content,
        })
        .execute
    )


async def clear_history(telegram_id: int) -> None:
    # Удаляет только переписку. Память (memories) и опросы (daily_surveys) остаются.
    await db_call(
        supabase.table("conversations")
        .delete()
        .eq("telegram_id", telegram_id)
        .execute
    )


# ===================== ДОЛГОВРЕМЕННАЯ ПАМЯТЬ =====================

async def load_memories(telegram_id: int, limit: int = 50) -> list[dict]:
    result = await db_call(
        supabase.table("memories")
        .select("id, category, content, importance")
        .eq("telegram_id", telegram_id)
        .eq("status", "active")
        .order("importance", desc=True)
        .order("created_at", desc=True)
        .limit(limit)
        .execute
    )
    return result.data or []


async def save_memory(
    telegram_id: int,
    category: str,
    content: str,
    importance: int,
    source_message: str,
) -> None:
    await db_call(
        supabase.table("memories")
        .insert({
            "telegram_id": telegram_id,
            "category": category,
            "content": content,
            "importance": importance,
            "source_message": source_message,
        })
        .execute
    )


async def delete_memory(telegram_id: int, memory_id: int) -> bool:
    result = await db_call(
        supabase.table("memories")
        .delete()
        .eq("id", memory_id)
        .eq("telegram_id", telegram_id)
        .execute
    )
    return bool(result.data)


def build_memory_block(memories: list[dict]) -> str:
    """Превращаем записи памяти в текст для системного промпта."""
    if not memories:
        return ""
    lines = [
        f"- [{CATEGORY_NAMES.get(m['category'], m['category'])}] {m['content']}"
        for m in memories
    ]
    return (
        "\n\nЧто ты знаешь о пользователе (долговременная память):\n"
        + "\n".join(lines)
        + "\n\nУчитывай эти сведения в ответах, но не пересказывай их без нужды. "
        "Не придумывай того, чего здесь нет."
    )


def looks_like_secret(text: str) -> bool:
    lowered = text.lower()
    markers = (
        "api_key", "api key", "apikey", "token", "токен",
        "пароль", "password", "gsk_", "sk-",
    )
    return any(marker in lowered for marker in markers)


async def extract_and_save_memories(user_id: int, message: str) -> int:
    """Просим модель найти в сообщении важное и сохраняем в базу.
    Возвращаем, сколько записей добавлено."""
    if len(message) < 20 or looks_like_secret(message):
        return 0
    # Не тратим отдельный вызов Groq на вопросы, команды и обычные короткие реплики.
    memory_signals = re.compile(
        r"(я работаю|я живу|мне нравится|я не люблю|моя цель|моя мечта|"
        r"мой проект|моя работа|моя должност|мне важно|я хочу|я планирую|"
        r"у меня есть|запомни|в дальнейшем|обычно я|мне удобнее|"
        r"предпочитаю|мой график|моя привычк|я занимаюсь)",
        re.IGNORECASE,
    )
    if not memory_signals.search(message):
        return 0

    try:
        existing = await load_memories(user_id, limit=100)
        existing_text = "\n".join(f"- {m['content']}" for m in existing) or "(пока пусто)"
        existing_lower = {m["content"].strip().lower() for m in existing}

        response = await ai.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": EXTRACT_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"Уже сохранено:\n{existing_text}\n\n"
                        f"Новое сообщение пользователя:\n{message}"
                    ),
                },
            ],
            temperature=0,
            max_tokens=1500,
            response_format={"type": "json_object"},
        )

        raw = (response.choices[0].message.content or "").strip()
        start, end = raw.find("{"), raw.rfind("}")
        if start == -1 or end == -1:
            return 0

        data = json.loads(raw[start:end + 1])
        saved = 0

        for item in data.get("memories", []):
            category = item.get("category")
            content = (item.get("content") or "").strip()
            importance = item.get("importance", 3)

            if category not in VALID_CATEGORIES or not content:
                continue
            if content.lower() in existing_lower:
                continue
            if not isinstance(importance, int) or not 1 <= importance <= 5:
                importance = 3

            await save_memory(user_id, category, content, importance, message)
            existing_lower.add(content.lower())
            saved += 1

        return saved

    except Exception:
        # Ошибка памяти не должна ломать общение.
        logging.exception("Не удалось извлечь или сохранить память")
        return 0


# ===================== НАСТРОЙКИ РАСПИСАНИЯ =====================

TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


def normalize_time(text: str) -> str | None:
    """'7:30' -> '07:30'. Если формат неверный — None."""
    match = TIME_RE.match(text.strip())
    if not match:
        return None
    return f"{int(match.group(1)):02d}:{match.group(2)}"


async def load_settings(telegram_id: int) -> dict:
    result = await db_call(
        supabase.table("profiles")
        .select("timezone, morning_time, noon_time, evening_time, weekly_time, surveys_enabled")
        .eq("telegram_id", telegram_id)
        .limit(1)
        .execute
    )
    row = (result.data or [{}])[0]
    enabled = row.get("surveys_enabled")
    return {
        "timezone": row.get("timezone") or DEFAULT_TIMEZONE,
        "morning": row.get("morning_time") or DEFAULT_TIMES["morning"],
        "noon": row.get("noon_time") or DEFAULT_TIMES["noon"],
        "evening": row.get("evening_time") or DEFAULT_TIMES["evening"],
        "weekly": row.get("weekly_time") or DEFAULT_TIMES["weekly"],
        "enabled": True if enabled is None else bool(enabled),
    }


async def update_settings(telegram_id: int, **fields) -> None:
    await ensure_profile(telegram_id)
    await db_call(
        supabase.table("profiles")
        .update(fields)
        .eq("telegram_id", telegram_id)
        .execute
    )


def schedule_jobs(app: Application, settings: dict) -> None:
    """(Пере)создаём ежедневные задачи. Старые удаляем, новые ставим по настройкам."""
    if app.job_queue is None:
        logging.warning(
            "JobQueue недоступен: установи  pip install \"python-telegram-bot[job-queue]\""
        )
        return

    for name in [f"survey_{k}" for k in SURVEY_ORDER] + ["report_week", "deadlines"]:
        for job in app.job_queue.get_jobs_by_name(name):
            job.schedule_removal()

    if not settings["enabled"]:
        logging.info("Ежедневные опросы выключены")
        return

    tz = ZoneInfo(settings["timezone"])
    for kind in SURVEY_ORDER:
        hh, mm = settings[kind].split(":")
        app.job_queue.run_daily(
            send_survey_job,
            time=dtime(int(hh), int(mm), tzinfo=tz),
            name=f"survey_{kind}",
            data=kind,
        )
        logging.info("Опрос %s назначен на %s (%s)", kind, settings[kind], settings["timezone"])

    # Недельный отчёт: задача идёт каждый день, а по воскресеньям отправляет отчёт.
    hh, mm = settings["weekly"].split(":")
    app.job_queue.run_daily(
        weekly_report_job,
        time=dtime(int(hh), int(mm), tzinfo=tz),
        name="report_week",
    )
    logging.info("Недельный отчёт назначен на воскресенье %s", settings["weekly"])

    # Напоминания о сроках: каждое утро через 10 минут после утреннего опроса.
    mh, mm = settings["morning"].split(":")
    total = (int(mh) * 60 + int(mm) + 10) % 1440
    app.job_queue.run_daily(
        deadline_reminders_job,
        time=dtime(total // 60, total % 60, tzinfo=tz),
        name="deadlines",
    )
    logging.info("Напоминания о сроках назначены на %02d:%02d", total // 60, total % 60)


# ===================== ОПРОСЫ: БАЗА И ЛОГИКА =====================

def clip(text: str | None, limit: int = 1500) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit] + "…"


async def create_survey(telegram_id: int, kind: str, question: str, local_date: str) -> None:
    await db_call(
        supabase.table("daily_surveys")
        .insert({
            "telegram_id": telegram_id,
            "survey_date": local_date,
            "kind": kind,
            "question": question,
        })
        .execute
    )


async def get_open_survey(telegram_id: int) -> dict | None:
    """Последний опрос, который был отправлен не раньше SURVEY_WINDOW_HOURS часов назад."""
    cutoff = (
        datetime.now(dt_timezone.utc) - timedelta(hours=SURVEY_WINDOW_HOURS)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")

    result = await db_call(
        supabase.table("daily_surveys")
        .select("*")
        .eq("telegram_id", telegram_id)
        .gte("created_at", cutoff)
        .order("created_at", desc=True)
        .limit(1)
        .execute
    )
    rows = result.data or []
    return rows[0] if rows else None


async def load_day_surveys(telegram_id: int, survey_date: str) -> list[dict]:
    result = await db_call(
        supabase.table("daily_surveys")
        .select("kind, answer, summary, created_at")
        .eq("telegram_id", telegram_id)
        .eq("survey_date", survey_date)
        .order("created_at", desc=False)
        .execute
    )
    return result.data or []


async def record_survey_answer(survey: dict, user_message: str, ai_answer: str) -> None:
    """Дописываем сообщение пользователя к ответу на опрос и сохраняем вывод ИИ."""
    old_answer = survey.get("answer") or ""
    new_answer = (old_answer + "\n" + user_message).strip()
    await db_call(
        supabase.table("daily_surveys")
        .update({
            "answer": new_answer,
            "summary": ai_answer,
            "answered_at": datetime.now(dt_timezone.utc).isoformat(),
        })
        .eq("id", survey["id"])
        .execute
    )


async def build_survey_context(user_id: int, survey: dict) -> str:
    """Текст, который добавляем в системный промпт, пока идёт опрос."""
    kind = survey["kind"]
    text = "\n\n" + SURVEYS[kind]["instruction"]

    try:
        rows = await load_day_surveys(user_id, survey["survey_date"])
    except Exception:
        logging.exception("Не удалось загрузить опросы за день")
        rows = []

    current_index = SURVEY_ORDER.index(kind)
    earlier = [
        r for r in rows
        if r["kind"] in SURVEY_ORDER
        and SURVEY_ORDER.index(r["kind"]) < current_index
        and r.get("answer")
    ]

    if earlier:
        parts = []
        for r in earlier:
            parts.append(
                f"{SURVEYS[r['kind']]['title']}:\n"
                f"Ответ пользователя: {clip(r['answer'])}\n"
                f"Твой вывод тогда: {clip(r.get('summary')) or '—'}"
            )
        text += "\n\nЗаписи сегодняшнего дня:\n" + "\n\n".join(parts)
    elif kind in ("noon", "evening"):
        text += "\n\nУтреннего плана на сегодня в записях нет, так и скажи, если это важно."

    todoist_text = await todoist_context_for_ai(user_id)
    if todoist_text:
        text += "\n\n" + todoist_text

    return text


async def deliver_survey(bot, kind: str) -> None:
    """Отправляем вопрос и заводим запись об опросе."""
    settings = await load_settings(ALLOWED_TELEGRAM_USER_ID)
    local_date = datetime.now(ZoneInfo(settings["timezone"])).date().isoformat()
    question = SURVEYS[kind]["question"]

    await bot.send_message(
        chat_id=ALLOWED_TELEGRAM_USER_ID,
        text=question,
        reply_markup=MAIN_MENU,
    )
    await create_survey(ALLOWED_TELEGRAM_USER_ID, kind, question, local_date)
    # Кладём вопрос и в историю переписки, чтобы модель видела, на что отвечает пользователь.
    await save_message(ALLOWED_TELEGRAM_USER_ID, "assistant", question)


async def send_survey_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Эту функцию по расписанию запускает JobQueue."""
    kind = context.job.data
    try:
        await deliver_survey(context.bot, kind)
    except Exception:
        logging.exception("Не удалось отправить опрос %s", kind)


# ===================== TODOIST (пока только чтение) =====================

TODOIST_BASE = "https://api.todoist.com/api/v1"
TODOIST_CACHE_SECONDS = 60
_todoist_cache: dict = {}


def todoist_enabled() -> bool:
    return bool(TODOIST_TOKEN)


async def todoist_request(method: str, path: str, params: dict | None = None,
                          payload: dict | None = None):
    """Единая точка доступа к Todoist API v1."""
    if not TODOIST_TOKEN:
        raise RuntimeError("TODOIST_API_TOKEN не задан в .env")
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.request(
            method,
            f"{TODOIST_BASE}/{path.lstrip('/')}",
            params=params,
            json=payload,
            headers={"Authorization": f"Bearer {TODOIST_TOKEN}"},
        )
        response.raise_for_status()
        if response.status_code == 204 or not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            return None


async def todoist_get(path: str, params: dict | None = None):
    """GET-запрос к Todoist; оставлен для совместимости со старым кодом."""
    return await todoist_request("GET", path, params=params)


async def todoist_post(path: str, payload: dict | None = None):
    """POST-запрос к Todoist API v1."""
    return await todoist_request("POST", path, payload=payload)


async def todoist_paged(path: str, params: dict, max_pages: int = 5) -> list[dict]:
    """Todoist отдаёт данные страницами. Собираем несколько страниц в один список."""
    items: list[dict] = []
    cursor = None
    for _ in range(max_pages):
        page_params = {**params, "limit": 100}
        if cursor:
            page_params["cursor"] = cursor
        data = await todoist_get(path, page_params)
        if isinstance(data, list):
            items += data
            break
        items += data.get("results") or data.get("items") or []
        cursor = data.get("next_cursor")
        if not cursor:
            break
    return items



async def todoist_active_tasks(fresh: bool = False) -> list[dict]:
    """Кэшируем общий список активных задач на тот же короткий интервал."""
    now_ts = datetime.now().timestamp()
    cached = _todoist_cache.get("active_tasks")
    if cached and not fresh and now_ts - cached[0] < TODOIST_CACHE_SECONDS:
        return cached[1]
    tasks = await todoist_paged("tasks", {}, max_pages=2)
    tasks = tasks[:150]
    _todoist_cache["active_tasks"] = (now_ts, tasks)
    return tasks


def to_utc_str(moment: datetime) -> str:
    return moment.astimezone(dt_timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


async def todoist_completed(start_local: datetime, end_local: datetime) -> list[dict]:
    return await todoist_paged(
        "tasks/completed/by_completion_date",
        {"since": to_utc_str(start_local), "until": to_utc_str(end_local)},
    )


async def todoist_snapshot(tz, fresh: bool = False) -> dict:
    """Состояние задач на сейчас: просроченные, на сегодня, выполненные сегодня."""
    now_ts = datetime.now().timestamp()
    cached = _todoist_cache.get("snapshot")
    if cached and not fresh and now_ts - cached[0] < TODOIST_CACHE_SECONDS:
        return cached[1]

    now_local = datetime.now(tz)
    start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)

    overdue, today, done = await asyncio.gather(
        todoist_paged("tasks/filter", {"query": "overdue"}),
        todoist_paged("tasks/filter", {"query": "today"}),
        todoist_completed(start_local, now_local),
    )

    overdue_ids = {t.get("id") for t in overdue}
    today = [t for t in today if t.get("id") not in overdue_ids]

    snapshot = {"overdue": overdue, "today": today, "done": done}
    _todoist_cache["snapshot"] = (now_ts, snapshot)
    return snapshot


def format_task_line(task: dict, show_due: bool = False) -> str:
    # В API приоритет 4 — самый срочный (в приложении Todoist это P1).
    prio = {4: "🔴 ", 3: "🟠 ", 2: "🟡 "}.get(task.get("priority"), "")
    title = (task.get("content") or "").strip() or "(без названия)"
    line = f"{prio}{title}"
    if show_due:
        due = task.get("due") or {}
        if due.get("date"):
            line += f" (срок {fmt_date(due['date'])})"
    return line


def format_task_section(title: str, tasks: list[dict], show_due: bool = False, limit: int = 15) -> str:
    if not tasks:
        return f"{title}: нет"
    lines = [f"{title} ({len(tasks)}):"]
    for task in tasks[:limit]:
        lines.append(f"  • {format_task_line(task, show_due)}")
    if len(tasks) > limit:
        lines.append(f"  …и ещё {len(tasks) - limit}")
    return "\n".join(lines)


def build_tasks_text(snapshot: dict, heading: str) -> str:
    return "\n\n".join([
        heading,
        format_task_section("📌 Просрочено", snapshot["overdue"], show_due=True),
        format_task_section("📅 На сегодня", snapshot["today"]),
        format_task_section("✅ Выполнено сегодня", snapshot["done"]),
    ])



TODOIST_INTENT_RE = re.compile(
    r"(todoist|задач|дела|дел на|план|расписан|распиш|завтрашн|перенес|перенести|перенеси|"
    r"отлож|сдвин|выполн|заверш|закрой|добавь|создай|поставь|"
    r"запланир|на завтра|на сегодня|на неделю|что у меня)",
    re.IGNORECASE,
)


def is_todoist_intent(message: str) -> bool:
    return bool(TODOIST_INTENT_RE.search(message or ""))


def build_todoist_tools(active_tasks: list[dict]) -> list[dict]:
    """Инструменты ограничены только реально загруженными активными задачами."""
    tools = [{
        "type": "function",
        "function": {
            "name": "todoist_create_task",
            "description": "Предложить создание одной новой задачи в Todoist. Не вызывать для простого планирования без просьбы сохранить задачу.",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {"type": "string", "minLength": 1, "maxLength": 500},
                    "due_string": {"type": "string", "description": "Срок на естественном русском языке, например завтра в 10:00. Не указывать, если срок не задан."}
                },
                "required": ["content"],
                "additionalProperties": False
            }
        }
    }]
    ids = [str(t.get("id")) for t in active_tasks if t.get("id")]
    if ids:
        task_enum = {"type": "string", "enum": ids}
        tools.extend([
            {
                "type": "function",
                "function": {
                    "name": "todoist_reschedule_task",
                    "description": "Предложить перенос срока существующей задачи Todoist. Выбери ID только из списка задач, переданного в контексте.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "task_id": task_enum,
                            "due_string": {"type": "string", "description": "Новый срок на естественном русском языке."}
                        },
                        "required": ["task_id", "due_string"],
                        "additionalProperties": False
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "todoist_complete_task",
                    "description": "Предложить отметить задачу Todoist выполненной. Выбери ID только из переданного списка активных задач.",
                    "parameters": {
                        "type": "object",
                        "properties": {"task_id": task_enum},
                        "required": ["task_id"],
                        "additionalProperties": False
                    }
                }
            }
        ])
    return tools


def todoist_action_from_tool_call(tool_call, active_tasks: list[dict]) -> dict | None:
    """Строго проверяет аргументы инструмента до показа кнопки подтверждения."""
    try:
        name = tool_call.function.name
        args = json.loads(tool_call.function.arguments or "{}")
        active_by_id = {str(t.get("id")): t for t in active_tasks if t.get("id")}
        if name == "todoist_create_task":
            content = str(args.get("content", "")).strip()
            if not content or len(content) > 500:
                return None
            action = {"op": "create", "content": content}
            due = str(args.get("due_string", "")).strip()
            if due:
                action["due_string"] = due[:100]
            return action
        if name == "todoist_reschedule_task":
            task_id = str(args.get("task_id", ""))
            due = str(args.get("due_string", "")).strip()
            if task_id not in active_by_id or not due:
                return None
            return {
                "op": "reschedule", "task_id": task_id,
                "content": str(active_by_id[task_id].get("content") or "Без названия"),
                "due_string": due[:100]
            }
        if name == "todoist_complete_task":
            task_id = str(args.get("task_id", ""))
            if task_id not in active_by_id:
                return None
            return {
                "op": "complete", "task_id": task_id,
                "content": str(active_by_id[task_id].get("content") or "Без названия")
            }
    except Exception:
        logging.exception("Не удалось разобрать предложенное действие Todoist")
    return None


def todoist_action_prompt(action: dict) -> str:
    """Служебный маркер передаётся обработчику, а не показывается как обычный ответ."""
    return "__TODOIST_ACTION__" + json.dumps(action, ensure_ascii=False)


def clean_ai_text(text: str) -> str:
    """Делает ответы читабельными в Telegram без включения Markdown parse_mode."""
    text = re.sub(r"(?m)^\s{0,3}#{1,6}\s*", "", text or "")
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text, flags=re.DOTALL)
    text = re.sub(r"__(.*?)__", r"\1", text, flags=re.DOTALL)
    text = re.sub(r"(?<!\*)\*(?!\s)(.*?)(?<!\s)\*(?!\*)", r"\1", text)
    text = re.sub(r"(?m)^\s*[-*_]{3,}\s*$", "", text)
    # Простые Markdown-таблицы преобразуем в строки с разделителями.
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|"):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if cells and all(re.fullmatch(r":?-{3,}:?", c or "-") for c in cells):
                continue
            line = " • ".join(c for c in cells if c)
        lines.append(line)
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


async def execute_todoist_action(action: dict) -> str:
    """Выполняет только действие, которое пользователь явно подтвердил кнопкой."""
    op = action.get("op")
    if op == "create":
        payload = {"content": action["content"], "due_lang": "ru"}
        if action.get("due_string"):
            payload["due_string"] = action["due_string"]
        result = await todoist_post("tasks", payload)
        _todoist_cache.pop("snapshot", None)
        _todoist_cache.pop("active_tasks", None)
        return f"Задача добавлена в Todoist: {result.get('content', action['content']) if isinstance(result, dict) else action['content']}"
    if op == "reschedule":
        result = await todoist_post(
            f"tasks/{action['task_id']}",
            {"due_string": action["due_string"], "due_lang": "ru"}
        )
        _todoist_cache.pop("snapshot", None)
        _todoist_cache.pop("active_tasks", None)
        return f"Срок задачи «{action['content']}» перенесён: {action['due_string']}."
    if op == "complete":
        await todoist_post(f"tasks/{action['task_id']}/close")
        _todoist_cache.pop("snapshot", None)
        _todoist_cache.pop("active_tasks", None)
        return f"Задача отмечена выполненной: «{action['content']}»."
    raise ValueError("Неизвестный тип действия Todoist")


async def todoist_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_authorized(update):
        await query.answer("Нет доступа.", show_alert=True)
        return
    await query.answer()
    action = context.user_data.pop("todoist_pending_action", None)
    if not action:
        await query.edit_message_text("Это подтверждение уже не действует. Отправь запрос ещё раз.")
        return
    if query.data == "td:no":
        await query.edit_message_text("Отменено. В Todoist ничего не изменено.")
        return
    try:
        result = await execute_todoist_action(action)
        await query.edit_message_text("✅ " + result)
    except Exception:
        logging.exception("Не удалось выполнить подтверждённое действие Todoist")
        await query.edit_message_text(
            "Не удалось изменить Todoist. Проверь подключение и права токена; "
            "сама задача не считается изменённой."
        )


async def todoist_context_for_ai(user_id: int) -> str:
    """Текст про задачи из Todoist для системного промпта."""
    if not todoist_enabled():
        return ""
    try:
        settings = await load_settings(user_id)
        snapshot = await todoist_snapshot(ZoneInfo(settings["timezone"]))
    except Exception:
        logging.exception("Не удалось получить задачи Todoist")
        return ""

    return (
        "Задачи из Todoist — это точные данные и источник задач пользователя. "
        "Сопоставляй их со словами пользователя и называй расхождения: например, "
        "важная задача из Todoist не попала в план или есть просроченные. "
        "Выполненной считай задачу из списка выполненных в Todoist или ту, о которой "
        "пользователь сам сказал. Ты ничего не меняешь в Todoist.\n\n"
        + build_tasks_text(snapshot, "Состояние Todoist на сейчас:")
    )


async def todoist_period_info(kind: str, start, end, tz) -> tuple[str, str]:
    """Для отчёта: (текст для модели, строка с цифрами для заголовка)."""
    if not todoist_enabled():
        return "", ""
    try:
        start_local = datetime(start.year, start.month, start.day, tzinfo=tz)
        now_local = datetime.now(tz)
        done, overdue = await asyncio.gather(
            todoist_completed(start_local, now_local),
            todoist_paged("tasks/filter", {"query": "overdue"}),
        )
    except Exception:
        logging.exception("Не удалось получить задачи Todoist для отчёта")
        return "", ""

    line = f"Задач выполнено в Todoist: {len(done)}; сейчас просрочено: {len(overdue)}"
    block = (
        format_task_section("Выполнено в Todoist за период", done, limit=40)
        + "\n\n"
        + format_task_section("Сейчас просрочено в Todoist", overdue, show_due=True)
        + "\n\n"
    )
    return block, line


async def tasks_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await reject_unauthorized(update)
        return

    if not todoist_enabled():
        await update.effective_message.reply_text(
            "Todoist пока не подключён. Добавь в файл .env строку\n"
            "TODOIST_API_TOKEN=твой_токен\n"
            "Токен берётся в Todoist: Настройки → Интеграции → Разработчик. "
            "После этого перезапусти бота.",
            reply_markup=MAIN_MENU,
        )
        return

    await update.effective_message.chat.send_action("typing")
    try:
        settings = await load_settings(update.effective_user.id)
        snapshot = await todoist_snapshot(ZoneInfo(settings["timezone"]), fresh=True)
    except Exception:
        logging.exception("Не удалось получить задачи из Todoist")
        await update.effective_message.reply_text(
            "Не удалось получить задачи из Todoist. Проверь токен в .env и консоль.",
            reply_markup=MAIN_MENU,
        )
        return

    await reply_long(update.effective_message, build_tasks_text(snapshot, "✅ Задачи в Todoist"), inline=TASKS_KB)


# ===================== АНАЛИТИКА И ОТЧЁТЫ =====================

REPORT_KINDS = {
    "day": {"title": "за день", "days": 1},
    "week": {"title": "за неделю", "days": 7},
    "month": {"title": "за месяц", "days": 30},
}

# Сколько символов берём из каждой записи, чтобы отчёт помещался в лимиты модели.
DATA_LIMITS = {
    "day": {"morning": 1200, "noon": 1200, "evening": 1200, "summary": 800},
    "week": {"morning": 350, "noon": 250, "evening": 450, "summary": 300},
    "month": {"morning": 0, "noon": 0, "evening": 250, "summary": 0},
}

REPORT_PROMPT = """
Ты — аналитик личного ИИ-агента. Составь отчёт о периоде строго по ЗАПИСЯМ ниже.

Правила:
- Опирайся только на записи и цифры из сообщения. Ничего не выдумывай.
- Если данных мало, прямо скажи об этом и не делай громких выводов.
- Каждый вывод подкрепляй примером: дата и короткий пересказ записи.
- Различай факты (что пользователь написал сам) и предположения. Предположения начинай со слова «Предположение».
- Не считай задачу выполненной без слов пользователя или отметки в Todoist.
- Данные Todoist (если есть ниже) точные: сопоставляй их с записями пользователя и называй расхождения.
- Будь честным и требовательным, но не унижай.
- Не используй таблицы и Markdown (звёздочки, решётки). Пиши простым текстом, короткими абзацами, списки через тире.

Структура отчёта:
1. Итог периода (2–3 предложения)
2. Что получилось
3. Что не получилось и почему
4. Повторяющиеся проблемы (переносимые задачи, слишком много задач на день, время суток, когда всё срывается). Пиши только то, что видно в записях.
5. Прогресс по целям и проектам (по списку целей ниже; если по цели нет данных, так и напиши)
6. Что изменить в следующем периоде (1–3 конкретных шага)
"""

REPORT_KIND_HINT = {
    "day": (
        "Это отчёт за ОДИН день. Разделы 4 и 5 пропусти, если данных для них нет."
    ),
    "week": (
        "Это недельный отчёт. Особенно внимательно ищи то, что повторяется несколько дней подряд. "
        "Если ниже есть прошлый недельный отчёт, сравни: что повторилось, а что улучшилось."
    ),
    "month": (
        "Это месячный отчёт. Записи по дням даны сжато, опирайся также на недельные отчёты ниже, "
        "если они есть. Найди устойчивые закономерности и оцени динамику месяца."
    ),
}


def fmt_date(iso: str) -> str:
    """'2026-11-03' -> '03.11'"""
    return f"{iso[8:10]}.{iso[5:7]}"


def split_text(text: str, limit: int = 4000) -> list[str]:
    """Telegram не принимает сообщения длиннее ~4096 символов, режем по строкам."""
    chunks = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut <= 0:
            cut = limit
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    chunks.append(text)
    return chunks


async def reply_long(message, text: str, inline=None) -> None:
    chunks = split_text(text)
    for i, chunk in enumerate(chunks):
        is_last = i == len(chunks) - 1
        await message.reply_text(chunk, reply_markup=(inline or MAIN_MENU) if is_last else None)


async def send_long(bot, chat_id: int, text: str) -> None:
    chunks = split_text(text)
    for i, chunk in enumerate(chunks):
        is_last = i == len(chunks) - 1
        await bot.send_message(
            chat_id=chat_id, text=chunk, reply_markup=MAIN_MENU if is_last else None
        )


async def load_surveys_range(telegram_id: int, start_iso: str, end_iso: str) -> list[dict]:
    result = await db_call(
        supabase.table("daily_surveys")
        .select("kind, survey_date, answer, summary")
        .eq("telegram_id", telegram_id)
        .gte("survey_date", start_iso)
        .lte("survey_date", end_iso)
        .order("survey_date")
        .order("created_at")
        .limit(1000)
        .execute
    )
    return result.data or []


async def load_reports(
    telegram_id: int,
    kind: str,
    gte: str | None = None,
    lt: str | None = None,
    limit: int = 3,
) -> list[dict]:
    """Ранее сохранённые отчёты. Фильтры по дате окончания периода."""
    query = (
        supabase.table("analytics_reports")
        .select("period_start, period_end, content")
        .eq("telegram_id", telegram_id)
        .eq("kind", kind)
    )
    if gte:
        query = query.gte("period_end", gte)
    if lt:
        query = query.lt("period_end", lt)
    result = await db_call(
        query.order("period_end", desc=True).limit(limit).execute
    )
    return list(reversed(result.data or []))


async def save_report(telegram_id: int, kind: str, start, end, content: str) -> None:
    await db_call(
        supabase.table("analytics_reports")
        .insert({
            "telegram_id": telegram_id,
            "kind": kind,
            "period_start": start.isoformat(),
            "period_end": end.isoformat(),
            "content": content,
        })
        .execute
    )


def compute_period_stats(rows: list[dict], start, end) -> dict:
    """Точные цифры считаем кодом, а не моделью."""
    sent = {k: 0 for k in SURVEY_ORDER}
    answered = {k: 0 for k in SURVEY_ORDER}
    answered_days = set()

    for r in rows:
        k = r.get("kind")
        if k not in sent:
            continue
        sent[k] += 1
        if (r.get("answer") or "").strip():
            answered[k] += 1
            answered_days.add(r["survey_date"])

    total_days = (end - start).days + 1
    missed = []
    for i in range(total_days):
        d = (start + timedelta(days=i)).isoformat()
        # Сегодняшний день не считаем пропущенным: он ещё идёт.
        if d not in answered_days and d != end.isoformat():
            missed.append(d)

    return {
        "sent": sent,
        "answered": answered,
        "answered_days": answered_days,
        "total_days": total_days,
        "missed": missed,
    }


def build_stats_header(kind: str, start, end, stats: dict) -> str:
    if kind == "day":
        period = fmt_date(end.isoformat())
    else:
        period = f"{fmt_date(start.isoformat())}–{fmt_date(end.isoformat())}"

    sent_total = sum(stats["sent"].values())
    answered_total = sum(stats["answered"].values())

    lines = [
        f"📈 Отчёт {REPORT_KINDS[kind]['title']} ({period})",
        f"Дней с ответами: {len(stats['answered_days'])} из {stats['total_days']}",
    ]
    if sent_total:
        percent = round(answered_total * 100 / sent_total)
        lines.append(f"Опросов отправлено: {sent_total}, с ответом: {answered_total} ({percent}%)")
        for k in SURVEY_ORDER:
            lines.append(f"  • {SURVEYS[k]['title']}: {stats['answered'][k]} из {stats['sent'][k]}")
    else:
        lines.append("Опросов за период не отправлялось.")

    if kind != "day" and stats["missed"]:
        shown = ", ".join(fmt_date(d) for d in stats["missed"][:10])
        lines.append(f"Дни без ответов: {shown}")

    return "\n".join(lines)


def build_period_data(rows: list[dict], kind: str) -> str:
    """Собираем записи по дням в компактный текст для модели."""
    limits = DATA_LIMITS[kind]

    by_date: dict[str, dict] = {}
    for r in rows:
        by_date.setdefault(r["survey_date"], {})[r["kind"]] = r

    blocks = []
    for d in sorted(by_date):
        day = by_date[d]
        parts = []
        for k in SURVEY_ORDER:
            r = day.get(k)
            limit = limits[k]
            if not limit or not r or not (r.get("answer") or "").strip():
                continue
            parts.append(f"{SURVEYS[k]['title']}: {clip(r['answer'], limit)}")
            if limits["summary"] and (k == "evening" or kind == "day") and r.get("summary"):
                parts.append(f"Вывод агента: {clip(r['summary'], limits['summary'])}")
        if parts:
            blocks.append(f"[{fmt_date(d)}]\n" + "\n".join(parts))

    return "\n\n".join(blocks)


async def build_goals_block(user_id: int) -> str:
    try:
        memories = await load_memories(user_id, limit=100)
    except Exception:
        logging.exception("Не удалось загрузить цели для отчёта")
        return "(не удалось загрузить)"

    wanted = ("goal", "project", "commitment", "habit", "observation")
    lines = [
        f"- [{CATEGORY_NAMES.get(m['category'], m['category'])}] {m['content']}"
        for m in memories
        if m["category"] in wanted
    ]
    result = "\n".join(lines) or "(целей и проектов в памяти пока нет)"
    try:
        projects_block = await build_projects_block(user_id)
    except Exception:
        projects_block = ""
    return result + projects_block


async def generate_report(user_id: int, kind: str) -> str:
    settings = await load_settings(user_id)
    tz = ZoneInfo(settings["timezone"])
    end = datetime.now(tz).date()
    start = end - timedelta(days=REPORT_KINDS[kind]["days"] - 1)

    rows = await load_surveys_range(user_id, start.isoformat(), end.isoformat())
    stats = compute_period_stats(rows, start, end)
    header = build_stats_header(kind, start, end, stats)

    todoist_block, todoist_line = await todoist_period_info(kind, start, end, tz)
    if todoist_line:
        header += "\n" + todoist_line

    if not stats["answered_days"]:
        return (
            header
            + "\n\nОтветов на опросы за этот период пока нет, анализировать нечего. "
            "Отвечай на утренние, дневные и вечерние вопросы, и отчёт станет полезным."
        )

    data = build_period_data(rows, kind)
    goals = await build_goals_block(user_id)

    extra = ""
    try:
        if kind == "week":
            previous = await load_reports(user_id, "week", lt=end.isoformat(), limit=1)
            if previous:
                p = previous[-1]
                extra = (
                    f"\n\nПрошлый недельный отчёт ({fmt_date(p['period_start'])}–"
                    f"{fmt_date(p['period_end'])}):\n{clip(p['content'], 1500)}"
                )
        elif kind == "month":
            weeks = await load_reports(
                user_id, "week", gte=start.isoformat(), lt=(end + timedelta(days=1)).isoformat(), limit=5
            )
            if weeks:
                parts = [
                    f"Неделя {fmt_date(w['period_start'])}–{fmt_date(w['period_end'])}:\n{clip(w['content'], 1200)}"
                    for w in weeks
                ]
                extra = "\n\nНедельные отчёты за период:\n" + "\n\n".join(parts)
    except Exception:
        logging.exception("Не удалось загрузить прошлые отчёты")

    user_content = (
        f"Цифры за период (посчитаны программой, они точные):\n{header}\n\n"
        f"Цели, проекты и наблюдения из памяти:\n{goals}\n\n"
        f"{todoist_block}"
        f"Записи за период:\n{data}{extra}"
    )

    response = await ai.chat.completions.create(
        model=GROQ_MODEL,
        messages=[
            {"role": "system", "content": REPORT_PROMPT + "\n" + REPORT_KIND_HINT[kind]},
            {"role": "user", "content": user_content},
        ],
        temperature=0.3,
        max_tokens=3000,
    )
    analysis = (response.choices[0].message.content or "").strip()
    if not analysis:
        return header + "\n\nМодель не вернула текст отчёта. Попробуй ещё раз чуть позже."

    try:
        await save_report(user_id, kind, start, end, analysis)
    except Exception:
        logging.exception("Не удалось сохранить отчёт в analytics_reports")

    return header + "\n\n" + analysis


async def weekly_report_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Запускается каждый день в назначенное время. Воскресенье: недельный отчёт и финансы за неделю.
    Последний день месяца: финансы за месяц."""
    try:
        settings = await load_settings(ALLOWED_TELEGRAM_USER_ID)
        local_now = datetime.now(ZoneInfo(settings["timezone"]))
    except Exception:
        logging.exception("Не удалось прочитать настройки для недельной задачи")
        return

    if local_now.weekday() == 6:  # 6 = воскресенье
        try:
            text = await generate_report(ALLOWED_TELEGRAM_USER_ID, "week")
            await send_long(context.bot, ALLOWED_TELEGRAM_USER_ID, text)
        except Exception:
            logging.exception("Не удалось отправить недельный отчёт")
        try:
            await send_backup(context.bot, ALLOWED_TELEGRAM_USER_ID, auto=True)
        except Exception:
            logging.exception("Не удалось отправить автоматическую резервную копию")
        try:
            fin_text, count = await build_finance_text(ALLOWED_TELEGRAM_USER_ID, "week")
            if count:
                await send_long(context.bot, ALLOWED_TELEGRAM_USER_ID, fin_text)
        except Exception:
            logging.exception("Не удалось отправить финансовый итог недели")

    last_day = calendar.monthrange(local_now.year, local_now.month)[1]
    if local_now.day == last_day:
        try:
            fin_text, count = await build_finance_text(ALLOWED_TELEGRAM_USER_ID, "month")
            if count:
                await send_long(context.bot, ALLOWED_TELEGRAM_USER_ID, fin_text)
        except Exception:
            logging.exception("Не удалось отправить финансовый итог месяца")


REPORT_MENU = InlineKeyboardMarkup([[
    InlineKeyboardButton("День", callback_data="report:day"),
    InlineKeyboardButton("Неделя", callback_data="report:week"),
    InlineKeyboardButton("Месяц", callback_data="report:month"),
]])


async def run_report(message, user_id: int, kind: str) -> None:
    await message.chat.send_action("typing")
    try:
        text = await generate_report(user_id, kind)
    except Exception:
        logging.exception("Не удалось собрать отчёт %s", kind)
        await message.reply_text(
            "Не удалось собрать отчёт. Проверь, что SQL для этапа 5 выполнен в Supabase. "
            "Если SQL выполнен, возможно, Groq временно ограничил запросы: попробуй через минуту. "
            "Подробности в консоли.",
            reply_markup=MAIN_MENU,
        )
        return
    await reply_long(message, text)


async def reports_menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await reject_unauthorized(update)
        return

    await update.effective_message.reply_text(
        "Какой отчёт показать?", reply_markup=REPORT_MENU
    )


async def day_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await reject_unauthorized(update)
        return
    await run_report(update.effective_message, update.effective_user.id, "day")


async def week_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await reject_unauthorized(update)
        return
    await run_report(update.effective_message, update.effective_user.id, "week")


async def month_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await reject_unauthorized(update)
        return
    await run_report(update.effective_message, update.effective_user.id, "month")


async def report_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Нажатие на кнопки День / Неделя / Месяц под сообщением «Какой отчёт показать?»."""
    query = update.callback_query
    if not is_authorized(update):
        await query.answer("Доступ запрещён.", show_alert=True)
        return

    kind = (query.data or "").split(":", 1)[-1]
    if kind not in REPORT_KINDS:
        await query.answer()
        return

    await query.answer("Собираю отчёт…")
    await run_report(query.message, update.effective_user.id, kind)


# ===================== СТАТИСТИКА =====================

async def count_rows(table: str, **filters) -> int:
    """Считаем строки в таблице (с необязательными фильтрами вида колонка=значение)."""
    query = supabase.table(table).select("*", count="exact")
    for column, value in filters.items():
        query = query.eq(column, value)
    result = await db_call(query.limit(1).execute)
    return result.count or 0


async def safe_count(table: str, **filters) -> str:
    """Как count_rows, но при ошибке не падает, а возвращает «ошибка»."""
    try:
        return str(await count_rows(table, **filters))
    except Exception:
        logging.exception("Не удалось посчитать строки в таблице %s", table)
        return "ошибка"


async def edge_date(table: str, newest: bool) -> str:
    """Дата самой новой или самой старой записи в таблице."""
    try:
        result = await db_call(
            supabase.table(table)
            .select("created_at")
            .order("created_at", desc=newest)
            .limit(1)
            .execute
        )
        if result.data:
            return result.data[0]["created_at"][:16].replace("T", " ")
        return "—"
    except Exception:
        logging.exception("Не удалось получить дату из таблицы %s", table)
        return "ошибка"


async def build_stats_text() -> str:
    lines = ["📊 Что лежит в базе\n"]

    # --- Переписка ---
    total = await safe_count("conversations")
    from_user = await safe_count("conversations", role="user")
    from_bot = await safe_count("conversations", role="assistant")
    first_date = await edge_date("conversations", newest=False)
    last_date = await edge_date("conversations", newest=True)
    lines += [
        "💬 Переписка (conversations)",
        f"Всего сообщений: {total}",
        f"Твоих: {from_user}, ответов бота: {from_bot}",
        f"Первое: {first_date}",
        f"Последнее: {last_date}",
        "",
    ]

    # --- Память ---
    active = await safe_count("memories", status="active")
    archived = await safe_count("memories", status="archived")
    lines += [
        "🧠 Память (memories)",
        f"Активных записей: {active}",
        f"В архиве: {archived}",
    ]

    try:
        result = await db_call(
            supabase.table("memories")
            .select("category, importance")
            .eq("status", "active")
            .limit(1000)
            .execute
        )
        rows = result.data or []
        if rows:
            by_category: dict[str, int] = {}
            for row in rows:
                by_category[row["category"]] = by_category.get(row["category"], 0) + 1
            for category, count in sorted(by_category.items(), key=lambda x: -x[1]):
                lines.append(f"  • {CATEGORY_NAMES.get(category, category)}: {count}")

            importances = [r["importance"] for r in rows if r.get("importance")]
            if importances:
                avg = sum(importances) / len(importances)
                lines.append(f"Средняя важность: {avg:.1f} из 5")
    except Exception:
        logging.exception("Не удалось разобрать память по категориям")
        lines.append("Разбивка по категориям: ошибка")
    lines.append("")

    # --- Опросы ---
    surveys_total = await safe_count("daily_surveys")
    lines += [
        "⏰ Опросы (daily_surveys)",
        f"Отправлено опросов: {surveys_total}",
    ]
    for kind in SURVEY_ORDER:
        count = await safe_count("daily_surveys", kind=kind)
        lines.append(f"  • {SURVEYS[kind]['title']}: {count}")
    lines.append("")

    # --- Отчёты ---
    reports_total = await safe_count("analytics_reports")
    lines += [
        "📈 Отчёты (analytics_reports)",
        f"Сохранено отчётов: {reports_total}",
    ]
    for report_kind, label in (("day", "Дневных"), ("week", "Недельных"), ("month", "Месячных")):
        count = await safe_count("analytics_reports", kind=report_kind)
        lines.append(f"  • {label}: {count}")
    lines.append("")

    # --- Остальные таблицы ---
    profiles = await safe_count("profiles")
    daily_logs = await safe_count("daily_logs")
    insights = await safe_count("ai_insights")
    lines += [
        "🗂 Остальные таблицы",
        f"Профили (profiles): {profiles}",
        f"Дневные отчёты (daily_logs): {daily_logs}",
        f"Наблюдения ИИ (ai_insights): {insights}",
    ]

    return "\n".join(lines)


# ===================== ФИНАНСЫ (ЭТАП 7А) =====================

CURRENCY = "₽"
FIN_TABLES = {"finance_transactions", "finance_payments", "finance_purchases"}

FIN_SQL_HINT = "Проверь, что SQL этапа 7 (stage7_finance.sql) выполнен в Supabase. Подробности в консоли."

BUY_PROMPT = """
Ты — финансовый помощник личного ИИ-агента. Пользователь думает о покупке.
Оцени, можно ли её себе позволить, строго по ЦИФРАМ ниже. Цифры посчитаны программой и точные.

Правила:
- Ничего не выдумывай и не добавляй доходы или расходы, которых нет в цифрах.
- Если данных мало (об этом сказано в цифрах), прямо скажи и не делай уверенных выводов.
- Ты не финансовый консультант: помогай прикинуть, а не приказывай.
- Будь честным: если покупка бьёт по бюджету, скажи прямо, но без осуждения.
- Предложи 1–2 конкретных варианта: купить сейчас, подождать несколько месяцев, откладывать часть суммы.
- Не используй таблицы и Markdown (звёздочки, решётки). Пиши простым текстом, 5–8 предложений.
"""


def parse_amount(text: str) -> float | None:
    """'350' -> 350.0, '1500,50' -> 1500.5. Неверное или неположительное число -> None."""
    cleaned = text.replace("\u00a0", "").replace(" ", "").replace(",", ".")
    try:
        value = float(cleaned)
    except ValueError:
        return None
    if not (0 < value < 1_000_000_000):
        return None
    return round(value, 2)


def money(value: float) -> str:
    value = round(float(value), 2)
    if value == int(value):
        text = f"{int(value):,}".replace(",", " ")
    else:
        text = f"{value:,.2f}".replace(",", " ")
    return f"{text} {CURRENCY}"


async def user_today(user_id: int) -> dt_date:
    settings = await load_settings(user_id)
    return datetime.now(ZoneInfo(settings["timezone"])).date()


def finance_period(kind: str, today: dt_date) -> tuple[dt_date, dt_date]:
    """Неделя: с понедельника по воскресенье. Месяц: с 1-го по последнее число."""
    if kind == "week":
        start = today - timedelta(days=today.weekday())
        return start, start + timedelta(days=6)
    last = calendar.monthrange(today.year, today.month)[1]
    return today.replace(day=1), today.replace(day=last)


def sum_by_kind(rows: list[dict]) -> tuple[float, float]:
    income = sum(float(r["amount"]) for r in rows if r["kind"] == "income")
    expense = sum(float(r["amount"]) for r in rows if r["kind"] == "expense")
    return income, expense


def upcoming_payments(payments: list[dict], today: dt_date) -> list[dict]:
    """Обязательные платежи, которые ещё впереди в этом месяце (включая сегодняшний день)."""
    last = calendar.monthrange(today.year, today.month)[1]
    items = []
    for p in payments:
        due = min(int(p["day_of_month"]), last)
        if due >= today.day:
            items.append({"title": p["title"], "amount": float(p["amount"]), "day": due})
    items.sort(key=lambda x: x["day"])
    return items


# ---------- база ----------

async def fin_add_transaction(
    user_id: int,
    kind: str,
    amount: float,
    category: str,
    note: str | None,
    tx_date: dt_date,
    project_id: int | None = None,
    source_update_id: int | None = None,
    source_item_index: int = 0,
) -> bool:
    """Сохраняет операцию. Возвращает False, если Telegram update уже обработан."""
    await ensure_profile(user_id)

    payload = {
        "telegram_id": user_id,
        "kind": kind,
        "amount": amount,
        "category": category,
        "note": note,
        "tx_date": tx_date.isoformat(),
        "project_id": project_id,
        "source_update_id": source_update_id,
        "source_item_index": source_item_index,
    }

    if source_update_id is not None:
        existing = await db_call(
            supabase.table("finance_transactions")
            .select("id")
            .eq("telegram_id", user_id)
            .eq("source_update_id", source_update_id)
            .eq("source_item_index", source_item_index)
            .limit(1)
            .execute
        )
        if existing.data:
            return False

    try:
        result = await db_call(
            supabase.table("finance_transactions").insert(payload).execute
        )
    except Exception:
        # Уникальный индекс закрывает гонку между параллельными повторами update.
        if source_update_id is not None:
            existing = await db_call(
                supabase.table("finance_transactions")
                .select("id")
                .eq("telegram_id", user_id)
                .eq("source_update_id", source_update_id)
                .eq("source_item_index", source_item_index)
                .limit(1)
                .execute
            )
            if existing.data:
                return False
        raise

    return bool(result.data)


async def fin_load_transactions(user_id: int, start_iso: str, end_iso: str) -> list[dict]:
    result = await db_call(
        supabase.table("finance_transactions")
        .select("id, kind, amount, category, note, tx_date")
        .eq("telegram_id", user_id)
        .gte("tx_date", start_iso)
        .lte("tx_date", end_iso)
        .order("tx_date", desc=True)
        .order("id", desc=True)
        .limit(1000)
        .execute
    )
    return result.data or []


async def fin_load_recent(user_id: int, limit: int = 10) -> list[dict]:
    result = await db_call(
        supabase.table("finance_transactions")
        .select("id, kind, amount, category, note, tx_date")
        .eq("telegram_id", user_id)
        .order("tx_date", desc=True)
        .order("id", desc=True)
        .limit(limit)
        .execute
    )
    return result.data or []


async def fin_load_payments(user_id: int) -> list[dict]:
    result = await db_call(
        supabase.table("finance_payments")
        .select("id, title, amount, day_of_month")
        .eq("telegram_id", user_id)
        .order("day_of_month")
        .execute
    )
    return result.data or []


async def fin_load_purchases(user_id: int) -> list[dict]:
    result = await db_call(
        supabase.table("finance_purchases")
        .select("id, title, price")
        .eq("telegram_id", user_id)
        .eq("status", "planned")
        .order("id")
        .execute
    )
    return result.data or []


async def fin_delete(table: str, user_id: int, row_id: int) -> bool:
    if table not in FIN_TABLES:
        raise ValueError("Неизвестная таблица")
    result = await db_call(
        supabase.table(table)
        .delete()
        .eq("id", row_id)
        .eq("telegram_id", user_id)
        .execute
    )
    return bool(result.data)


# ---------- итоги ----------

async def build_finance_text(user_id: int, kind: str) -> tuple[str, int]:
    """Возвращает (текст итогов, сколько записей было за период)."""
    today = await user_today(user_id)
    start, end = finance_period(kind, today)
    rows = await fin_load_transactions(user_id, start.isoformat(), end.isoformat())
    income, expense = sum_by_kind(rows)

    title = "за неделю" if kind == "week" else "за месяц"
    lines = [
        f"💰 Финансы {title} ({fmt_date(start.isoformat())}–{fmt_date(end.isoformat())})",
        "",
        f"Доходы: {money(income)}",
        f"Расходы: {money(expense)}",
        f"Разница: {money(income - expense)}",
    ]

    categories: dict[str, float] = {}
    for r in rows:
        if r["kind"] == "expense":
            name = r.get("category") or "без категории"
            categories[name] = categories.get(name, 0.0) + float(r["amount"])
    if categories and expense > 0:
        lines += ["", "Куда ушли деньги:"]
        for name, total in sorted(categories.items(), key=lambda x: -x[1])[:6]:
            lines.append(f"- {name}: {money(total)} ({round(total / expense * 100)}%)")

    if not rows:
        lines += [
            "",
            "Записей за этот период пока нет. Нажми «➖ Расход» или «➕ Доход» ниже.",
        ]

    if kind == "month":
        payments = await fin_load_payments(user_id)
        ahead = upcoming_payments(payments, today)
        if ahead:
            ahead_total = sum(item["amount"] for item in ahead)
            lines += ["", f"Обязательные платежи впереди в этом месяце: {money(ahead_total)}"]
            for item in ahead:
                lines.append(f"- {item['day']:02d}.{today.month:02d} {item['title']}: {money(item['amount'])}")
            left = income - expense - ahead_total
            lines.append(f"После них по записям останется: {money(left)}")
            if left < 0:
                lines.append(
                    f"⚠️ По записям не хватает {money(-left)}. Если зарплата ещё не пришла и ты её "
                    "не записал, это нормально. Если пришла, пора урезать расходы."
                )
        lines += ["", "Считаю только по твоим записям, остаток на счёте мне неизвестен."]

    return "\n".join(lines), len(rows)


async def build_purchase_facts(user_id: int, price: float, exclude_id: int) -> str:
    """Собираем точные цифры для оценки покупки. Считает программа, не модель."""
    today = await user_today(user_id)
    m_start, m_end = finance_period("month", today)
    month_rows = await fin_load_transactions(user_id, m_start.isoformat(), m_end.isoformat())
    m_income, m_expense = sum_by_kind(month_rows)

    payments = await fin_load_payments(user_id)
    ahead_total = sum(item["amount"] for item in upcoming_payments(payments, today))
    monthly_payments = sum(float(p["amount"]) for p in payments)
    left_month = m_income - m_expense - ahead_total

    window_start = today - timedelta(days=89)
    rows = await fin_load_transactions(user_id, window_start.isoformat(), today.isoformat())
    income, expense = sum_by_kind(rows)
    data_days = 0
    if rows:
        first = min(dt_date.fromisoformat(r["tx_date"]) for r in rows)
        data_days = (today - first).days + 1

    others = [p for p in await fin_load_purchases(user_id) if p["id"] != exclude_id]
    others_total = sum(float(p["price"]) for p in others)

    lines = [
        f"Цена покупки: {money(price)}",
        f"В этом месяце: доходы {money(m_income)}, расходы {money(m_expense)}, "
        f"обязательные платежи впереди {money(ahead_total)}, останется по записям {money(left_month)}",
        f"Обязательные платежи в месяц всего: {money(monthly_payments)}",
    ]
    if data_days >= 14:
        factor = 30 / data_days
        avg_income = income * factor
        avg_expense = expense * factor
        avg_save = avg_income - avg_expense
        lines.append(
            f"В среднем за месяц (по записям за {data_days} дн.): доходы {money(avg_income)}, "
            f"расходы {money(avg_expense)}, остаётся {money(avg_save)}"
        )
        if avg_save > 0:
            lines.append(f"При такой норме покупку можно накопить примерно за {price / avg_save:.1f} мес.")
        else:
            lines.append("При такой норме откладывать нечего: расходы не меньше доходов.")
    else:
        lines.append(
            f"ДАННЫХ МАЛО: записи ведутся только {data_days} дн. Среднемесячные цифры не считаю."
        )
    if others:
        lines.append(f"Другие запланированные покупки: {len(others)} шт. на {money(others_total)}")
    return "\n".join(lines)


# ---------- команды: записи ----------

async def finance_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await reject_unauthorized(update)
        return

    arg = context.args[0].lower() if context.args else "month"
    kind = "week" if arg in ("week", "неделя") else "month"
    try:
        text, _ = await build_finance_text(update.effective_user.id, kind)
    except Exception:
        logging.exception("Не удалось собрать финансовые итоги")
        await update.effective_message.reply_text(FIN_SQL_HINT, reply_markup=MAIN_MENU)
        return
    await reply_long(update.effective_message, text, inline=FIN_MENU)


async def fin_add_command(update: Update, context: ContextTypes.DEFAULT_TYPE, kind: str):
    if not is_authorized(update):
        await reject_unauthorized(update)
        return

    example = "/expense 350 еда обед" if kind == "expense" else "/income 50000 зарплата аванс"
    args = context.args or []
    amount = parse_amount(args[0]) if args else None
    if amount is None:
        await update.effective_message.reply_text(
            "Напиши сумму числом без пробелов, потом категорию и заметку. Например:\n" + example,
            reply_markup=MAIN_MENU,
        )
        return

    user_id = update.effective_user.id
    details = args[1:]
    project_tokens = [part for part in details if part.lower().startswith("project=")]
    details = [part for part in details if not part.lower().startswith("project=")]
    category = details[0].lower()[:40] if details else "без категории"
    note = clip(" ".join(details[1:]), 200) or None
    project_id = None

    if project_tokens:
        raw_project_id = project_tokens[-1].split("=", 1)[1]
        if not raw_project_id.isdigit():
            await update.effective_message.reply_text(
                "Формат проекта: project=ID. Номер можно посмотреть в /projects."
            )
            return
        project_id = int(raw_project_id)
        project = await pj_get(user_id, project_id)
        if not project:
            await update.effective_message.reply_text(
                "Не нашёл этот проект среди твоих проектов. Проверь /projects."
            )
            return

    try:
        today = await user_today(user_id)
        inserted = await fin_add_transaction(
            user_id, kind, amount, category, note, today,
            project_id=project_id, source_update_id=update.update_id,
        )
        if not inserted:
            await update.effective_message.reply_text(
                "Это сообщение уже было обработано: повторную запись не создавал."
            )
            return
        start, end = finance_period("month", today)
        rows = await fin_load_transactions(user_id, start.isoformat(), end.isoformat())
        income, expense = sum_by_kind(rows)
    except Exception:
        logging.exception("Не удалось записать финансовую операцию")
        await update.effective_message.reply_text(FIN_SQL_HINT, reply_markup=MAIN_MENU)
        return

    project_suffix = f"\nПроект: {project['title']}" if project_id else ""
    word = "Расход записан" if kind == "expense" else "Доход записан"
    await update.effective_message.reply_text(
        f"✅ {word}: {money(amount)} ({category}).{project_suffix}\n"
        f"За месяц: доходы {money(income)}, расходы {money(expense)}.",
        reply_markup=FIN_AFTER_KB,
    )


async def expense_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await fin_add_command(update, context, "expense")


async def income_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await fin_add_command(update, context, "income")


async def fin_list_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await reject_unauthorized(update)
        return

    try:
        rows = await fin_load_recent(update.effective_user.id, 10)
    except Exception:
        logging.exception("Не удалось получить список операций")
        await update.effective_message.reply_text(FIN_SQL_HINT, reply_markup=MAIN_MENU)
        return

    if not rows:
        await update.effective_message.reply_text("Операций пока нет.", reply_markup=FIN_ADD_KB)
        return

    lines = ["Последние операции:", ""]
    for r in rows:
        sign = "+" if r["kind"] == "income" else "−"
        note = f" — {r['note']}" if r.get("note") else ""
        lines.append(
            f"№{r['id']} | {fmt_date(r['tx_date'])} | {sign}{money(float(r['amount']))} | "
            f"{r.get('category') or 'без категории'}{note}"
        )
    lines += ["", "Кнопки с корзиной ниже удаляют запись."]
    await reply_long(update.effective_message, "\n".join(lines), inline=fin_list_keyboard(rows))


async def fin_del_generic(
    update: Update, context: ContextTypes.DEFAULT_TYPE, table: str, usage: str, done: str, missing: str
):
    if not is_authorized(update):
        await reject_unauthorized(update)
        return

    if not context.args or not context.args[0].isdigit():
        await update.effective_message.reply_text(usage, reply_markup=MAIN_MENU)
        return

    try:
        deleted = await fin_delete(table, update.effective_user.id, int(context.args[0]))
    except Exception:
        logging.exception("Не удалось удалить запись")
        await update.effective_message.reply_text(FIN_SQL_HINT, reply_markup=MAIN_MENU)
        return

    await update.effective_message.reply_text(done if deleted else missing, reply_markup=MAIN_MENU)


async def fin_del_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await fin_del_generic(
        update, context, "finance_transactions",
        "Напиши номер записи: /fin_del 12. Номера видны в /fin_list",
        "Запись удалена.", "Не нашёл запись с таким номером.",
    )


# ---------- команды: обязательные платежи ----------

async def payment_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await reject_unauthorized(update)
        return

    args = context.args or []
    amount = parse_amount(args[0]) if args else None
    day_ok = len(args) > 1 and args[1].isdigit() and 1 <= int(args[1]) <= 31
    title = clip(" ".join(args[2:]), 80)
    if amount is None or not day_ok or not title:
        await update.effective_message.reply_text(
            "Формат: /payment сумма число_месяца название\n"
            "Например: /payment 15000 5 Аренда квартиры\n"
            "(платёж каждый месяц 5-го числа)",
            reply_markup=MAIN_MENU,
        )
        return

    user_id = update.effective_user.id
    try:
        await ensure_profile(user_id)
        await db_call(
            supabase.table("finance_payments")
            .insert({
                "telegram_id": user_id,
                "title": title,
                "amount": amount,
                "day_of_month": int(args[1]),
            })
            .execute
        )
    except Exception:
        logging.exception("Не удалось сохранить обязательный платёж")
        await update.effective_message.reply_text(FIN_SQL_HINT, reply_markup=MAIN_MENU)
        return

    await update.effective_message.reply_text(
        f"✅ Платёж сохранён: {title}, {money(amount)}, каждый месяц {int(args[1])}-го числа.",
        reply_markup=PAY_AFTER_KB,
    )


async def payments_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await reject_unauthorized(update)
        return

    try:
        payments = await fin_load_payments(update.effective_user.id)
    except Exception:
        logging.exception("Не удалось получить платежи")
        await update.effective_message.reply_text(FIN_SQL_HINT, reply_markup=MAIN_MENU)
        return

    if not payments:
        await update.effective_message.reply_text(
            "Обязательных платежей пока нет.",
            reply_markup=payments_keyboard([]),
        )
        return

    total = sum(float(p["amount"]) for p in payments)
    lines = ["Обязательные платежи (каждый месяц):", ""]
    for p in payments:
        lines.append(f"№{p['id']} | {p['day_of_month']}-го | {money(float(p['amount']))} | {p['title']}")
    lines += ["", f"Всего в месяц: {money(total)}"]
    await reply_long(update.effective_message, "\n".join(lines), inline=payments_keyboard(payments))


async def payment_del_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await fin_del_generic(
        update, context, "finance_payments",
        "Напиши номер платежа: /payment_del 3. Номера видны в /payments",
        "Платёж удалён.", "Не нашёл платёж с таким номером.",
    )


# ---------- команды: покупки ----------

async def buy_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await reject_unauthorized(update)
        return

    args = context.args or []
    price = parse_amount(args[0]) if args else None
    title = clip(" ".join(args[1:]), 100)
    if price is None or not title:
        await update.effective_message.reply_text(
            "Формат: /buy цена название\nНапример: /buy 80000 Новый ноутбук",
            reply_markup=MAIN_MENU,
        )
        return

    user_id = update.effective_user.id
    try:
        await ensure_profile(user_id)
        await db_call(
            supabase.table("finance_purchases")
            .insert({"telegram_id": user_id, "title": title, "price": price})
            .execute
        )
    except Exception:
        logging.exception("Не удалось сохранить покупку")
        await update.effective_message.reply_text(FIN_SQL_HINT, reply_markup=MAIN_MENU)
        return

    await update.effective_message.reply_text(
        f"✅ Покупка в планах: {title}, {money(price)}.\n"
        "Оценить, потянешь ли: открой «Покупки» и нажми 📊 у этой покупки.",
        reply_markup=BUY_AFTER_KB,
    )


async def buys_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await reject_unauthorized(update)
        return

    try:
        purchases = await fin_load_purchases(update.effective_user.id)
    except Exception:
        logging.exception("Не удалось получить покупки")
        await update.effective_message.reply_text(FIN_SQL_HINT, reply_markup=MAIN_MENU)
        return

    if not purchases:
        await update.effective_message.reply_text(
            "Запланированных покупок нет.",
            reply_markup=buys_keyboard([]),
        )
        return

    total = sum(float(p["price"]) for p in purchases)
    lines = ["Запланированные покупки:", ""]
    for p in purchases:
        lines.append(f"№{p['id']} | {money(float(p['price']))} | {p['title']}")
    lines += [
        "",
        f"Всего: {money(total)}",
        "📊 оценить, потянешь ли",
        "✅ купил (запишу как расход)",
        "🗑 убрать из планов",
    ]
    await reply_long(update.effective_message, "\n".join(lines), inline=buys_keyboard(purchases))


async def find_planned_purchase(user_id: int, purchase_id: int) -> dict | None:
    for p in await fin_load_purchases(user_id):
        if p["id"] == purchase_id:
            return p
    return None


async def buy_check_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await reject_unauthorized(update)
        return

    if not context.args or not context.args[0].isdigit():
        await update.effective_message.reply_text(
            "Напиши номер покупки: /buy_check 2. Номера видны в /buys", reply_markup=MAIN_MENU
        )
        return

    user_id = update.effective_user.id
    await update.effective_message.chat.send_action("typing")
    try:
        purchase = await find_planned_purchase(user_id, int(context.args[0]))
        if not purchase:
            await update.effective_message.reply_text(
                "Не нашёл покупку с таким номером. Список: /buys", reply_markup=MAIN_MENU
            )
            return
        price = float(purchase["price"])
        facts = await build_purchase_facts(user_id, price, purchase["id"])
    except Exception:
        logging.exception("Не удалось собрать цифры для покупки")
        await update.effective_message.reply_text(FIN_SQL_HINT, reply_markup=MAIN_MENU)
        return

    header = f"🛒 {purchase['title']}\n{facts}"
    try:
        response = await ai.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": BUY_PROMPT},
                {"role": "user", "content": f"Покупка: {purchase['title']}\n{facts}"},
            ],
            temperature=0.3,
            max_tokens=1500,
        )
        analysis = (response.choices[0].message.content or "").strip()
    except Exception:
        logging.exception("Groq не ответил при оценке покупки")
        analysis = ""

    text = header + ("\n\n" + analysis if analysis else "\n\nКомментарий модели получить не удалось, цифры выше точные.")
    await reply_long(update.effective_message, text)


async def buy_done_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await reject_unauthorized(update)
        return

    if not context.args or not context.args[0].isdigit():
        await update.effective_message.reply_text(
            "Напиши номер покупки: /buy_done 2. Номера видны в /buys", reply_markup=MAIN_MENU
        )
        return

    user_id = update.effective_user.id
    try:
        purchase = await find_planned_purchase(user_id, int(context.args[0]))
        if not purchase:
            await update.effective_message.reply_text(
                "Не нашёл покупку с таким номером. Список: /buys", reply_markup=MAIN_MENU
            )
            return
        today = await user_today(user_id)
        inserted = await fin_add_transaction(
            user_id, "expense", float(purchase["price"]), "покупка", purchase["title"], today,
            source_update_id=update.update_id,
        )
        if not inserted:
            await update.effective_message.reply_text(
                "Это действие уже было обработано ранее.", reply_markup=MAIN_MENU
            )
            return
        await db_call(
            supabase.table("finance_purchases")
            .update({"status": "bought"})
            .eq("id", purchase["id"])
            .eq("telegram_id", user_id)
            .execute
        )
    except Exception:
        logging.exception("Не удалось отметить покупку купленной")
        await update.effective_message.reply_text(FIN_SQL_HINT, reply_markup=MAIN_MENU)
        return

    await update.effective_message.reply_text(
        f"✅ Куплено: {purchase['title']}. Записал расход {money(float(purchase['price']))} (категория «покупка»).",
        reply_markup=MAIN_MENU,
    )


async def buy_del_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await fin_del_generic(
        update, context, "finance_purchases",
        "Напиши номер покупки: /buy_del 2. Номера видны в /buys",
        "Покупка убрана из планов.", "Не нашёл покупку с таким номером.",
    )


# ===================== ПРОЕКТЫ (ЭТАП 7Б) =====================

PJ_SQL_HINT = "Проверь, что SQL этапа 7Б (stage7b_projects.sql) выполнен в Supabase. Подробности в консоли."

PROJECT_STATUS_NAMES = {
    "idea": "💡 идея",
    "active": "🚀 в работе",
    "paused": "⏸ на паузе",
    "done": "✅ завершён",
}

PROJECT_STATUS_ALIASES = {
    "idea": "idea", "идея": "idea",
    "active": "active", "работа": "active", "в_работе": "active", "активный": "active",
    "paused": "paused", "пауза": "paused",
    "done": "done", "готово": "done", "завершён": "done", "завершен": "done",
}

PROJECT_FIELDS = {
    "title": "title", "название": "title",
    "desc": "description", "описание": "description",
    "goal": "goal", "цель": "goal",
    "status": "status", "статус": "status",
    "deadline": "deadline", "срок": "deadline",
    "result": "result", "результат": "result",
}

ITEM_NAMES = {"stage": "Этап", "task": "Задача", "note": "Заметка"}
ITEM_ADDED = {"stage": "Этап добавлен", "task": "Задача добавлена", "note": "Заметка добавлена"}
ITEM_USAGE = {
    "stage": "Формат: /stage номер_проекта [срок] текст\nНапример: /stage 3 15.12 Собрать прототип",
    "task": "Формат: /task номер_проекта [срок] текст\nНапример: /task 3 20.11 Купить плату",
    "note": "Формат: /note номер_проекта текст\nНапример: /note 3 Решил делать на Arduino",
}

PLAN_PROMPT = """
Ты помогаешь превратить идею проекта в конкретный план действий.

Правила:
- Опирайся только на название, описание, цель и срок проекта ниже. Не выдумывай факты о проекте.
- Предложи от 4 до 7 этапов в логическом порядке. Каждый этап — короткое конкретное действие с понятным результатом (до 80 символов).
- Не повторяй этапы, которые уже есть.
- Если информации слишком мало для плана, не придумывай: верни пустой список этапов и один уточняющий вопрос.
- Пиши по-русски.

Верни ТОЛЬКО JSON без пояснений:
{"stages": ["этап 1", "этап 2"], "question": ""}
Если не хватает данных: {"stages": [], "question": "уточняющий вопрос"}
"""

DATE_RE = re.compile(r"^(\d{1,2})\.(\d{1,2})(?:\.(\d{2}|\d{4}))?$")


def parse_due(text: str, today: dt_date) -> dt_date | None:
    """'15.12' -> ближайшее 15 декабря; '15.12.2026' -> точная дата. Не дата -> None."""
    match = DATE_RE.match(text.strip())
    if not match:
        return None
    day, month = int(match.group(1)), int(match.group(2))
    year_raw = match.group(3)
    try:
        if year_raw:
            year = int(year_raw)
            if year < 100:
                year += 2000
            return dt_date(year, month, day)
        result = dt_date(today.year, month, day)
        if result < today:
            result = dt_date(today.year + 1, month, day)
        return result
    except ValueError:
        return None


def fmt_full_date(iso: str) -> str:
    """'2026-12-15' -> '15.12.2026'"""
    return f"{iso[8:10]}.{iso[5:7]}.{iso[0:4]}"


def deadline_phrase(iso: str | None, status: str, today: dt_date) -> str:
    if not iso:
        return "не задан"
    text = fmt_full_date(iso)
    if status == "done":
        return text
    days = (dt_date.fromisoformat(iso) - today).days
    if days > 0:
        return f"{text} (осталось {days} дн.)"
    if days == 0:
        return f"{text} (срок сегодня)"
    return f"{text} (⚠️ просрочен на {-days} дн.)"


# ---------- база ----------

async def pj_create(user_id: int, title: str) -> dict:
    await ensure_profile(user_id)
    result = await db_call(
        supabase.table("agent_projects")
        .insert({"telegram_id": user_id, "title": title})
        .execute
    )
    return result.data[0]


async def pj_get(user_id: int, project_id: int) -> dict | None:
    result = await db_call(
        supabase.table("agent_projects")
        .select("*")
        .eq("id", project_id)
        .eq("telegram_id", user_id)
        .limit(1)
        .execute
    )
    return (result.data or [None])[0]


async def pj_list(user_id: int) -> list[dict]:
    result = await db_call(
        supabase.table("agent_projects")
        .select("id, title, status, deadline, goal")
        .eq("telegram_id", user_id)
        .order("id")
        .execute
    )
    return result.data or []


async def pj_items(user_id: int, project_id: int) -> list[dict]:
    result = await db_call(
        supabase.table("agent_project_items")
        .select("id, kind, content, due_date, done, created_at")
        .eq("telegram_id", user_id)
        .eq("project_id", project_id)
        .order("id")
        .execute
    )
    return result.data or []


async def pj_progress(user_id: int) -> dict:
    """{project_id: {'stage': [сделано, всего], 'task': [сделано, всего]}} по всем проектам сразу."""
    result = await db_call(
        supabase.table("agent_project_items")
        .select("project_id, kind, done")
        .eq("telegram_id", user_id)
        .in_("kind", ["stage", "task"])
        .limit(5000)
        .execute
    )
    progress: dict = {}
    for row in result.data or []:
        entry = progress.setdefault(row["project_id"], {"stage": [0, 0], "task": [0, 0]})
        entry[row["kind"]][1] += 1
        if row["done"]:
            entry[row["kind"]][0] += 1
    return progress


async def pj_update(user_id: int, project_id: int, **fields) -> bool:
    fields["updated_at"] = datetime.now(dt_timezone.utc).isoformat()
    result = await db_call(
        supabase.table("agent_projects")
        .update(fields)
        .eq("id", project_id)
        .eq("telegram_id", user_id)
        .execute
    )
    return bool(result.data)


async def pj_add_item(
    user_id: int, project_id: int, kind: str, content: str, due: dt_date | None
) -> dict:
    result = await db_call(
        supabase.table("agent_project_items")
        .insert({
            "telegram_id": user_id,
            "project_id": project_id,
            "kind": kind,
            "content": content,
            "due_date": due.isoformat() if due else None,
        })
        .execute
    )
    return result.data[0]


async def pj_item_get(user_id: int, item_id: int) -> dict | None:
    result = await db_call(
        supabase.table("agent_project_items")
        .select("id, project_id, kind, content, done")
        .eq("id", item_id)
        .eq("telegram_id", user_id)
        .limit(1)
        .execute
    )
    return (result.data or [None])[0]


def count_progress(items: list[dict]) -> dict:
    stages = [i for i in items if i["kind"] == "stage"]
    tasks = [i for i in items if i["kind"] == "task"]
    return {
        "stage": [sum(1 for i in stages if i["done"]), len(stages)],
        "task": [sum(1 for i in tasks if i["done"]), len(tasks)],
    }


# ---------- тексты ----------

def build_project_card(project: dict, items: list[dict], today: dt_date) -> str:
    status = project["status"]
    lines = [
        f"📁 Проект №{project['id']}: {project['title']}",
        f"Статус: {PROJECT_STATUS_NAMES.get(status, status)}",
        f"Срок: {deadline_phrase(project.get('deadline'), status, today)}",
        f"Цель: {project.get('goal') or 'не задана'}",
    ]
    if project.get("description"):
        lines.append(f"Описание: {project['description']}")

    def item_line(i: dict) -> str:
        mark = "✅" if i["done"] else "⬜"
        due = ""
        if i.get("due_date"):
            overdue = (not i["done"]) and dt_date.fromisoformat(i["due_date"]) < today
            due = f" — до {fmt_date(i['due_date'])}" + (" ⚠️" if overdue else "")
        return f"{mark} №{i['id']} {i['content']}{due}"

    progress = count_progress(items)
    stages = [i for i in items if i["kind"] == "stage"]
    tasks = [i for i in items if i["kind"] == "task"]
    notes = [i for i in items if i["kind"] == "note"]

    lines += ["", f"Этапы ({progress['stage'][0]}/{progress['stage'][1]}):"]
    lines += [item_line(i) for i in stages] or ["пока нет"]
    lines += ["", f"Задачи ({progress['task'][0]}/{progress['task'][1]}):"]
    lines += [item_line(i) for i in tasks[:20]] or ["пока нет"]
    if len(tasks) > 20:
        lines.append(f"…и ещё {len(tasks) - 20}")
    if notes:
        lines += ["", "Заметки (последние 5):"]
        for n in notes[-5:]:
            lines.append(f"№{n['id']} ({fmt_date(n['created_at'][:10])}) {n['content']}")
    if project.get("result"):
        lines += ["", f"Результат: {project['result']}"]

    return "\n".join(lines)


async def build_projects_block(user_id: int, limit: int = 8) -> str:
    """Короткая сводка по живым проектам для подсказки модели. Пусто, если проектов нет."""
    projects = [p for p in await pj_list(user_id) if p["status"] != "done"][:limit]
    if not projects:
        return ""
    progress = await pj_progress(user_id)
    lines = []
    for p in projects:
        pr = progress.get(p["id"], {"stage": [0, 0], "task": [0, 0]})
        parts = [f"№{p['id']} {p['title']} [{PROJECT_STATUS_NAMES.get(p['status'], p['status'])}]"]
        if p.get("goal"):
            parts.append(f"цель: {clip(p['goal'], 120)}")
        if p.get("deadline"):
            parts.append(f"срок {fmt_full_date(p['deadline'])}")
        if pr["stage"][1]:
            parts.append(f"этапы {pr['stage'][0]}/{pr['stage'][1]}")
        if pr["task"][1]:
            parts.append(f"задачи {pr['task'][0]}/{pr['task'][1]}")
        lines.append("- " + ", ".join(parts))
    return "\n\nПроекты пользователя (из модуля проектов, данные точные):\n" + "\n".join(lines)


# ---------- команды ----------

async def project_new_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await reject_unauthorized(update)
        return

    title = clip(" ".join(context.args or []), 100)
    if not title:
        await update.effective_message.reply_text(
            "Формат: /project_new название\nНапример: /project_new Бот-аналитик канала",
            reply_markup=MAIN_MENU,
        )
        return

    try:
        project = await pj_create(update.effective_user.id, title)
    except Exception:
        logging.exception("Не удалось создать проект")
        await update.effective_message.reply_text(PJ_SQL_HINT, reply_markup=MAIN_MENU)
        return

    n = project["id"]
    await update.effective_message.reply_text(
        f"✅ Проект №{n} создан: {title}\n\nЧто дальше?",
        reply_markup=project_created_keyboard(n),
    )


async def projects_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await reject_unauthorized(update)
        return

    show_all = bool(context.args) and context.args[0].lower() in ("all", "все")
    user_id = update.effective_user.id
    try:
        projects = await pj_list(user_id)
        progress = await pj_progress(user_id)
        today = await user_today(user_id)
    except Exception:
        logging.exception("Не удалось получить проекты")
        await update.effective_message.reply_text(PJ_SQL_HINT, reply_markup=MAIN_MENU)
        return

    if not projects:
        await update.effective_message.reply_text(
            "Проектов пока нет.", reply_markup=projects_keyboard([], False, False)
        )
        return

    shown = [p for p in projects if show_all or p["status"] != "done"]
    done_count = sum(1 for p in projects if p["status"] == "done")

    lines = ["📁 Проекты:", ""]
    for p in shown:
        pr = progress.get(p["id"], {"stage": [0, 0], "task": [0, 0]})
        parts = [f"№{p['id']} {PROJECT_STATUS_NAMES.get(p['status'], p['status'])} {p['title']}"]
        if pr["stage"][1]:
            parts.append(f"этапы {pr['stage'][0]}/{pr['stage'][1]}")
        if p.get("deadline") and p["status"] != "done":
            parts.append("срок " + deadline_phrase(p["deadline"], p["status"], today))
        lines.append(" — ".join(parts))
    if not shown:
        lines.append("Живых проектов нет.")
    if done_count and not show_all:
        lines += ["", f"Завершённых: {done_count}"]
    lines += ["", "Нажми на проект, чтобы открыть его."]
    await reply_long(
        update.effective_message, "\n".join(lines),
        inline=projects_keyboard(shown, done_count > 0, show_all),
    )


async def project_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await reject_unauthorized(update)
        return

    if not context.args or not context.args[0].isdigit():
        await update.effective_message.reply_text(
            "Напиши номер проекта: /project 3. Номера видны в /projects", reply_markup=MAIN_MENU
        )
        return

    user_id = update.effective_user.id
    try:
        project = await pj_get(user_id, int(context.args[0]))
        if not project:
            await update.effective_message.reply_text(
                "Не нашёл проект с таким номером. Список: /projects", reply_markup=MAIN_MENU
            )
            return
        items = await pj_items(user_id, project["id"])
        today = await user_today(user_id)
    except Exception:
        logging.exception("Не удалось открыть проект")
        await update.effective_message.reply_text(PJ_SQL_HINT, reply_markup=MAIN_MENU)
        return

    await reply_long(
        update.effective_message, build_project_card(project, items, today),
        inline=project_card_keyboard(project["id"]),
    )


async def project_set_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await reject_unauthorized(update)
        return

    usage = (
        "Формат: /project_set номер поле значение\n"
        "Поля: title (название), desc (описание), goal (цель), status (статус), "
        "deadline (срок), result (результат)\n\n"
        "Примеры:\n"
        "/project_set 3 goal Запустить бота до января\n"
        "/project_set 3 deadline 31.12.2026\n"
        "/project_set 3 status paused (idea, active, paused, done)\n"
        "Чтобы очистить поле, напиши вместо значения минус: /project_set 3 goal -"
    )
    args = context.args or []
    if len(args) < 3 or not args[0].isdigit() or args[1].lower() not in PROJECT_FIELDS:
        await update.effective_message.reply_text(usage, reply_markup=MAIN_MENU)
        return

    field = PROJECT_FIELDS[args[1].lower()]
    raw_value = " ".join(args[2:]).strip()
    user_id = update.effective_user.id

    try:
        today = await user_today(user_id)
        if field == "title":
            if raw_value == "-":
                await update.effective_message.reply_text(
                    "Название нельзя очистить, можно только заменить.", reply_markup=MAIN_MENU
                )
                return
            value = clip(raw_value, 100)
        elif field == "status":
            value = PROJECT_STATUS_ALIASES.get(raw_value.lower())
            if not value:
                await update.effective_message.reply_text(
                    "Статус бывает: idea (идея), active (в работе), paused (пауза), done (завершён).",
                    reply_markup=MAIN_MENU,
                )
                return
        elif field == "deadline":
            if raw_value == "-":
                value = None
            else:
                parsed = parse_due(raw_value, today)
                if not parsed:
                    await update.effective_message.reply_text(
                        "Дату пиши так: 31.12.2026 или 31.12", reply_markup=MAIN_MENU
                    )
                    return
                value = parsed.isoformat()
        else:
            value = None if raw_value == "-" else clip(raw_value, 1000)

        updated = await pj_update(user_id, int(args[0]), **{field: value})
    except Exception:
        logging.exception("Не удалось изменить проект")
        await update.effective_message.reply_text(PJ_SQL_HINT, reply_markup=MAIN_MENU)
        return

    if not updated:
        await update.effective_message.reply_text(
            "Не нашёл проект с таким номером. Список: /projects", reply_markup=MAIN_MENU
        )
        return

    text = f"✅ Проект №{args[0]} обновлён."
    if field == "status" and value == "done":
        text += "\nМожно записать итог: «✏️ Изменить» → «Результат»."
    await update.effective_message.reply_text(
        text, reply_markup=ikb([[ib("📂 Открыть проект", f"pj:open:{args[0]}")]])
    )


async def pj_item_add_command(update: Update, context: ContextTypes.DEFAULT_TYPE, kind: str):
    if not is_authorized(update):
        await reject_unauthorized(update)
        return

    args = context.args or []
    if len(args) < 2 or not args[0].isdigit():
        await update.effective_message.reply_text(ITEM_USAGE[kind], reply_markup=MAIN_MENU)
        return

    user_id = update.effective_user.id
    rest = args[1:]
    try:
        today = await user_today(user_id)
        due = None
        if kind != "note":
            due = parse_due(rest[0], today)
            if due:
                rest = rest[1:]
        content = clip(" ".join(rest), 300)
        if not content:
            await update.effective_message.reply_text(ITEM_USAGE[kind], reply_markup=MAIN_MENU)
            return

        project = await pj_get(user_id, int(args[0]))
        if not project:
            await update.effective_message.reply_text(
                "Не нашёл проект с таким номером. Список: /projects", reply_markup=MAIN_MENU
            )
            return
        item = await pj_add_item(user_id, project["id"], kind, content, due)
    except Exception:
        logging.exception("Не удалось добавить запись в проект")
        await update.effective_message.reply_text(PJ_SQL_HINT, reply_markup=MAIN_MENU)
        return

    due_text = f", срок {fmt_full_date(due.isoformat())}" if due else ""
    await update.effective_message.reply_text(
        f"✅ {ITEM_ADDED[kind]} в «{project['title']}»: №{item['id']}{due_text}",
        reply_markup=item_after_keyboard(kind, project["id"]),
    )


async def stage_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await pj_item_add_command(update, context, "stage")


async def task_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await pj_item_add_command(update, context, "task")


async def note_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await pj_item_add_command(update, context, "note")


async def pj_done_generic(update: Update, context: ContextTypes.DEFAULT_TYPE, done: bool):
    if not is_authorized(update):
        await reject_unauthorized(update)
        return

    command = "/p_done" if done else "/p_undo"
    if not context.args or not context.args[0].isdigit():
        await update.effective_message.reply_text(
            f"Напиши номер этапа или задачи: {command} 15. Номера видны в карточке проекта (/project номер)",
            reply_markup=MAIN_MENU,
        )
        return

    user_id = update.effective_user.id
    try:
        item = await pj_item_get(user_id, int(context.args[0]))
        if not item:
            await update.effective_message.reply_text(
                "Не нашёл запись с таким номером. Номера видны в /project номер", reply_markup=MAIN_MENU
            )
            return
        if item["kind"] == "note":
            await update.effective_message.reply_text(
                "Заметку нельзя отметить выполненной. Удалить её: /p_del номер", reply_markup=MAIN_MENU
            )
            return

        await db_call(
            supabase.table("agent_project_items")
            .update({"done": done})
            .eq("id", item["id"])
            .eq("telegram_id", user_id)
            .execute
        )
        items = await pj_items(user_id, item["project_id"])
        progress = count_progress(items)
        project = await pj_get(user_id, item["project_id"])
    except Exception:
        logging.exception("Не удалось отметить запись проекта")
        await update.effective_message.reply_text(PJ_SQL_HINT, reply_markup=MAIN_MENU)
        return

    mark = "✅ Выполнено" if done else "↩️ Возвращено в работу"
    text = (
        f"{mark}: {item['content']}\n"
        f"Этапы {progress['stage'][0]}/{progress['stage'][1]}, "
        f"задачи {progress['task'][0]}/{progress['task'][1]}."
    )
    stage_total = progress["stage"][1]
    if done and stage_total and progress["stage"][0] == stage_total and project and project["status"] != "done":
        text += f"\n🎉 Все этапы закрыты. Завершить проект: /project_set {item['project_id']} status done"
    await update.effective_message.reply_text(text, reply_markup=MAIN_MENU)


async def p_done_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await pj_done_generic(update, context, True)


async def p_undo_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await pj_done_generic(update, context, False)


async def p_del_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await reject_unauthorized(update)
        return

    if not context.args or not context.args[0].isdigit():
        await update.effective_message.reply_text(
            "Напиши номер: /p_del 15. Номера видны в /project номер", reply_markup=MAIN_MENU
        )
        return

    try:
        result = await db_call(
            supabase.table("agent_project_items")
            .delete()
            .eq("id", int(context.args[0]))
            .eq("telegram_id", update.effective_user.id)
            .execute
        )
    except Exception:
        logging.exception("Не удалось удалить запись проекта")
        await update.effective_message.reply_text(PJ_SQL_HINT, reply_markup=MAIN_MENU)
        return

    text = "Удалено." if result.data else "Не нашёл запись с таким номером."
    await update.effective_message.reply_text(text, reply_markup=MAIN_MENU)


async def project_del_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await reject_unauthorized(update)
        return

    args = context.args or []
    if not args or not args[0].isdigit():
        await update.effective_message.reply_text(
            "Напиши номер проекта: /project_del 3 да", reply_markup=MAIN_MENU
        )
        return

    if len(args) < 2 or args[1].lower() != "да":
        await update.effective_message.reply_text(
            f"Проект удалится вместе со всеми этапами, задачами, заметками и прикреплёнными материалами. "
            f"Финансовые операции сохранятся, но потеряют привязку к проекту. Если уверен, напиши: /project_del {args[0]} да",
            reply_markup=MAIN_MENU,
        )
        return

    try:
        result = await db_call(
            supabase.table("agent_projects")
            .delete()
            .eq("id", int(args[0]))
            .eq("telegram_id", update.effective_user.id)
            .execute
        )
    except Exception:
        logging.exception("Не удалось удалить проект")
        await update.effective_message.reply_text(PJ_SQL_HINT, reply_markup=MAIN_MENU)
        return

    text = "Проект удалён." if result.data else "Не нашёл проект с таким номером."
    await update.effective_message.reply_text(text, reply_markup=MAIN_MENU)


# ---------- план от ИИ ----------

PLAN_CONFIRM = InlineKeyboardMarkup([[
    InlineKeyboardButton("✅ Сохранить этапы", callback_data="plan:save"),
    InlineKeyboardButton("Отмена", callback_data="plan:cancel"),
]])


async def project_plan_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await reject_unauthorized(update)
        return

    if not context.args or not context.args[0].isdigit():
        await update.effective_message.reply_text(
            "Напиши номер проекта: /project_plan 3. Номера видны в /projects", reply_markup=MAIN_MENU
        )
        return

    user_id = update.effective_user.id
    await update.effective_message.chat.send_action("typing")
    try:
        project = await pj_get(user_id, int(context.args[0]))
        if not project:
            await update.effective_message.reply_text(
                "Не нашёл проект с таким номером. Список: /projects", reply_markup=MAIN_MENU
            )
            return
        items = await pj_items(user_id, project["id"])
        today = await user_today(user_id)
    except Exception:
        logging.exception("Не удалось загрузить проект для плана")
        await update.effective_message.reply_text(PJ_SQL_HINT, reply_markup=MAIN_MENU)
        return

    existing = [i["content"] for i in items if i["kind"] == "stage"]
    user_content = (
        f"Название: {project['title']}\n"
        f"Описание: {project.get('description') or 'не задано'}\n"
        f"Цель: {project.get('goal') or 'не задана'}\n"
        f"Срок: {deadline_phrase(project.get('deadline'), project['status'], today)}\n"
        "Уже есть этапы:\n" + ("\n".join(f"- {e}" for e in existing) or "(нет)")
    )

    try:
        response = await ai.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": PLAN_PROMPT},
                {"role": "user", "content": user_content},
            ],
            temperature=0.4,
            max_tokens=1500,
            response_format={"type": "json_object"},
        )
        raw = (response.choices[0].message.content or "").strip()
        start, end = raw.find("{"), raw.rfind("}")
        data = json.loads(raw[start:end + 1])
    except Exception:
        logging.exception("Не удалось получить план от модели")
        await update.effective_message.reply_text(
            "Не получилось составить план. Возможно, Groq временно ограничил запросы: "
            "попробуй через минуту. Подробности в консоли.",
            reply_markup=MAIN_MENU,
        )
        return

    stages = [clip(str(s), 100) for s in (data.get("stages") or []) if str(s).strip()][:7]
    if not stages:
        question = str(data.get("question") or "").strip()
        n = project["id"]
        await update.effective_message.reply_text(
            "Чтобы составить план, мне не хватает данных."
            + (f"\nВопрос: {question}" if question else "")
            + "\n\nДобавь описание или цель: открой проект и нажми «✏️ Изменить», потом повтори «🤖 План от ИИ».",
            reply_markup=ikb([[ib("📂 Открыть проект", f"pj:open:{n}")]]),
        )
        return

    pending = context.application.bot_data.setdefault("pending_plans", {})
    pending[user_id] = {"project_id": project["id"], "stages": stages}

    lines = [f"📝 Предлагаю этапы для «{project['title']}»:", ""]
    lines += [f"{i}. {s}" for i, s in enumerate(stages, 1)]
    lines += ["", "Сохранить их в проект? Это только предложение, пока ты не нажал кнопку, ничего не изменится."]
    await update.effective_message.reply_text("\n".join(lines), reply_markup=PLAN_CONFIRM)


async def plan_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Кнопки «Сохранить этапы» / «Отмена» под планом от ИИ."""
    query = update.callback_query
    if not is_authorized(update):
        await query.answer("Доступ запрещён.", show_alert=True)
        return

    user_id = update.effective_user.id
    action = (query.data or "").split(":", 1)[-1]
    pending = context.application.bot_data.get("pending_plans", {})
    plan = pending.pop(user_id, None)

    if action == "cancel":
        await query.answer("Отменено")
        await query.edit_message_reply_markup(reply_markup=None)
        return

    if not plan:
        await query.answer("План устарел (бот перезапускался). Запусти /project_plan ещё раз.", show_alert=True)
        await query.edit_message_reply_markup(reply_markup=None)
        return

    try:
        for stage in plan["stages"]:
            await pj_add_item(user_id, plan["project_id"], "stage", stage, None)
    except Exception:
        logging.exception("Не удалось сохранить этапы плана")
        await query.answer("Не удалось сохранить. Подробности в консоли.", show_alert=True)
        return

    await query.answer("Сохранено")
    await query.edit_message_reply_markup(reply_markup=None)
    await query.message.reply_text(
        f"✅ Этапов сохранено: {len(plan['stages'])}. Открыть: /project {plan['project_id']}",
        reply_markup=MAIN_MENU,
    )


# ---------- естественный язык и материалы проектов ----------

PROJECT_AGENT_PROMPT = """
Ты — диспетчер проектных действий личного Telegram-агента.
Верни только JSON с полями:
{
  "action": "create_project|add_stage|add_task|add_note|update_project|project_query",
  "project_title": "точное название из списка или пустая строка",
  "title": "название нового проекта",
  "description": "описание нового проекта",
  "goal": "цель нового проекта",
  "content": "текст этапа, задачи или заметки",
  "due_date": "дата ГГГГ-ММ-ДД или пустая строка",
  "field": "description|goal|status|deadline|result",
  "value": "новое значение поля",
  "clear": false,
  "confidence": 0.0,
  "question": "уточняющий вопрос"
}
Правила:
- Список проектов ниже — единственный источник допустимых названий. Не выдумывай project_id и не подбирай похожий проект.
- Создание нового проекта разрешено только при прямой просьбе создать проект.
- Для добавления этапа/задачи/заметки укажи точное project_title из списка.
- Вопросы о статусе и прогрессе — project_query.
- Изменение полей проекта — update_project, но только если пользователь явно попросил.
- Устанавливай clear=true только если пользователь явно попросил очистить поле; иначе верни новое value.
- Удаление проекта, этапов, задач и материалов этим агентом запрещено.
- Не превращай идеи, планы и гипотетические фразы в записи.
- Если не хватает названия проекта, содержания или даты, верни clarification.
- confidence от 0 до 1; ниже 0.75 действие не будет выполнено.
- Пиши по-русски. Никаких действий в БД не выполняй.
""".strip()


async def prepare_project_action(update: Update, context: ContextTypes.DEFAULT_TYPE, user_message: str):
    user_id = update.effective_user.id
    try:
        projects = await pj_list(user_id)
        today = await user_today(user_id)
        source = {
            "today": today.isoformat(),
            "projects": [
                {"id": p["id"], "title": p["title"], "status": p["status"],
                 "goal": p.get("goal"), "deadline": p.get("deadline")}
                for p in projects
            ],
            "message": user_message,
        }
        response = await ai.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": PROJECT_AGENT_PROMPT},
                {"role": "user", "content": json.dumps(source, ensure_ascii=False)},
            ],
            temperature=0.1,
            max_tokens=900,
            response_format={"type": "json_object"},
        )
        raw = (response.choices[0].message.content or "").strip()
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end < start:
            raise ValueError("Модель не вернула JSON")
        draft = parse_project_response(raw[start:end + 1], today, projects)
    except ValueError as error:
        await update.effective_message.reply_text(
            f"Не удалось безопасно разобрать проектное действие: {error}. Переформулируй запрос.",
            reply_markup=MAIN_MENU,
        )
        return
    except Exception:
        logging.exception("Не удалось подготовить проектное действие")
        await update.effective_message.reply_text(
            "Не получилось обработать запрос к проекту. Ничего не изменено; попробуй ещё раз.",
            reply_markup=MAIN_MENU,
        )
        return

    if draft.get("clarification"):
        await update.effective_message.reply_text(draft["clarification"], reply_markup=MAIN_MENU)
        return

    action = draft["action"]
    if action == "project_query":
        if not draft.get("project_id"):
            await projects_command(update, ArgsContext(context, []))
            return
        project = await pj_get(user_id, draft["project_id"])
        if not project:
            await update.effective_message.reply_text("Проект больше не найден. Обнови список: /projects")
            return
        items = await pj_items(user_id, project["id"])
        await reply_long(
            update.effective_message,
            build_project_card(project, items, today),
            inline=project_card_keyboard(project["id"]),
        )
        return

    context.user_data["project_action_pending"] = draft
    if action == "create_project":
        preview = f"Создать проект «{draft['title']}»?"
        if draft.get("goal"):
            preview += f"\nЦель: {draft['goal']}"
        if draft.get("description"):
            preview += f"\nОписание: {draft['description']}"
    elif action.startswith("add_"):
        labels = {"add_stage": "этап", "add_task": "задачу", "add_note": "заметку"}
        preview = (
            f"Добавить {labels[action]} в проект «{draft['project_title']}»?\n"
            f"{draft['content']}"
        )
        if draft.get("due_date"):
            preview += f"\nСрок: {fmt_full_date(draft['due_date'].isoformat())}"
    else:
        field_names = {
            "description": "описание", "goal": "цель", "status": "статус",
            "deadline": "срок", "result": "результат",
        }
        preview = (
            f"Изменить {field_names[draft['field']]} проекта «{draft['project_title']}»?\n"
            f"Новое значение: {draft.get('value') or 'очистить поле'}"
        )
    await update.effective_message.reply_text(
        "Проверь черновик. До подтверждения ничего не записано.\n\n" + preview,
        reply_markup=ikb([[
            ib("✅ Подтвердить", "pjact:confirm"),
            ib("❌ Отмена", "pjact:cancel"),
        ]]),
    )


async def project_action_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_authorized(update):
        await query.answer("Доступ запрещён.", show_alert=True)
        return
    action = (query.data or "").split(":", 1)[-1]
    draft = context.user_data.get("project_action_pending")
    if draft and draft.get("_processing"):
        await query.answer("Действие уже обрабатывается.", show_alert=True)
        return
    if action == "cancel":
        context.user_data.pop("project_action_pending", None)
        await query.answer("Отменено")
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text("Отменено. Данные проекта не изменены.", reply_markup=MAIN_MENU)
        return
    if action != "confirm" or not draft:
        await query.answer("Черновик устарел. Отправь запрос ещё раз.", show_alert=True)
        return

    draft["_processing"] = True
    user_id = update.effective_user.id
    try:
        kind = draft["action"]
        if kind == "create_project":
            current = await pj_list(user_id)
            if any(p["title"].casefold().strip() == draft["title"].casefold().strip() for p in current):
                raise ValueError("Такой проект уже существует")
            project = await pj_create(user_id, draft["title"])
            fields = {}
            if draft.get("description"):
                fields["description"] = draft["description"]
            if draft.get("goal"):
                fields["goal"] = draft["goal"]
            if fields:
                await pj_update(user_id, project["id"], **fields)
            result_text = f"Проект №{project['id']} «{project['title']}» создан."
            open_id = project["id"]
        elif kind in {"add_stage", "add_task", "add_note"}:
            project = await pj_get(user_id, int(draft["project_id"]))
            if not project:
                raise ValueError("Проект не найден или недоступен")
            item_kind = kind.removeprefix("add_")
            item = await pj_add_item(
                user_id, project["id"], item_kind, draft["content"], draft.get("due_date")
            )
            result_text = f"Добавлено в «{project['title']}»: запись №{item['id']}."
            open_id = project["id"]
        elif kind == "update_project":
            project = await pj_get(user_id, int(draft["project_id"]))
            if not project:
                raise ValueError("Проект не найден или недоступен")
            if not await pj_update(user_id, project["id"], **{draft["field"]: draft.get("value")}):
                raise ValueError("База не подтвердила изменение")
            result_text = f"Проект «{project['title']}» обновлён."
            open_id = project["id"]
        else:
            raise ValueError("Неизвестное действие")
    except Exception as error:
        draft.pop("_processing", None)
        logging.exception("Не удалось сохранить проектное действие")
        await query.answer("Не удалось сохранить. Черновик оставлен.", show_alert=True)
        await query.message.reply_text(
            f"Запись не подтверждена базой: {error}. Попробуй повторить действие.",
            reply_markup=MAIN_MENU,
        )
        return

    context.user_data.pop("project_action_pending", None)
    await query.answer("Сохранено")
    await query.edit_message_reply_markup(reply_markup=None)
    await query.message.reply_text(
        "✅ " + result_text,
        reply_markup=ikb([[ib("📂 Открыть проект", f"pj:open:{open_id}")]]),
    )


async def project_asset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await reject_unauthorized(update)
        return
    if not context.args or not context.args[0].isdigit():
        await update.effective_message.reply_text("Формат: /asset номер_проекта. Затем отправь фото или документ.")
        return
    user_id = update.effective_user.id
    project_id = int(context.args[0])
    project = await pj_get(user_id, project_id)
    if not project:
        await update.effective_message.reply_text("Проект не найден. Проверь /projects.")
        return
    context.user_data["project_asset_target"] = project_id
    await update.effective_message.reply_text(
        f"Отправь одним следующим сообщением фото или документ для проекта «{project['title']}». "
        "Файл будет привязан к проекту; /cancel отменит ожидание.",
        reply_markup=CANCEL_KB,
    )


async def project_assets_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await reject_unauthorized(update)
        return
    if not context.args or not context.args[0].isdigit():
        await update.effective_message.reply_text("Формат: /assets номер_проекта")
        return
    user_id = update.effective_user.id
    project_id = int(context.args[0])
    project = await pj_get(user_id, project_id)
    if not project:
        await update.effective_message.reply_text("Проект не найден. Проверь /projects.")
        return
    result = await db_call(
        supabase.table("agent_project_assets")
        .select("id, telegram_file_id, media_type, file_name, caption, created_at")
        .eq("telegram_id", user_id)
        .eq("project_id", project_id)
        .order("id")
        .limit(20)
        .execute
    )
    assets = result.data or []
    if not assets:
        await update.effective_message.reply_text(f"В проекте «{project['title']}» пока нет материалов.")
        return
    await update.effective_message.reply_text(
        f"Материалы проекта «{project['title']}» — последние {len(assets)}:"
    )
    for asset in assets:
        caption = f"Материал №{asset['id']}: {asset.get('file_name') or asset.get('media_type')}"
        if asset.get("caption"):
            caption += f"\n{clip(asset['caption'], 700)}"
        try:
            if asset["media_type"] == "photo":
                await update.effective_message.reply_photo(asset["telegram_file_id"], caption=caption[:1000])
            else:
                await update.effective_message.reply_document(asset["telegram_file_id"], caption=caption[:1000])
        except Exception:
            logging.exception("Не удалось повторно отправить материал проекта %s", asset["id"])
            await update.effective_message.reply_text(caption)


async def handle_project_asset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await reject_unauthorized(update)
        return
    user_id = update.effective_user.id
    project_id = context.user_data.get("project_asset_target")
    if not project_id:
        await update.effective_message.reply_text(
            "Чтобы прикрепить материал к проекту, сначала напиши /asset номер_проекта."
        )
        return
    project = await pj_get(user_id, int(project_id))
    if not project:
        context.user_data.pop("project_asset_target", None)
        await update.effective_message.reply_text("Проект не найден. Ожидание файла отменено.")
        return

    message = update.effective_message
    if message.photo:
        media = message.photo[-1]
        media_type = "photo"
        file_id = media.file_id
        unique_id = media.file_unique_id
        file_name = None
        mime_type = "image/jpeg"
    elif message.document:
        media = message.document
        media_type = "document"
        file_id = media.file_id
        unique_id = media.file_unique_id
        file_name = media.file_name
        mime_type = media.mime_type
    else:
        return

    try:
        duplicate = await db_call(
            supabase.table("agent_project_assets")
            .select("id")
            .eq("telegram_id", user_id)
            .eq("project_id", int(project_id))
            .eq("telegram_file_unique_id", unique_id)
            .limit(1)
            .execute
        )
        if duplicate.data:
            context.user_data.pop("project_asset_target", None)
            await update.effective_message.reply_text("Этот файл уже прикреплён к проекту.")
            return
        result = await db_call(
            supabase.table("agent_project_assets")
            .insert({
                "telegram_id": user_id,
                "project_id": int(project_id),
                "telegram_file_id": file_id,
                "telegram_file_unique_id": unique_id,
                "media_type": media_type,
                "file_name": file_name,
                "mime_type": mime_type,
                "caption": clip(message.caption or "", 1000) or None,
                "source_message_id": message.message_id,
            })
            .execute
        )
        if not result.data:
            raise RuntimeError("Supabase не вернул созданную запись")
    except Exception:
        logging.exception("Не удалось сохранить материал проекта")
        await update.effective_message.reply_text("Не удалось сохранить материал. Ожидание оставлено; попробуй отправить файл ещё раз.")
        return

    context.user_data.pop("project_asset_target", None)
    await update.effective_message.reply_text(
        f"✅ Материал сохранён в проект «{project['title']}». Посмотреть: /assets {project_id}",
        reply_markup=MAIN_MENU,
    )


# ===================== ОТВЕТ МОДЕЛИ =====================

async def ask_ai(user_id: int, message: str, extra_system: str = "") -> str:
    try:
        history = await load_history(user_id)

        # Сохраняем новое сообщение до запроса к модели.
        await save_message(user_id, "user", message)

        # Память подгружаем отдельно: если с ней беда, бот всё равно ответит.
        try:
            memories = await load_memories(user_id)
        except Exception:
            logging.exception("Не удалось загрузить память")
            memories = []

        # Сводка по проектам: если таблиц ещё нет или сбой, бот всё равно ответит.
        try:
            projects_block = await build_projects_block(user_id)
        except Exception:
            logging.exception("Не удалось загрузить проекты для ответа")
            projects_block = ""

        todoist_block = ""
        active_tasks = []
        task_tools = []
        if todoist_enabled() and is_todoist_intent(message):
            try:
                settings = await load_settings(user_id)
                tz = ZoneInfo(settings["timezone"])
                snapshot = await todoist_snapshot(tz)
                # Для выбора и переноса/завершения доступны активные задачи,
                # а не только задачи на сегодня.
                active_tasks = await todoist_active_tasks()
                todoist_block = (
                    "\n\nАКТУАЛЬНЫЕ ДАННЫЕ TODOIST (источник истины):\n"
                    + build_tasks_text(snapshot, "Просрочено / сегодня / выполнено сегодня:")
                    + "\n\nДругие активные задачи (ID | название | срок):\n"
                    + "\n".join(
                        f"- {t.get('id')} | {t.get('content')} | "
                        f"{(t.get('due') or {}).get('string') or (t.get('due') or {}).get('date') or 'без срока'}"
                        for t in active_tasks[:100]
                    )
                )
                task_tools = build_todoist_tools(active_tasks)
            except Exception:
                logging.exception("Не удалось загрузить Todoist в обычный диалог")
                todoist_block = (
                    "\n\nTodoist сейчас не удалось прочитать. Не утверждай, что видел "
                    "актуальные задачи, и не предлагай изменение задач как уже выполненное."
                )

        system_prompt = (
            SYSTEM_PROMPT + build_memory_block(memories) + projects_block
            + extra_system + todoist_block
        )
        if task_tools:
            system_prompt += (
                "\n\nПРАВИЛА TODOIST: Используй инструменты только когда пользователь "
                "ясно просит добавить задачу, перенести срок или отметить конкретную задачу "
                "выполненной. Для простого составления расписания не изменяй Todoist. "
                "Перед любым изменением вызови ровно один подходящий инструмент: бот покажет "
                "пользователю точное действие и попросит подтверждение Да/Нет. Никогда не "
                "говори, что действие уже выполнено до подтверждения. Для выбора задачи "
                "используй только переданные ID. Если неясно, какую задачу менять, задай вопрос."
            )

        messages = [
            {"role": "system", "content": system_prompt},
            *history,
            {"role": "user", "content": message},
        ]

        request_args = {
            "model": GROQ_MODEL,
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": 1000,
        }
        if task_tools:
            request_args["tools"] = task_tools
            request_args["tool_choice"] = "auto"

        response = await ai.chat.completions.create(**request_args)
        assistant_message = response.choices[0].message

        if getattr(assistant_message, "tool_calls", None):
            for tool_call in assistant_message.tool_calls:
                action = todoist_action_from_tool_call(tool_call, active_tasks)
                if action:
                    answer = todoist_action_prompt(action)
                    await save_message(user_id, "assistant", "Предложено действие Todoist; ожидает подтверждения пользователя.")
                    return answer

        answer = assistant_message.content or "Не удалось получить текстовый ответ."
        await save_message(user_id, "assistant", answer)
        return answer

    except Exception:
        logging.exception("Ошибка при обработке сообщения или работе с Supabase/Groq")
        return (
            "Не получилось обработать сообщение. "
            "Проверь подключение к Supabase и Groq. Подробности есть в консоли."
        )


# ===================== КОМАНДЫ И КНОПКИ =====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await reject_unauthorized(update)
        return

    try:
        user = update.effective_user
        await ensure_profile(user.id, user.full_name)
        await update.effective_message.reply_text(
            "Привет! Я твой личный ИИ-агент.\n\n"
            "Моя память хранится в Supabase и не пропадёт после перезапуска. "
            "Важное из твоих сообщений я запоминаю сам.\n\n"
            "Три раза в день я сам напишу тебе с вопросами. "
            "Время можно посмотреть кнопкой «Расписание».\n\n"
            "Кнопки меню — внизу экрана.",
            reply_markup=MAIN_MENU,
        )
    except Exception:
        logging.exception("Не удалось создать/обновить профиль в Supabase")
        await update.effective_message.reply_text(
            "Не удалось подключиться к Supabase. Проверь настройки в .env и консоль.",
            reply_markup=MAIN_MENU,
        )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await reject_unauthorized(update)
        return

    await update.effective_message.reply_text(HELP_TEXT, reply_markup=MAIN_MENU)


async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await reject_unauthorized(update)
        return

    try:
        await clear_history(update.effective_user.id)
        await update.effective_message.reply_text(
            "История диалога удалена. Долговременная память и записи опросов остались.",
            reply_markup=MAIN_MENU,
        )
    except Exception:
        logging.exception("Не удалось очистить историю в Supabase")
        await update.effective_message.reply_text(
            "Не удалось очистить историю. Проверь консоль.",
            reply_markup=MAIN_MENU,
        )


async def memory_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await reject_unauthorized(update)
        return

    try:
        memories = await load_memories(update.effective_user.id)
        if not memories:
            await update.effective_message.reply_text(
                "Память пока пуста.", reply_markup=MAIN_MENU
            )
            return

        lines = [
            f"{m['id']}. [{CATEGORY_NAMES.get(m['category'], m['category'])}] {m['content']}"
            for m in memories
        ]
        text = "Что я помню:\n\n" + "\n".join(lines)
        await update.effective_message.reply_text(text[:4000], reply_markup=MEMORY_KB)
    except Exception:
        logging.exception("Не удалось прочитать память")
        await update.effective_message.reply_text(
            "Не удалось прочитать память. Проверь консоль.", reply_markup=MAIN_MENU
        )


async def forget_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await reject_unauthorized(update)
        return

    if not context.args or not context.args[0].isdigit():
        await update.effective_message.reply_text(
            "Напиши номер записи, например: /forget 3\n"
            "Номера смотри по кнопке «Память».",
            reply_markup=MAIN_MENU,
        )
        return

    try:
        deleted = await delete_memory(update.effective_user.id, int(context.args[0]))
        if deleted:
            await update.effective_message.reply_text(
                "Запись удалена.", reply_markup=MAIN_MENU
            )
        else:
            await update.effective_message.reply_text(
                "Записи с таким номером нет.", reply_markup=MAIN_MENU
            )
    except Exception:
        logging.exception("Не удалось удалить запись памяти")
        await update.effective_message.reply_text(
            "Не удалось удалить запись. Проверь консоль.", reply_markup=MAIN_MENU
        )


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await reject_unauthorized(update)
        return

    await update.effective_message.chat.send_action("typing")
    try:
        text = await build_stats_text()
        await update.effective_message.reply_text(text, reply_markup=MAIN_MENU)
    except Exception:
        logging.exception("Не удалось собрать статистику")
        await update.effective_message.reply_text(
            "Не удалось собрать статистику. Проверь консоль.", reply_markup=MAIN_MENU
        )


async def schedule_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await reject_unauthorized(update)
        return

    try:
        s = await load_settings(update.effective_user.id)
    except Exception:
        logging.exception("Не удалось прочитать настройки расписания")
        await update.effective_message.reply_text(
            "Не удалось прочитать настройки. Проверь, что SQL для этапов 4 и 5 выполнен в Supabase.",
            reply_markup=MAIN_MENU,
        )
        return

    state = "включены" if s["enabled"] else "выключены"
    await update.effective_message.reply_text(
        f"⏰ Ежедневные опросы: {state}\n"
        f"Часовой пояс: {s['timezone']}\n\n"
        f"☀️ Утро: {s['morning']}\n"
        f"🌤 День: {s['noon']}\n"
        f"🌙 Вечер: {s['evening']}\n"
        f"📈 Недельный отчёт (воскресенье): {s['weekly']}\n\n"
        "Нажми кнопку, чтобы что-то изменить.",
        reply_markup=SCHEDULE_KB,
    )


async def times_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await reject_unauthorized(update)
        return

    usage = (
        "Напиши времена через пробел: утро, день, вечер и (по желанию) недельный отчёт.\n"
        "Например: /times 07:30 13:00 20:00 20:30\n"
        "Если четвёртое время не указать, время недельного отчёта не изменится."
    )
    if len(context.args) not in (3, 4):
        await update.effective_message.reply_text(usage, reply_markup=MAIN_MENU)
        return

    times = [normalize_time(arg) for arg in context.args]
    if None in times:
        await update.effective_message.reply_text(
            "Не понял время. Нужен формат ЧЧ:ММ, например 07:30.\n" + usage,
            reply_markup=MAIN_MENU,
        )
        return

    try:
        user_id = update.effective_user.id
        fields = {
            "morning_time": times[0],
            "noon_time": times[1],
            "evening_time": times[2],
        }
        if len(times) == 4:
            fields["weekly_time"] = times[3]
        await update_settings(user_id, **fields)

        settings = await load_settings(user_id)
        schedule_jobs(context.application, settings)
        await update.effective_message.reply_text(
            f"Готово. Опросы: утро {settings['morning']}, день {settings['noon']}, "
            f"вечер {settings['evening']}. Недельный отчёт (воскресенье): {settings['weekly']}.",
            reply_markup=MAIN_MENU,
        )
    except Exception:
        logging.exception("Не удалось сохранить время опросов")
        await update.effective_message.reply_text(
            "Не удалось сохранить время. Проверь консоль.", reply_markup=MAIN_MENU
        )


async def timezone_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await reject_unauthorized(update)
        return

    examples = (
        "Примеры: Europe/Moscow, Asia/Yekaterinburg, Asia/Omsk, "
        "Asia/Novosibirsk, Asia/Krasnoyarsk"
    )
    if len(context.args) != 1:
        await update.effective_message.reply_text(
            "Напиши часовой пояс, например: /timezone Europe/Moscow\n" + examples,
            reply_markup=MAIN_MENU,
        )
        return

    name = context.args[0]
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        await update.effective_message.reply_text(
            "Такой часовой пояс не нашёл.\n" + examples
            + "\n\nЕсли пояса вообще не находятся, выполни в терминале: pip install tzdata",
            reply_markup=MAIN_MENU,
        )
        return

    try:
        user_id = update.effective_user.id
        await update_settings(user_id, timezone=name)
        schedule_jobs(context.application, await load_settings(user_id))
        await update.effective_message.reply_text(
            f"Часовой пояс: {name}. Опросы перенастроены.", reply_markup=MAIN_MENU
        )
    except Exception:
        logging.exception("Не удалось сохранить часовой пояс")
        await update.effective_message.reply_text(
            "Не удалось сохранить часовой пояс. Проверь консоль.", reply_markup=MAIN_MENU
        )


async def surveys_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await reject_unauthorized(update)
        return

    arg = context.args[0].lower() if context.args else ""
    if arg not in ("on", "off"):
        await update.effective_message.reply_text(
            "Напиши /surveys on, чтобы включить опросы, или /surveys off, чтобы выключить.",
            reply_markup=MAIN_MENU,
        )
        return

    try:
        user_id = update.effective_user.id
        await update_settings(user_id, surveys_enabled=(arg == "on"))
        schedule_jobs(context.application, await load_settings(user_id))
        text = "Опросы включены." if arg == "on" else "Опросы выключены."
        await update.effective_message.reply_text(text, reply_markup=MAIN_MENU)
    except Exception:
        logging.exception("Не удалось переключить опросы")
        await update.effective_message.reply_text(
            "Не удалось переключить опросы. Проверь консоль.", reply_markup=MAIN_MENU
        )


async def survey_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ручной запуск опроса — чтобы проверить, не дожидаясь расписания."""
    if not is_authorized(update):
        await reject_unauthorized(update)
        return

    kind = context.args[0].lower() if context.args else ""
    if kind not in SURVEYS:
        await update.effective_message.reply_text(
            "Напиши, какой опрос запустить: /survey morning, /survey noon или /survey evening",
            reply_markup=MAIN_MENU,
        )
        return

    try:
        await deliver_survey(context.bot, kind)
    except Exception:
        logging.exception("Не удалось запустить опрос вручную")
        await update.effective_message.reply_text(
            "Не удалось запустить опрос. Проверь, что SQL для этапа 4 выполнен. Подробности в консоли.",
            reply_markup=MAIN_MENU,
        )


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await reject_unauthorized(update)
        return

    try:
        await db_call(
            supabase.table("profiles")
            .select("telegram_id")
            .eq("telegram_id", update.effective_user.id)
            .limit(1)
            .execute
        )
        supabase_status = "подключён"
    except Exception:
        logging.exception("Проверка Supabase не удалась")
        supabase_status = "ошибка подключения"

    try:
        memories = await load_memories(update.effective_user.id, limit=1000)
        memory_status = f"{len(memories)} активных записей"
    except Exception:
        logging.exception("Проверка памяти не удалась")
        memory_status = "ошибка чтения"

    if not todoist_enabled():
        todoist_status = "не настроен"
    else:
        try:
            await todoist_get("projects", {"limit": 1})
            todoist_status = "подключён"
        except Exception:
            logging.exception("Проверка Todoist не удалась")
            todoist_status = "ошибка (проверь токен)"

    jobs = 0
    if context.application.job_queue is not None:
        for name in [f"survey_{k}" for k in SURVEY_ORDER] + ["report_week"]:
            jobs += len(context.application.job_queue.get_jobs_by_name(name))
    survey_status = f"{jobs} из 4 задач назначены" if jobs else "не назначены"

    await update.effective_message.reply_text(
        "Состояние агента:\n"
        "Telegram: работает.\n"
        f"Groq: настроена модель {GROQ_MODEL}.\n"
        f"Supabase: {supabase_status}.\n"
        f"Память: {memory_status}.\n"
        f"Опросы: {survey_status}.\n"
        f"Todoist: {todoist_status}.\n"
        "Доступ: только разрешённый Telegram ID.",
        reply_markup=STATUS_KB,
    )


async def handle_menu_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Нажатия на кнопки внизу — это обычные текстовые сообщения, ловим их по тексту."""
    if not is_authorized(update):
        await reject_unauthorized(update)
        return

    text = update.effective_message.text

    if text == BTN_MEMORY:
        await memory_command(update, context)
    elif text == BTN_STATS:
        await stats_command(update, context)
    elif text == BTN_TASKS:
        await tasks_command(update, context)
    elif text == BTN_FINANCE:
        await finance_command(update, context)
    elif text == BTN_PROJECTS:
        await projects_command(update, context)
    elif text == BTN_REPORT:
        await reports_menu_command(update, context)
    elif text == BTN_SCHEDULE:
        await schedule_command(update, context)
    elif text == BTN_STATUS:
        await status(update, context)
    elif text == BTN_HELP:
        await help_command(update, context)


async def answer_finance_question(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    message: str,
):
    """Read-only natural-language finance query. All totals are calculated from Supabase rows."""
    user_id = update.effective_user.id
    try:
        today = await user_today(user_id)
        projects = await pj_list(user_id)
        project_lines = [
            {"id": int(p["id"]), "title": p["title"]}
            for p in projects
        ]
        prompt = (
            "Разбери вопрос пользователя о его личных финансах. Верни только JSON: "
            '{"period":"day|week|month|all|custom","date_from":"YYYY-MM-DD|null","date_to":"YYYY-MM-DD|null","kind":"income|expense|null",'
            '"search_terms":["слова для фильтра по категории или заметке"],'
            '"project_title":"точное название проекта из списка или null",'
            '"confidence":0.0,"clarification":null}. '
            "Не отвечай на вопрос и не придумывай суммы — только извлеки фильтры. "
            "Если пользователь не указал период, выбери month. Для вчера, прошлого месяца или заданных дат используй custom и точные date_from/date_to. "
            "Для поиска по расходу/категории укажи до 5 близких поисковых слов. "
            "Если назван проект, project_title должен точно совпадать с одним названием из списка. "
            "Если вопрос неоднозначен, задай один короткий вопрос в clarification. "
            f"Текущая дата пользователя: {today.isoformat()}. "
            "Проекты: " + json.dumps(project_lines, ensure_ascii=False)
        )
        response = await ai.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": message},
            ],
            temperature=0.1,
            max_tokens=700,
            response_format={"type": "json_object"},
        )
        payload = json.loads(response.choices[0].message.content or "{}")
        if not isinstance(payload, dict):
            raise ValueError("Некорректный формат фильтров")
        if payload.get("clarification"):
            await update.effective_message.reply_text(
                str(payload["clarification"])[:400] + "\nНикакие данные не изменены.",
                reply_markup=MAIN_MENU,
            )
            return

        confidence = float(payload.get("confidence", 1))
        if confidence < 0.75:
            await update.effective_message.reply_text(
                "Не до конца понял, какие финансовые данные показать. Уточни период или категорию.",
                reply_markup=MAIN_MENU,
            )
            return

        period = str(payload.get("period") or "month").lower()
        if period not in {"day", "week", "month", "all", "custom"}:
            period = "month"
        kind = payload.get("kind")
        if kind not in {"income", "expense", None}:
            kind = None

        project_id = None
        project_title = payload.get("project_title")
        if project_title:
            normalize = lambda value: " ".join(re.sub(r"[^\w]+", " ", str(value).casefold()).split())
            wanted = normalize(project_title)
            matches = [p for p in project_lines if normalize(p["title"]) == wanted]
            if len(matches) != 1:
                await update.effective_message.reply_text(
                    f"Не нашёл точный проект «{str(project_title)[:100]}». Посмотри названия в /projects.",
                    reply_markup=MAIN_MENU,
                )
                return
            project_id = matches[0]["id"]
            project_title = matches[0]["title"]

        query = (
            supabase.table("finance_transactions")
            .select("id, kind, amount, category, note, tx_date, project_id")
            .eq("telegram_id", user_id)
            .order("tx_date", desc=True)
            .order("id", desc=True)
            .limit(5000)
        )
        if period == "day":
            start = end = today
        elif period == "week":
            start, end = finance_period("week", today)
        elif period == "month":
            start, end = finance_period("month", today)
        elif period == "custom":
            try:
                start = dt_date.fromisoformat(str(payload.get("date_from") or ""))
                end = dt_date.fromisoformat(str(payload.get("date_to") or ""))
            except ValueError:
                await update.effective_message.reply_text(
                    "Не удалось определить даты периода. Уточни, за какой промежуток показать операции.",
                    reply_markup=MAIN_MENU,
                )
                return
            if end < start:
                await update.effective_message.reply_text(
                    "Дата окончания периода раньше даты начала. Уточни даты.",
                    reply_markup=MAIN_MENU,
                )
                return
        else:
            start = end = None
        if start and end:
            query = query.gte("tx_date", start.isoformat()).lte("tx_date", end.isoformat())
        if kind:
            query = query.eq("kind", kind)
        if project_id is not None:
            query = query.eq("project_id", project_id)

        result = await db_call(query.execute)
        rows = result.data or []
        terms = payload.get("search_terms") or []
        if isinstance(terms, str):
            terms = [terms]
        terms = [str(term).strip().casefold() for term in terms if str(term).strip()][:5]
        if terms:
            rows = [
                row for row in rows
                if any(
                    term in f"{row.get('category') or ''} {row.get('note') or ''}".casefold()
                    for term in terms
                )
            ]

        income, expense = sum_by_kind(rows)
        period_label = {
            "day": "сегодня",
            "week": "за текущую неделю",
            "month": "за текущий месяц",
            "all": "за всё время",
            "custom": f"с {fmt_date(start.isoformat())} по {fmt_date(end.isoformat())}",
        }[period]
        lines = [f"📊 По сохранённым операциям {period_label}:"]
        if project_title:
            lines.append(f"Проект: {project_title}")
        if terms:
            lines.append("Поиск: " + ", ".join(terms))
        if not rows:
            lines += ["", "Подходящих операций в Supabase не нашёл."]
        else:
            if kind == "expense":
                lines.append(f"Расходы: {money(expense)}")
            elif kind == "income":
                lines.append(f"Доходы: {money(income)}")
            else:
                lines.append(f"Доходы: {money(income)}")
                lines.append(f"Расходы: {money(expense)}")
                lines.append(f"Разница: {money(income - expense)}")

            categories = {}
            for row in rows:
                label = row.get("category") or "без категории"
                categories[label] = categories.get(label, 0.0) + float(row["amount"])
            if categories:
                lines += ["", "Категории:"]
                for label, total in sorted(categories.items(), key=lambda item: -item[1])[:6]:
                    lines.append(f"- {label}: {money(total)}")
            lines += ["", f"Операций найдено: {len(rows)}."]
        lines += ["", "Это сумма записей в базе, а не фактический остаток банковского счёта."]
        await update.effective_message.reply_text("\n".join(lines), reply_markup=MAIN_MENU)
    except Exception:
        logging.exception("Не удалось обработать финансовый запрос")
        await update.effective_message.reply_text(
            "Не удалось получить финансовые данные из Supabase. Ничего не изменял.",
            reply_markup=MAIN_MENU,
        )


async def prepare_finance_draft(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    message: str,
    source_update_id: int,
):
    """Распознаёт финансовые события, но никогда не пишет их без подтверждения."""
    user_id = update.effective_user.id
    try:
        today = await user_today(user_id)
        projects = await pj_list(user_id)
        project_lines = [
            {"id": int(p["id"]), "title": p["title"]}
            for p in projects
            if p.get("status") != "done"
        ]
        project_context = json.dumps(project_lines, ensure_ascii=False)
        system_prompt = (
            "Ты извлекаешь финансовые операции из сообщения пользователя на русском языке. "
            "Верни только JSON-объект формата: "
            '{"transactions":[{"kind":"income|expense","amount":number,'
            '"category":"короткая категория","note":"описание или null",'
            '"date":"YYYY-MM-DD","project_title":"точное название проекта или null",'
            '"confidence":0.0,"needs_clarification":"вопрос или null"}],'
            '"clarification":null}. '
            "Извлекай ВСЕ отдельные доходы и расходы, не объединяй их. "
            "Не выдумывай операции, суммы, даты, категории и проекты. "
            "Учитывай только фактически совершённые операции; игнорируй отрицания, гипотезы и планы на будущее. "
            "Суммы вроде 15 тысяч, 2 тыс., 1,5 тысячи преобразуй в число рублей. "
            f"Если дата не указана, используй {today.isoformat()}. "
            "Тип операции определяй по смыслу; перевод между своими счетами не является доходом или расходом. "
            "Если неясно, доход это, расход, возврат долга или перевод — задай уточнение. "
            "Если пользователь говорит «на проект», но не называет какой, задай уточнение. "
            "Если неясна категория и это влияет на учёт, задай один короткий вопрос. "
            "project_title разрешено заполнять только точным названием из списка проектов ниже; "
            "не угадывай и не создавай проекты. Если проект не указан — null. "
            "При любой существенной неоднозначности верни transactions:[] и clarification с одним вопросом. "
            "Для каждой операции укажи confidence от 0 до 1; ниже 0.75 не ставь."
            "\nПроекты пользователя: " + project_context
        )
        response = await ai.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": message},
            ],
            temperature=0.1,
            max_tokens=1400,
            response_format={"type": "json_object"},
        )
        raw = response.choices[0].message.content or "{}"
        parsed = parse_finance_response(raw, today, project_lines)
    except Exception:
        logging.exception("Не удалось распознать финансовые операции")
        await update.effective_message.reply_text(
            "Не смог надёжно разобрать финансовое сообщение. Ничего не записывал. "
            "Попробуй указать суммы и назначение отдельнее.",
            reply_markup=MAIN_MENU,
        )
        return

    if parsed["clarification"]:
        context.user_data["finance_clarification_pending"] = {
            "message": message,
            "source_update_id": source_update_id,
        }
        await update.effective_message.reply_text(
            "Перед записью нужно уточнить: " + parsed["clarification"] +
            "\nОтветь одним сообщением. Пока ничего не сохранено.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Отмена", callback_data="fin:cancel")]]),
        )
        return

    transactions = parsed["transactions"]
    if not transactions:
        await update.effective_message.reply_text(
            "Не нашёл в сообщении подтверждённых доходов или расходов. Ничего не записывал.",
            reply_markup=MAIN_MENU,
        )
        return

    context.user_data["finance_draft_pending"] = {
        "transactions": transactions,
        "source_update_id": source_update_id,
    }
    lines = ["💰 Подготовил финансовые операции. Проверь перед записью:", ""]
    for index, row in enumerate(transactions, start=1):
        sign = "+" if row["kind"] == "income" else "−"
        label = "Доход" if row["kind"] == "income" else "Расход"
        line = (
            f"{index}. {label}: {sign}{money(row['amount'])} · {row['category']} "
            f"· {fmt_date(row['tx_date'].isoformat())}"
        )
        if row.get("note"):
            line += f"\n   {row['note']}"
        if row.get("project_title"):
            line += f"\n   Проект: {row['project_title']}"
        lines.append(line)
    lines += ["", "В базу пока ничего не записано."]
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Подтвердить запись", callback_data="fin:confirm"),
        InlineKeyboardButton("❌ Отмена", callback_data="fin:cancel"),
    ]])
    await update.effective_message.reply_text("\n".join(lines), reply_markup=keyboard)


async def confirm_finance_draft(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id
    if query.data == "fin:cancel":
        context.user_data.pop("finance_draft_pending", None)
        context.user_data.pop("finance_clarification_pending", None)
        await query.edit_message_text("Отменено. Финансовые записи не создавались.")
        return

    draft = context.user_data.get("finance_draft_pending")
    if not draft:
        context.user_data.pop("finance_clarification_pending", None)
        await query.edit_message_text("Черновик не найден или уже обработан. Повтори исходное сообщение.")
        return

    inserted = 0
    duplicates = 0
    try:
        for index, row in enumerate(draft["transactions"]):
            was_inserted = await fin_add_transaction(
                user_id,
                row["kind"],
                row["amount"],
                row["category"],
                row.get("note"),
                row["tx_date"],
                project_id=row.get("project_id"),
                source_update_id=draft["source_update_id"],
                source_item_index=index,
            )
            if was_inserted:
                inserted += 1
            else:
                duplicates += 1

        today = await user_today(user_id)
        start, end = finance_period("month", today)
        rows = await fin_load_transactions(user_id, start.isoformat(), end.isoformat())
        income, expense = sum_by_kind(rows)
    except Exception:
        logging.exception("Ошибка при подтверждённой записи финансового черновика")
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("🔁 Повторить безопасно", callback_data="fin:confirm"),
            InlineKeyboardButton("❌ Отмена черновика", callback_data="fin:cancel"),
        ]])
        await query.edit_message_text(
            "Не удалось завершить запись всех операций. Часть могла сохраниться. "
            "Повторная попытка не создаст дубликаты. Проверь подключение и попробуй ещё раз.",
            reply_markup=keyboard,
        )
        return

    context.user_data.pop("finance_draft_pending", None)
    if inserted == 0 and duplicates:
        result_line = "Эти операции уже были сохранены ранее; повторов не создано."
    else:
        result_line = f"Сохранено операций: {inserted}."
        if duplicates:
            result_line += f" Уже существовало без повторной записи: {duplicates}."
    await query.edit_message_text(
        "✅ " + result_line +
        f"\n\nИтоги за месяц по данным Supabase:"
        f"\nДоходы: {money(income)}"
        f"\nРасходы: {money(expense)}"
        f"\nРазница: {money(income - expense)}"
    )


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await reject_unauthorized(update)
        return
    keys = ("await", "finance_clarification_pending", "finance_draft_pending", "todoist_pending_action", "project_action_pending", "project_asset_target")
    had_pending = any(key in context.user_data for key in keys)
    for key in keys:
        context.user_data.pop(key, None)
    text = "Текущее действие отменено. Данные в базах не изменены." if had_pending else "Нет активного действия для отмены."
    await update.effective_message.reply_text(text, reply_markup=MAIN_MENU)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await reject_unauthorized(update)
        return

    user_id = update.effective_user.id
    user_message = update.effective_message.text

    if context.user_data.get("project_action_pending"):
        if user_message.strip().lower() in {"отмена", "отмени", "cancel"}:
            context.user_data.pop("project_action_pending", None)
            await update.effective_message.reply_text("Черновик проектного действия отменён. Данные не изменены.", reply_markup=MAIN_MENU)
        else:
            await update.effective_message.reply_text(
                "У тебя есть неподтверждённое действие по проекту. Подтверди или отмени его кнопками под сообщением."
            )
        return

    if context.user_data.get("project_asset_target"):
        if user_message.strip().lower() in {"отмена", "отмени", "cancel"}:
            context.user_data.pop("project_asset_target", None)
            await update.effective_message.reply_text("Ожидание файла отменено.", reply_markup=MAIN_MENU)
        else:
            await update.effective_message.reply_text("Жду фото или документ для проекта. Для отмены напиши /cancel.")
        return

    # Ответ на уточнение финансового агента относится к исходному сообщению.
    clarification = context.user_data.pop("finance_clarification_pending", None)
    if clarification:
        if user_message.strip().lower() in {"отмена", "отмени", "cancel"}:
            await update.effective_message.reply_text("Отменено. Ничего не записано.", reply_markup=MAIN_MENU)
            return
        combined = clarification["message"] + "\nУточнение пользователя: " + user_message
        await prepare_finance_draft(update, context, combined, clarification["source_update_id"])
        return

    if context.user_data.get("finance_draft_pending"):
        if user_message.strip().lower() in {"отмена", "отмени", "cancel"}:
            context.user_data.pop("finance_draft_pending", None)
            await update.effective_message.reply_text("Черновик отменён. Ничего не записано.", reply_markup=MAIN_MENU)
        else:
            await update.effective_message.reply_text(
                "У тебя уже есть непроверенный финансовый черновик. Сначала подтверди или отмени его кнопками под сообщением."
            )
        return

    # Если бот перед этим попросил что-то написать (сумму, название), это ответ на его просьбу.
    pending = context.user_data.pop("await", None)
    if pending:
        handler = AWAIT_ACTIONS.get(pending["action"])
        if handler:
            await handler(update, ArgsContext(context, pending["prefix"] + user_message.split()))
            return

    if looks_like_finance_entry(user_message):
        await update.effective_message.chat.send_action("typing")
        await prepare_finance_draft(update, context, user_message, update.update_id)
        return

    if looks_like_finance_question(user_message):
        await update.effective_message.chat.send_action("typing")
        await answer_finance_question(update, context, user_message)
        return

    if looks_like_project_intent(user_message):
        await update.effective_message.chat.send_action("typing")
        await prepare_project_action(update, context, user_message)
        return

    await update.effective_message.chat.send_action("typing")

    # Если недавно был опрос, это сообщение считаем ответом на него.
    survey = None
    extra_system = ""
    try:
        survey = await get_open_survey(user_id)
        if survey:
            extra_system = await build_survey_context(user_id, survey)
    except Exception:
        logging.exception("Не удалось проверить активный опрос")
        survey = None

    answer = await ask_ai(user_id, user_message, extra_system)
    if answer.startswith("__TODOIST_ACTION__"):
        try:
            action = json.loads(answer[len("__TODOIST_ACTION__"):])
            context.user_data["todoist_pending_action"] = action
            if action["op"] == "create":
                description = f"Добавить задачу: «{action['content']}»"
                if action.get("due_string"):
                    description += f"\nСрок: {action['due_string']}"
            elif action["op"] == "reschedule":
                description = (
                    f"Перенести задачу: «{action['content']}»"
                    f"\nНовый срок: {action['due_string']}"
                )
            else:
                description = f"Отметить выполненной задачу: «{action['content']}»"
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Да, выполнить", callback_data="td:yes"),
                InlineKeyboardButton("❌ Нет, отмена", callback_data="td:no"),
            ]])
            await update.effective_message.reply_text(
                "Подтверди изменение в Todoist. До твоего подтверждения ничего не изменится.\n\n"
                + description,
                reply_markup=keyboard,
            )
        except Exception:
            logging.exception("Не удалось показать подтверждение Todoist")
            await update.effective_message.reply_text(
                "Не удалось подготовить подтверждение действия Todoist. Ничего не изменено.",
                reply_markup=MAIN_MENU,
            )
    else:
        await update.effective_message.reply_text(clean_ai_text(answer), reply_markup=MAIN_MENU)

    if survey:
        try:
            await record_survey_answer(survey, user_message, answer)
        except Exception:
            logging.exception("Не удалось сохранить ответ на опрос")

    # Запоминаем уже после ответа, чтобы не заставлять ждать.
    saved = await extract_and_save_memories(user_id, user_message)
    if saved:
        logging.info("Сохранено новых записей памяти: %s", saved)


# ===================== КНОПКИ ВМЕСТО КОМАНД =====================
# Нажал кнопку в нижнем меню -> в чате появились кнопки действий.
# Если нужно что-то написать (сумму, название), бот просит это одним сообщением и ждёт.


class ArgsContext:
    """Обёртка: даёт старым командам готовые аргументы, как будто их напечатали после команды."""

    def __init__(self, base, args: list[str]):
        self._base = base
        self.args = args

    def __getattr__(self, name):
        return getattr(self._base, name)


def ib(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text, callback_data=data)


def ikb(rows: list[list[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(rows)


CANCEL_KB = ikb([[ib("❌ Отмена", "ui:cancel")]])

TASKS_KB = ikb([[ib("🔄 Обновить", "ui:tasks")]])

# ---------- финансы ----------

FIN_MENU = ikb([
    [ib("➖ Расход", "fin:exp"), ib("➕ Доход", "fin:inc")],
    [ib("📅 За неделю", "fin:week"), ib("🗓 За месяц", "fin:month")],
    [ib("🧾 Последние записи", "fin:list"), ib("🔁 Платежи", "fin:pay")],
    [ib("🛒 Покупки", "fin:buys")],
])

FIN_ADD_KB = ikb([[ib("➖ Расход", "fin:exp"), ib("➕ Доход", "fin:inc")]])

FIN_AFTER_KB = ikb([
    [ib("➖ Ещё расход", "fin:exp"), ib("➕ Ещё доход", "fin:inc")],
    [ib("💰 Итоги за месяц", "fin:month")],
])

PAY_AFTER_KB = ikb([[ib("➕ Ещё платёж", "pay:add")], [ib("🔁 Все платежи", "fin:pay")]])
BUY_AFTER_KB = ikb([[ib("➕ Ещё покупка", "buy:add")], [ib("🛒 Мои покупки", "fin:buys")]])


def fin_list_keyboard(rows: list[dict]) -> InlineKeyboardMarkup:
    keyboard = [[ib("➖ Расход", "fin:exp"), ib("➕ Доход", "fin:inc")]]
    row: list[InlineKeyboardButton] = []
    for r in rows:
        sign = "+" if r["kind"] == "income" else "−"
        row.append(ib(f"🗑 №{r['id']} {sign}{money(float(r['amount']))}", f"fin:del:{r['id']}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    keyboard.append([ib("💰 Итоги за месяц", "fin:month")])
    return ikb(keyboard)


def payments_keyboard(payments: list[dict]) -> InlineKeyboardMarkup:
    keyboard = [[ib("➕ Добавить платёж", "pay:add")]]
    for p in payments:
        keyboard.append([ib(f"🗑 №{p['id']} {clip(p['title'], 24)}", f"pay:del:{p['id']}")])
    keyboard.append([ib("💰 Итоги за месяц", "fin:month")])
    return ikb(keyboard)


def buys_keyboard(purchases: list[dict]) -> InlineKeyboardMarkup:
    keyboard = [[ib("➕ Добавить покупку", "buy:add")]]
    for p in purchases:
        keyboard.append([
            ib(f"📊 №{p['id']}", f"buy:check:{p['id']}"),
            ib(f"✅ №{p['id']}", f"buy:done:{p['id']}"),
            ib(f"🗑 №{p['id']}", f"buy:del:{p['id']}"),
        ])
    keyboard.append([ib("💰 Итоги за месяц", "fin:month")])
    return ikb(keyboard)


# ---------- проекты ----------

def projects_keyboard(shown: list[dict], has_done: bool, show_all: bool) -> InlineKeyboardMarkup:
    keyboard = [[ib("➕ Новый проект", "pj:new"), ib("⏰ Сроки", "pj:dl")]]
    for p in shown[:12]:
        keyboard.append([ib(f"📂 №{p['id']} {clip(p['title'], 28)}", f"pj:open:{p['id']}")])
    if has_done and not show_all:
        keyboard.append([ib("📜 Показать завершённые", "pj:listall")])
    return ikb(keyboard)


def project_card_keyboard(n: int) -> InlineKeyboardMarkup:
    return ikb([
        [ib("➕ Этап", f"pj:add:stage:{n}"), ib("➕ Задача", f"pj:add:task:{n}"), ib("📝 Заметка", f"pj:add:note:{n}")],
        [ib("📎 Добавить файл", f"pj:asset:{n}"), ib("📂 Материалы", f"pj:assets:{n}")],
        [ib("🤖 План от ИИ", f"pj:plan:{n}"), ib("☑️ Отметить", f"pj:mark:{n}")],
        [ib("✏️ Изменить", f"pj:edit:{n}"), ib("🗑 Убрать запись", f"pj:delmenu:{n}")],
        [ib("🗑 Удалить проект", f"pj:delproj:{n}"), ib("⬅️ К списку", "pj:list")],
    ])


def project_created_keyboard(n: int) -> InlineKeyboardMarkup:
    return ikb([
        [ib("📂 Открыть проект", f"pj:open:{n}")],
        [ib("🎯 Задать цель", f"pj:f:{n}:goal"), ib("🤖 План от ИИ", f"pj:plan:{n}")],
    ])


def item_after_keyboard(kind: str, n: int) -> InlineKeyboardMarkup:
    return ikb([
        [ib("➕ Ещё", f"pj:add:{kind}:{n}")],
        [ib("📂 Открыть проект", f"pj:open:{n}")],
    ])


PROJECT_EDIT_FIELDS = [
    ("title", "Название"), ("desc", "Описание"), ("goal", "Цель"),
    ("deadline", "Срок"), ("result", "Результат"), ("status", "Статус"),
]


def project_edit_keyboard(n: int) -> InlineKeyboardMarkup:
    rows = []
    row: list[InlineKeyboardButton] = []
    for field, label in PROJECT_EDIT_FIELDS:
        data = f"pj:stmenu:{n}" if field == "status" else f"pj:f:{n}:{field}"
        row.append(ib(label, data))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([ib("📂 Открыть проект", f"pj:open:{n}")])
    return ikb(rows)


def project_status_keyboard(n: int) -> InlineKeyboardMarkup:
    return ikb([
        [ib("💡 Идея", f"pj:st:{n}:idea"), ib("🚀 В работе", f"pj:st:{n}:active")],
        [ib("⏸ Пауза", f"pj:st:{n}:paused"), ib("✅ Завершён", f"pj:st:{n}:done")],
    ])


async def pj_mark_keyboard(user_id: int, n: int) -> InlineKeyboardMarkup:
    items = [i for i in await pj_items(user_id, n) if i["kind"] in ("stage", "task")]
    rows = []
    for i in items[:40]:
        icon = "🏁" if i["kind"] == "stage" else "🔹"
        mark = "✅" if i["done"] else "⬜"
        rows.append([ib(f"{mark} {icon} {clip(i['content'], 30)}", f"pj:tgl:{i['id']}")])
    rows.append([ib("📂 Открыть проект", f"pj:open:{n}")])
    return ikb(rows)


async def pj_delete_keyboard(user_id: int, n: int) -> InlineKeyboardMarkup:
    icons = {"stage": "🏁", "task": "🔹", "note": "📝"}
    items = await pj_items(user_id, n)
    rows = [
        [ib(f"🗑 {icons.get(i['kind'], '')} {clip(i['content'], 30)}", f"pj:idel:{i['id']}")]
        for i in items[:40]
    ]
    rows.append([ib("📂 Открыть проект", f"pj:open:{n}")])
    return ikb(rows)


# ---------- память ----------

MEMORY_KB = ikb([
    [ib("🗑 Удалить запись", "mem:menu")],
    [ib("🧹 Очистить историю диалога", "mem:reset")],
])


async def memory_delete_keyboard(user_id: int) -> InlineKeyboardMarkup:
    memories = await load_memories(user_id, limit=20)
    rows = [
        [ib(f"🗑 №{m['id']} {clip(m['content'], 30)}", f"mem:del:{m['id']}")]
        for m in memories
    ]
    return ikb(rows)


# ---------- расписание ----------

SCHEDULE_KB = ikb([
    [ib("☀️ Утро", "sched:t:morning"), ib("🌤 День", "sched:t:noon")],
    [ib("🌙 Вечер", "sched:t:evening"), ib("📈 Недельный отчёт", "sched:t:weekly")],
    [ib("🌍 Часовой пояс", "sched:tz")],
    [ib("🔔 Включить опросы", "sched:on"), ib("🔕 Выключить", "sched:off")],
    [ib("▶️ Проверить опрос сейчас", "sched:try")],
])

TIMEZONES = [
    ("Калининград", "Europe/Kaliningrad"), ("Москва", "Europe/Moscow"),
    ("Самара", "Europe/Samara"), ("Екатеринбург", "Asia/Yekaterinburg"),
    ("Омск", "Asia/Omsk"), ("Новосибирск", "Asia/Novosibirsk"),
    ("Красноярск", "Asia/Krasnoyarsk"), ("Владивосток", "Asia/Vladivostok"),
]


def timezone_keyboard() -> InlineKeyboardMarkup:
    rows = []
    for i in range(0, len(TIMEZONES), 2):
        rows.append([ib(name, f"tz:set:{zone}") for name, zone in TIMEZONES[i:i + 2]])
    rows.append([ib("✍️ Другой (написать название)", "tz:other")])
    return ikb(rows)


SURVEY_RUN_KB = ikb([
    [ib("☀️ Утренний", "sched:run:morning"), ib("🌤 Дневной", "sched:run:noon")],
    [ib("🌙 Вечерний", "sched:run:evening")],
])

TIME_KIND_NAMES = {
    "morning": "утреннего опроса",
    "noon": "дневного опроса",
    "evening": "вечернего опроса",
    "weekly": "недельного отчёта",
}


async def time_one_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Меняет время одного опроса (остальные не трогает). Аргументы: вид и время."""
    if not is_authorized(update):
        await reject_unauthorized(update)
        return

    args = context.args or []
    if len(args) != 2 or args[0] not in TIME_KIND_NAMES:
        await update.effective_message.reply_text(
            "Не понял. Нажми кнопку «⏰ Расписание» и выбери, что менять.", reply_markup=MAIN_MENU
        )
        return

    value = normalize_time(args[1])
    if not value:
        await update.effective_message.reply_text(
            "Не понял время. Нужен формат ЧЧ:ММ, например 07:30. Попробуй ещё раз через «⏰ Расписание».",
            reply_markup=MAIN_MENU,
        )
        return

    try:
        s = await load_settings(update.effective_user.id)
    except Exception:
        logging.exception("Не удалось прочитать настройки расписания")
        await update.effective_message.reply_text(
            "Не удалось прочитать настройки. Проверь консоль.", reply_markup=MAIN_MENU
        )
        return

    s[args[0]] = value
    await times_command(update, ArgsContext(context, [s["morning"], s["noon"], s["evening"], s["weekly"]]))


# ---------- ожидание ответа ----------

AWAIT_ACTIONS = {
    "expense": expense_command,
    "income": income_command,
    "payment": payment_command,
    "buy": buy_command,
    "project_new": project_new_command,
    "stage": stage_command,
    "task": task_command,
    "note": note_command,
    "pset": project_set_command,
    "time_one": time_one_command,
    "tz": timezone_command,
}

PROMPTS = {
    "expense": "➖ Напиши расход одним сообщением: сумма, категория, заметка.\nНапример: 350 еда обед\n(сумму пиши цифрами без пробелов)",
    "income": "➕ Напиши доход одним сообщением: сумма, категория, заметка.\nНапример: 50000 зарплата аванс\n(сумму пиши цифрами без пробелов)",
    "payment": "🔁 Напиши платёж: сумма, число месяца, название.\nНапример: 15000 5 Аренда квартиры\n(платёж каждый месяц 5-го числа)",
    "buy": "🛒 Напиши покупку: цена и название.\nНапример: 80000 Новый ноутбук",
    "project_new": "📁 Напиши название нового проекта одним сообщением.",
    "stage": "🏁 Напиши этап. Срок можно указать первым словом.\nНапример: 15.12 Собрать прототип",
    "task": "🔹 Напиши задачу. Срок можно указать первым словом.\nНапример: 20.11 Купить плату",
    "note": "📝 Напиши заметку одним сообщением.",
    "title": "✏️ Напиши новое название проекта.",
    "desc": "✏️ Напиши описание проекта одним сообщением. Чтобы очистить, напиши минус (-).",
    "goal": "🎯 Напиши цель проекта одним сообщением. Чтобы очистить, напиши минус (-).",
    "deadline": "⏳ Напиши срок: 31.12.2026 или 31.12. Чтобы убрать срок, напиши минус (-).",
    "result": "🏆 Напиши результат проекта одним сообщением. Чтобы очистить, напиши минус (-).",
    "tz": "🌍 Напиши название часового пояса, например: Asia/Yekaterinburg",
}


async def ask_input(update: Update, context: ContextTypes.DEFAULT_TYPE, action: str, prefix: list[str], prompt: str):
    context.user_data["await"] = {"action": action, "prefix": prefix}
    await update.effective_message.reply_text(prompt, reply_markup=CANCEL_KB)


async def clear_await_state(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Любая команда или кнопка меню отменяет ожидание ответа, кроме явной команды /cancel."""
    message = update.effective_message
    if message and (message.text or "").split()[0:1] == ["/cancel"]:
        return
    context.user_data.pop("await", None)


# ---------- главный обработчик нажатий ----------

async def ui_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_authorized(update):
        await query.answer("Доступ запрещён.", show_alert=True)
        return

    await query.answer()
    context.user_data.pop("await", None)

    parts = (query.data or "").split(":")
    area = parts[0]
    act = parts[1] if len(parts) > 1 else ""
    msg = update.effective_message
    user_id = update.effective_user.id

    def ctx(args: list[str]) -> ArgsContext:
        return ArgsContext(context, args)

    try:
        if area == "ui":
            if act == "cancel":
                context.user_data.pop("project_action_pending", None)
                context.user_data.pop("project_asset_target", None)
                await query.edit_message_text("Отменено.")
            elif act == "tasks":
                await tasks_command(update, ctx([]))
            elif act == "backup":
                await backup_command(update, ctx([]))

        elif area == "fin":
            if act in ("confirm", "cancel"):
                await confirm_finance_draft(update, context)
            elif act == "exp":
                await ask_input(update, context, "expense", [], PROMPTS["expense"])
            elif act == "inc":
                await ask_input(update, context, "income", [], PROMPTS["income"])
            elif act in ("week", "month"):
                await finance_command(update, ctx([act]))
            elif act == "list":
                await fin_list_command(update, ctx([]))
            elif act == "pay":
                await payments_command(update, ctx([]))
            elif act == "buys":
                await buys_command(update, ctx([]))
            elif act == "del":
                await fin_del_command(update, ctx([parts[2]]))

        elif area == "pay":
            if act == "add":
                await ask_input(update, context, "payment", [], PROMPTS["payment"])
            elif act == "del":
                await payment_del_command(update, ctx([parts[2]]))

        elif area == "buy":
            if act == "add":
                await ask_input(update, context, "buy", [], PROMPTS["buy"])
            elif act == "check":
                await buy_check_command(update, ctx([parts[2]]))
            elif act == "done":
                await buy_done_command(update, ctx([parts[2]]))
            elif act == "del":
                await buy_del_command(update, ctx([parts[2]]))

        elif area == "pj":
            if act == "new":
                await ask_input(update, context, "project_new", [], PROMPTS["project_new"])
            elif act == "list":
                await projects_command(update, ctx([]))
            elif act == "listall":
                await projects_command(update, ctx(["all"]))
            elif act == "dl":
                await deadlines_command(update, ctx([]))
            elif act == "open":
                await project_command(update, ctx([parts[2]]))
            elif act == "add":
                kind, n = parts[2], parts[3]
                await ask_input(update, context, kind, [n], PROMPTS[kind])
            elif act == "plan":
                await project_plan_command(update, ctx([parts[2]]))
            elif act == "mark":
                n = int(parts[2])
                markup = await pj_mark_keyboard(user_id, n)
                if len(markup.inline_keyboard) <= 1:
                    await msg.reply_text(
                        "В проекте пока нет этапов и задач. Добавь их кнопками в карточке проекта.",
                        reply_markup=markup,
                    )
                else:
                    await msg.reply_text(
                        "Нажми на этап или задачу, чтобы отметить выполненной (✅) или вернуть в работу (⬜):",
                        reply_markup=markup,
                    )
            elif act == "tgl":
                item = await pj_item_get(user_id, int(parts[2]))
                if item and item["kind"] != "note":
                    await db_call(
                        supabase.table("agent_project_items")
                        .update({"done": not item["done"]})
                        .eq("id", item["id"])
                        .eq("telegram_id", user_id)
                        .execute
                    )
                    markup = await pj_mark_keyboard(user_id, item["project_id"])
                    await query.edit_message_reply_markup(reply_markup=markup)
            elif act == "edit":
                await msg.reply_text("Что изменить?", reply_markup=project_edit_keyboard(int(parts[2])))
            elif act == "f":
                n, field = parts[2], parts[3]
                await ask_input(update, context, "pset", [n, field], PROMPTS[field])
            elif act == "stmenu":
                await msg.reply_text("Какой статус поставить?", reply_markup=project_status_keyboard(int(parts[2])))
            elif act == "st":
                await project_set_command(update, ctx([parts[2], "status", parts[3]]))
            elif act == "delmenu":
                markup = await pj_delete_keyboard(user_id, int(parts[2]))
                text = "Что убрать из проекта? Нажми на запись:" if len(markup.inline_keyboard) > 1 else "В проекте пока нечего убирать."
                await msg.reply_text(text, reply_markup=markup)
            elif act == "idel":
                await p_del_command(update, ctx([parts[2]]))
            elif act == "asset":
                await project_asset_command(update, ctx([parts[2]]))
            elif act == "assets":
                await project_assets_command(update, ctx([parts[2]]))
            elif act == "delproj":
                n = parts[2]
                await msg.reply_text(
                    "Проект удалится вместе с этапами, задачами, заметками и материалами. Финансовые операции останутся без привязки к проекту. Удалить?",
                    reply_markup=ikb([[ib("🗑 Да, удалить", f"pj:delyes:{n}"), ib("Отмена", "ui:cancel")]]),
                )
            elif act == "delyes":
                await project_del_command(update, ctx([parts[2], "да"]))

        elif area == "mem":
            if act == "menu":
                markup = await memory_delete_keyboard(user_id)
                text = "Какую запись удалить? Нажми на неё:" if markup.inline_keyboard else "Память пока пуста."
                await msg.reply_text(text, reply_markup=markup if markup.inline_keyboard else None)
            elif act == "del":
                await forget_command(update, ctx([parts[2]]))
            elif act == "reset":
                await msg.reply_text(
                    "Удалить историю диалога? Долговременная память, опросы и отчёты останутся.",
                    reply_markup=ikb([[ib("🧹 Да, очистить", "mem:resetyes"), ib("Отмена", "ui:cancel")]]),
                )
            elif act == "resetyes":
                await reset(update, ctx([]))

        elif area == "sched":
            if act == "t":
                kind = parts[2]
                await ask_input(
                    update, context, "time_one", [kind],
                    f"Напиши новое время {TIME_KIND_NAMES[kind]} в формате ЧЧ:ММ, например 07:30",
                )
            elif act == "tz":
                await msg.reply_text("Выбери свой часовой пояс:", reply_markup=timezone_keyboard())
            elif act in ("on", "off"):
                await surveys_command(update, ctx([act]))
            elif act == "try":
                await msg.reply_text("Какой опрос запустить прямо сейчас?", reply_markup=SURVEY_RUN_KB)
            elif act == "run":
                await survey_command(update, ctx([parts[2]]))

        elif area == "tz":
            if act == "set":
                await timezone_command(update, ctx([":".join(parts[2:])]))
            elif act == "other":
                await ask_input(update, context, "tz", [], PROMPTS["tz"])

    except Exception:
        logging.exception("Ошибка при обработке кнопки %s", query.data)
        await msg.reply_text("Не получилось выполнить действие. Подробности в консоли.", reply_markup=MAIN_MENU)


# ===================== РЕЗЕРВНАЯ КОПИЯ И НАПОМИНАНИЯ О СРОКАХ (ЭТАП 8) =====================

# Все таблицы, которые бот использует. Если какой-то нет в Supabase, это не страшно: пропустим.
BACKUP_TABLES = [
    "profiles", "conversations", "memories", "daily_surveys", "analytics_reports",
    "daily_logs", "ai_insights", "finance_transactions", "finance_payments",
    "finance_purchases", "agent_projects", "agent_project_items", "agent_project_assets",
]
BACKUP_PAGE = 1000
BACKUP_MAX_ROWS = 200_000
BACKUP_MAX_BYTES = 45 * 1024 * 1024  # лимит Telegram для файла от бота — 50 МБ

STATUS_KB = ikb([[ib("💾 Резервная копия", "ui:backup")]])


async def fetch_all_rows(table: str, ordered: bool) -> list[dict]:
    rows: list[dict] = []
    offset = 0
    while len(rows) < BACKUP_MAX_ROWS:
        query = supabase.table(table).select("*")
        if ordered:
            query = query.order("id")
        result = await db_call(query.range(offset, offset + BACKUP_PAGE - 1).execute)
        batch = result.data or []
        rows.extend(batch)
        if len(batch) < BACKUP_PAGE:
            break
        offset += BACKUP_PAGE
    return rows


async def dump_table(table: str) -> dict:
    """Читает таблицу целиком. Если нет колонки id для сортировки, читает без неё."""
    try:
        rows = await fetch_all_rows(table, ordered=True)
    except Exception:
        try:
            rows = await fetch_all_rows(table, ordered=False)
        except Exception as error:
            logging.warning("Таблица %s пропущена в копии: %s", table, error)
            return {"count": 0, "rows": [], "error": "таблица недоступна"}
    return {"count": len(rows), "rows": rows}


async def make_backup_file() -> tuple[bytes, str, dict]:
    """Возвращает (содержимое файла, имя файла, сколько строк в каждой таблице)."""
    tables = {}
    for table in BACKUP_TABLES:
        tables[table] = await dump_table(table)

    stamp = datetime.now(dt_timezone.utc).strftime("%Y-%m-%d_%H-%M")
    payload = {
        "created_at_utc": datetime.now(dt_timezone.utc).isoformat(),
        "note": "Резервная копия данных личного ИИ-агента. Ключи и пароли в неё не входят.",
        "tables": tables,
    }
    data = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
    name = f"backup-{stamp}.json"
    if len(data) > BACKUP_MAX_BYTES:
        data = gzip.compress(data)
        name += ".gz"
    counts = {t: v["count"] for t, v in tables.items() if not v.get("error")}
    return data, name, counts


async def send_backup(bot, chat_id: int, auto: bool = False) -> None:
    data, name, counts = await make_backup_file()
    total = sum(counts.values())
    caption = (
        ("🗄 Автоматическая резервная копия" if auto else "🗄 Резервная копия")
        + f"\nСтрок всего: {total}. Таблиц: {len(counts)}."
        + "\nСохрани файл у себя (например, в «Избранное» или на компьютер)."
    )
    await bot.send_document(
        chat_id=chat_id,
        document=io.BytesIO(data),
        filename=name,
        caption=caption,
    )


async def backup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await reject_unauthorized(update)
        return

    await update.effective_message.reply_text("Собираю копию всех данных, это может занять минуту…")
    try:
        await send_backup(context.bot, update.effective_user.id)
    except Exception:
        logging.exception("Не удалось сделать резервную копию")
        await update.effective_message.reply_text(
            "Не получилось собрать копию. Подробности в консоли.", reply_markup=MAIN_MENU
        )


# ---------- сроки ----------

def days_phrase(days: int) -> str:
    if days == 0:
        return "сегодня"
    if days == 1:
        return "завтра"
    if days > 1:
        return f"через {days} дн."
    return f"просрочено на {-days} дн."


def reminder_due(days: int, thresholds: tuple[int, ...]) -> bool:
    """Напоминаем в заданные дни до срока, а после срока: через 1, 3, 7 дней и потом раз в неделю."""
    if days >= 0:
        return days in thresholds
    late = -days
    return late in (1, 3, 7) or late % 7 == 0


def next_payment_date(day_of_month: int, today: dt_date) -> dt_date:
    last = calendar.monthrange(today.year, today.month)[1]
    this_month = today.replace(day=min(day_of_month, last))
    if this_month >= today:
        return this_month
    year, month = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
    last_next = calendar.monthrange(year, month)[1]
    return dt_date(year, month, min(day_of_month, last_next))


async def build_deadlines_text(user_id: int, today: dt_date, only_reminders: bool) -> tuple[str, list[int]]:
    """Собирает сроки проектов, этапов, задач и платежей.
    only_reminders=True: только то, о чём стоит напомнить именно сегодня.
    only_reminders=False: всё ближайшее (для кнопки «Сроки»)."""
    projects = {p["id"]: p for p in await pj_list(user_id)}
    live = {pid: p for pid, p in projects.items() if p["status"] in ("idea", "active")}
    progress = await pj_progress(user_id)

    lines: list[str] = []
    project_ids: list[int] = []

    def mark(pid: int) -> None:
        if pid not in project_ids:
            project_ids.append(pid)

    # сроки проектов
    for pid, p in live.items():
        if not p.get("deadline"):
            continue
        days = (dt_date.fromisoformat(p["deadline"]) - today).days
        show = reminder_due(days, (7, 3, 1, 0)) if only_reminders else days <= 14
        if show:
            pr = progress.get(pid, {"stage": [0, 0]})
            stages = f", этапы {pr['stage'][0]}/{pr['stage'][1]}" if pr["stage"][1] else ""
            lines.append(
                f"📁 №{pid} {p['title']}: срок {days_phrase(days)} ({fmt_date(p['deadline'])}){stages}"
            )
            mark(pid)

    # сроки этапов и задач
    result = await db_call(
        supabase.table("agent_project_items")
        .select("id, project_id, kind, content, due_date")
        .eq("telegram_id", user_id)
        .eq("done", False)
        .in_("kind", ["stage", "task"])
        .gte("due_date", "1900-01-01")
        .order("due_date")
        .limit(500)
        .execute
    )
    for item in result.data or []:
        if item["project_id"] not in live:
            continue
        days = (dt_date.fromisoformat(item["due_date"]) - today).days
        show = reminder_due(days, (3, 1, 0)) if only_reminders else days <= 14
        if show:
            icon = "🏁" if item["kind"] == "stage" else "🔹"
            lines.append(
                f"{icon} №{item['id']} {clip(item['content'], 60)} (проект №{item['project_id']}): "
                f"{days_phrase(days)} ({fmt_date(item['due_date'])})"
            )
            mark(item["project_id"])

    # обязательные платежи
    try:
        for p in await fin_load_payments(user_id):
            days = (next_payment_date(int(p["day_of_month"]), today) - today).days
            show = days in (3, 1, 0) if only_reminders else days <= 7
            if show:
                lines.append(f"🔁 Платёж «{p['title']}» {money(float(p['amount']))}: {days_phrase(days)}")
    except Exception:
        logging.exception("Не удалось проверить платежи для напоминаний")

    if not lines:
        return "", []
    header = "⏰ Сроки, на которые стоит обратить внимание:" if only_reminders else "⏰ Ближайшие сроки (на 2 недели вперёд):"
    return header + "\n\n" + "\n".join(lines[:40]), project_ids


def deadlines_keyboard(project_ids: list[int], projects_titles: dict[int, str] | None = None) -> InlineKeyboardMarkup | None:
    if not project_ids:
        return None
    rows = [[ib(f"📂 Проект №{pid}", f"pj:open:{pid}")] for pid in project_ids[:6]]
    return ikb(rows)


async def deadlines_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await reject_unauthorized(update)
        return

    user_id = update.effective_user.id
    try:
        today = await user_today(user_id)
        text, ids = await build_deadlines_text(user_id, today, only_reminders=False)
    except Exception:
        logging.exception("Не удалось собрать сроки")
        await update.effective_message.reply_text(PJ_SQL_HINT, reply_markup=MAIN_MENU)
        return

    if not text:
        await update.effective_message.reply_text(
            "Ближайших сроков нет: ни по проектам, ни по платежам. Срок проекта ставится в карточке: «✏️ Изменить» → «Срок».",
            reply_markup=MAIN_MENU,
        )
        return
    await reply_long(update.effective_message, text, inline=deadlines_keyboard(ids))


async def deadline_reminders_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Каждое утро после опроса: пишет, только если есть о чём напомнить."""
    try:
        today = await user_today(ALLOWED_TELEGRAM_USER_ID)
        text, ids = await build_deadlines_text(ALLOWED_TELEGRAM_USER_ID, today, only_reminders=True)
        if text:
            await context.bot.send_message(
                chat_id=ALLOWED_TELEGRAM_USER_ID,
                text=text[:4000],
                reply_markup=deadlines_keyboard(ids),
            )
    except Exception:
        logging.exception("Не удалось отправить напоминания о сроках")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logging.error("Необработанная ошибка Telegram handler", exc_info=context.error)


async def post_init(app: Application) -> None:
    """Выполняется один раз при запуске: список команд и расписание опросов."""
    await app.bot.set_my_commands([
        BotCommand("start", "Начать и показать меню"),
        BotCommand("memory", "Что я помню"),
        BotCommand("stats", "Статистика по базе"),
        BotCommand("schedule", "Расписание опросов"),
        BotCommand("status", "Состояние бота"),
        BotCommand("tasks", "Задачи из Todoist"),
        BotCommand("day", "Отчёт за день"),
        BotCommand("week", "Отчёт за неделю"),
        BotCommand("month", "Отчёт за месяц"),
        BotCommand("times", "Время: /times 07:30 13:00 20:00 20:30"),
        BotCommand("timezone", "Часовой пояс: /timezone Europe/Moscow"),
        BotCommand("surveys", "Опросы on/off"),
        BotCommand("survey", "Запустить опрос сейчас"),
        BotCommand("forget", "Удалить запись памяти: /forget номер"),
        BotCommand("reset", "Удалить историю диалога"),
        BotCommand("finance", "Финансы: итоги за месяц или неделю"),
        BotCommand("expense", "Расход: /expense 350 еда обед"),
        BotCommand("income", "Доход: /income 50000 зарплата"),
        BotCommand("fin_list", "Последние финансовые записи"),
        BotCommand("fin_del", "Удалить запись: /fin_del номер"),
        BotCommand("payment", "Платёж: /payment 15000 5 Аренда"),
        BotCommand("payments", "Обязательные платежи"),
        BotCommand("payment_del", "Удалить платёж: /payment_del номер"),
        BotCommand("buy", "Покупка в планах: /buy 80000 Ноутбук"),
        BotCommand("buys", "Запланированные покупки"),
        BotCommand("buy_check", "Оценить покупку: /buy_check номер"),
        BotCommand("buy_done", "Купил: /buy_done номер"),
        BotCommand("buy_del", "Убрать покупку: /buy_del номер"),
        BotCommand("project_new", "Новый проект: /project_new название"),
        BotCommand("projects", "Список проектов"),
        BotCommand("project", "Карточка проекта: /project номер"),
        BotCommand("project_set", "Изменить проект: /project_set номер поле значение"),
        BotCommand("project_plan", "ИИ предложит этапы: /project_plan номер"),
        BotCommand("stage", "Этап: /stage номер текст"),
        BotCommand("task", "Задача: /task номер текст"),
        BotCommand("note", "Заметка: /note номер текст"),
        BotCommand("p_done", "Отметить выполненным: /p_done номер"),
        BotCommand("p_undo", "Вернуть в работу: /p_undo номер"),
        BotCommand("p_del", "Удалить этап/задачу/заметку: /p_del номер"),
        BotCommand("project_del", "Удалить проект: /project_del номер да"),
        BotCommand("deadlines", "Ближайшие сроки"),
        BotCommand("backup", "Резервная копия данных"),
        BotCommand("help", "Помощь"),
    ])

    try:
        settings = await load_settings(ALLOWED_TELEGRAM_USER_ID)
        schedule_jobs(app, settings)
    except Exception:
        logging.exception(
            "Не удалось запустить расписание опросов. Проверь, что SQL для этапа 4 выполнен."
        )


def main():
    app = Application.builder().token(TELEGRAM_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("cancel", cancel_command))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("memory", memory_command))
    app.add_handler(CommandHandler("forget", forget_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("schedule", schedule_command))
    app.add_handler(CommandHandler("times", times_command))
    app.add_handler(CommandHandler("timezone", timezone_command))
    app.add_handler(CommandHandler("surveys", surveys_command))
    app.add_handler(CommandHandler("survey", survey_command))
    app.add_handler(CommandHandler("tasks", tasks_command))
    app.add_handler(CommandHandler("day", day_command))
    app.add_handler(CommandHandler("week", week_command))
    app.add_handler(CommandHandler("month", month_command))
    app.add_handler(CommandHandler("finance", finance_command))
    app.add_handler(CommandHandler("expense", expense_command))
    app.add_handler(CommandHandler("income", income_command))
    app.add_handler(CommandHandler("fin_list", fin_list_command))
    app.add_handler(CommandHandler("fin_del", fin_del_command))
    app.add_handler(CommandHandler("payment", payment_command))
    app.add_handler(CommandHandler("payments", payments_command))
    app.add_handler(CommandHandler("payment_del", payment_del_command))
    app.add_handler(CommandHandler("buy", buy_command))
    app.add_handler(CommandHandler("buys", buys_command))
    app.add_handler(CommandHandler("buy_check", buy_check_command))
    app.add_handler(CommandHandler("buy_done", buy_done_command))
    app.add_handler(CommandHandler("buy_del", buy_del_command))
    app.add_handler(CommandHandler("backup", backup_command))
    app.add_handler(CommandHandler("deadlines", deadlines_command))
    app.add_handler(CommandHandler("project_new", project_new_command))
    app.add_handler(CommandHandler("projects", projects_command))
    app.add_handler(CommandHandler("project", project_command))
    app.add_handler(CommandHandler("project_set", project_set_command))
    app.add_handler(CommandHandler("project_plan", project_plan_command))
    app.add_handler(CommandHandler("stage", stage_command))
    app.add_handler(CommandHandler("task", task_command))
    app.add_handler(CommandHandler("note", note_command))
    app.add_handler(CommandHandler("p_done", p_done_command))
    app.add_handler(CommandHandler("p_undo", p_undo_command))
    app.add_handler(CommandHandler("p_del", p_del_command))
    app.add_handler(CommandHandler("project_del", project_del_command))
    app.add_handler(CommandHandler("asset", project_asset_command))
    app.add_handler(CommandHandler("assets", project_assets_command))
    app.add_handler(CallbackQueryHandler(project_action_callback, pattern="^pjact:"))
    app.add_handler(CallbackQueryHandler(plan_callback, pattern="^plan:"))
    app.add_handler(CallbackQueryHandler(report_callback, pattern="^report:"))
    app.add_handler(CallbackQueryHandler(todoist_confirm_callback, pattern="^td:"))

    # Кнопки меню должны идти РАНЬШЕ обычных сообщений, иначе их съест ИИ.
    buttons_pattern = "^(" + "|".join(re.escape(b) for b in MENU_BUTTONS) + ")$"
    app.add_handler(
        MessageHandler(filters.TEXT & filters.Regex(buttons_pattern), handle_menu_button)
    )
    # Любая команда или кнопка меню отменяет «жду твой ответ» (группа -1 срабатывает первой).
    app.add_handler(
        MessageHandler(filters.COMMAND | filters.Regex(buttons_pattern), clear_await_state),
        group=-1,
    )
    app.add_handler(CallbackQueryHandler(ui_callback, pattern=r"^(fin|pay|buy|pj|mem|sched|tz|ui):"))

    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, handle_project_asset))
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
    )
    app.add_error_handler(error_handler)

    print("Личный агент запущен! Доступ разрешён только владельцу.")
    app.run_polling()


if __name__ == "__main__":
    main()
