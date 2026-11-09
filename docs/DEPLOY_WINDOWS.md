# Локальный запуск на Windows

## 1. Подготовка

Установи Python 3.11 или новее. В PowerShell:

```powershell
git clone https://github.com/gutko36-rgb/dimka-agent.git
cd dimka-agent
git switch work/complete-tz-v1
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
notepad .env
```

Заполни локальный `.env`. Не отправляй его в чат и не добавляй в Git.

## 2. Секреты

Обязательны:
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_USER_ID`
- `GROQ_API_KEY`
- `GROQ_MODEL`
- `SUPABASE_URL`
- `SUPABASE_KEY`

`SUPABASE_KEY` должен быть серверным `service_role` / secret key. Никогда не используй его в браузере и не публикуй. В проекте с включённым RLS без политик обычный anon/publishable ключ не сможет выполнять операции бота.

`TODOIST_API_TOKEN` необязателен.

## 3. Проверка конфигурации и запуск

```powershell
python -m py_compile bot.py
python bot.py
```

В Telegram отправь `/start`, затем `/status`. Проверь чтение финансов, проектов и Todoist (если он настроен).

Не запускай одновременно две копии одного Telegram-бота с одним токеном: они будут конкурировать за long polling.

## 4. Проверка финансовой привязки к проекту

Сначала вызови `/projects` и выбери ID существующего проекта. Затем тестируй только небольшой тестовой операцией:

```
/expense 1 test project=ID
```

Проверь запись в `finance_transactions`: `project_id` должен указывать на выбранный проект. После проверки удали тестовую запись штатной командой бота.

## 5. Важное ограничение

Свободная фраза «потратил 500 рублей» пока не должна автоматически менять финансовую базу. В текущей версии для финансов используются команды. Следующий этап — распознавание естественного языка, показ всех извлечённых операций пользователю и запись только после подтверждения.

## 6. Возврат к стабильной версии

Рабочие изменения находятся в ветке `work/complete-tz-v1`. Стабильная ветка `main` не изменялась. Если обновление не прошло локальные проверки, вернись на `main`:

```powershell
git switch main
```

Не удаляй старую копию проекта и не заменяй её до проверки новой версии.
