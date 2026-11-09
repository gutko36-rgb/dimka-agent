# Локальный запуск и проверка на Windows

## 1. Подготовка

Установи Python 3.11 или новее. В PowerShell:

```powershell
git clone https://github.com/gutko36-rgb/dimka-agent.git
cd dimka-agent
git fetch origin
git switch --track origin/feature/finance-natural-language
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
notepad .env
```

Если репозиторий уже клонирован, достаточно `git fetch origin` и переключения на ветку.

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

## 3. Проверки без запуска Telegram

```powershell
python -m py_compile bot.py finance_agent.py
python -m unittest discover -s tests -v
```

Эти команды не запускают Telegram polling и не создают записи в Supabase.

## 4. Smoke test Telegram

Важно: не запускай одновременно две копии одного Telegram-бота с одним токеном — они будут конкурировать за long polling. Сначала останови текущий экземпляр бота либо используй отдельный тестовый Telegram bot token.

Запусти:

```powershell
python bot.py
```

В Telegram отправь `/start`, затем `/status`.

Для проверки финансового агента отправь небольшую тестовую операцию:

```text
Потратил 1 рубль на тестирование финансового агента
```

Ожидаемое поведение:
1. Бот покажет черновик расхода.
2. До нажатия «Подтвердить запись» база не меняется.
3. После подтверждения бот сообщит о записи и покажет итоги, перечитанные из Supabase.
4. Команда `/fin_list` покажет новую запись.
5. Запрос вроде «Сколько потратил на тестирование в этом месяце?» вернёт сумму по сохранённым данным.
6. Удали тестовую операцию штатной командой `/fin_del ID`.

Проверь также отмену: отправь ещё одну тестовую фразу и нажми «Отмена» или `/cancel`. Запись не должна появиться.

## 5. Возврат к стабильной версии

Изменения финансового агента пока находятся в отдельной ветке и Pull Request. Слияние и развёртывание в работающий экземпляр не выполняются автоматически.

Чтобы вернуться к основной версии:

```powershell
git switch main
git pull origin main
```

Не удаляй старую копию проекта и не заменяй её до проверки новой версии.
