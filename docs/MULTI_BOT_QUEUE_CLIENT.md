# Межботовая очередь: первый клиентский модуль

## Что уже есть

Файл bot_queue.py — отдельный модуль для отправки, чтения и обновления записей в bot_messages. Он не импортируется в bot.py и не меняет запуск Димки.

## Обязательное условие безопасности

BotQueueClient должен получать Supabase client, аутентифицированный как конкретный бот отдельным JWT. Нельзя передавать ему client, созданный с service_role / secret key. Локальные проверки в Python не являются защитой доступа: окончательную изоляцию обеспечивает RLS в Supabase.

Существующий Димка пока использует SUPABASE_KEY как серверный ключ для своей текущей базы. Это не меняется в данном этапе. Для каждого специализированного worker нужна отдельная Auth identity и JWT. Такая identity уже создана для `finance`; перед добавлением следующего агента нужно создать отдельную identity и проверить адресное чтение/запись очереди.

## Методы

- enqueue(...) — создаёт задачу/событие с sender ID из BotIdentity, адресатом, типом, JSON payload и idempotency key.
- claim_inbox(limit=20, lease_seconds=300, message_type="task") — атомарно забирает сообщения выбранного типа через RPC `claim_bot_messages`; по умолчанию worker получает только `task`, главный бот может отдельно забирать `result`. Параллельные workers не получают одну запись. Сообщения с будущим `available_at` не выдаются раньше срока. Если worker упал, просроченная аренда позволяет повторить попытку; после `max_attempts` сообщение переводится в `failed`. Сообщения с истёкшим `expires_at` переводятся в `failed`, в том числе если они истекли до первого захвата или во время аренды.
- fetch_inbox(limit=20) — только диагностическое чтение pending-сообщений; не использовать как механизм выдачи работы нескольким workers.
- update_status(...) — переводит уже захваченное сообщение из `processing` в конечный статус (`completed`, `failed`, `cancelled`) и только если оно адресовано этому боту.

Дублирование (from_bot_id, idempotency_key), проверка активности адресата и ограничения RLS обеспечиваются схемой БД, а не этим модулем.

## Ограничения текущего этапа

- SQL-миграция `20261110035645_multi_bot_queue` уже применена в Supabase. Для очереди созданы явные минимальные GRANT и RLS-политики; `anon` не получает доступ.
- Модуль не подключён к bot.py и не выполняет polling.
- Миграция `20261110035645_multi_bot_queue` применена в подключённом Supabase. CI проверяет SQL на PostgreSQL 17, включая RLS, ограничения ролей, параллельный claim и повтор после истечения lease. Эти тесты не заменяют отдельную проверку реального Supabase Auth/JWT.
- Скрипт `scripts/provision_bot_identity.py` создаёт отдельную Supabase Auth identity/JWT для нового worker. Для `finance` такая identity уже создана и использована в smoke test; Telegram-токены workers не нужны.
- Unit tests используют поддельный Supabase-клиент для проверки Python-запросов. Отдельные CI integration tests применяют SQL proposal к настоящему PostgreSQL 17 и проверяют RLS, подмену отправителя, запрет anon, выбор типа сообщения, параллельный claim и повтор после истечения lease. Тестовый `auth.jwt()` — shim; это ещё не проверка реального Supabase Auth, подписанных JWT или настроек Data API.

## Локальная проверка на Windows

Из корня репозитория после переключения на ветку PR:

    python -m py_compile bot_queue.py bot_worker.py
    python -m unittest discover -s tests -p "test_bot_queue.py" -v
    python -m unittest discover -s tests -p "test_bot_worker.py" -v

Ожидаемый результат: 13 тестов клиента и 9 тестов worker проходят. Эти команды не запускают Telegram и не меняют Supabase.

## Универсальный worker

Файл `bot_worker.py` содержит общий исполнитель задач. Он:
- атомарно забирает задачи через `claim_inbox()`;
- выбирает обработчик по `payload.task_type`;
- передаёт ему только объект `payload.input`;
- отправляет результат отправителю как отдельное `result`-сообщение;
- использует детерминированный idempotency key для результата;
- не повторяет внешние действия и не подтверждает операции за владельца.

Обработчики должны быть чистыми вычислениями: без записи финансовых операций, изменения Todoist, отправки сообщений или иных внешних побочных эффектов. Такие действия должны возвращаться главному Димке как предложение и проходить подтверждение владельца. Ошибки доставки оставляют задачу в processing до истечения lease, после чего она может быть повторена.

Worker-процесс запускается отдельно через `bot_worker_service.py`; он не подключается к Telegram и не требует Telegram token. Основной `bot.py` уже подключён к result inbox и доставляет адресованные ему ответы. Для каждой новой роли нужна отдельная Supabase Auth identity/JWT.


## Развёртывание первого изолированного worker

Схема очереди уже применена в Supabase. Перед запуском worker нужно создать ему отдельную Auth identity. Делать это следует на доверенной административной машине, где доступен service-role key:

```powershell
$env:SUPABASE_URL="https://<project-ref>.supabase.co"
$env:SUPABASE_SERVICE_ROLE_KEY="<service-role-key>"
python scripts/provision_bot_identity.py --bot-id finance --role finance --display-name "Финансовый агент" --email finance-worker@example.com
```

Скрипт:
- создаёт запись реестра сначала неактивной;
- создаёт Supabase Auth user с доверенным `app_metadata.bot_id=finance`;
- активирует запись только после успешного создания пользователя;
- генерирует отдельный случайный пароль и показывает его один раз;
- не записывает service-role key в worker и не меняет существующего пользователя.

Скопируй выведенные `BOT_AUTH_EMAIL` и `BOT_AUTH_PASSWORD` в отдельный приватный файл `.env.worker` в корне проекта. Worker загружает только этот файл и не читает основной `.env` Димки, где находится service-role key. Там должны быть:

```env
SUPABASE_URL=https://<project-ref>.supabase.co
SUPABASE_ANON_KEY=<publishable-or-anon-key>
BOT_ID=finance
BOT_ROLE=finance
BOT_DISPLAY_NAME=Финансовый агент
BOT_AUTH_EMAIL=finance-worker@example.com
BOT_AUTH_PASSWORD=<generated-password>
BOT_WORKER_BATCH_SIZE=10
```

**На worker-машине не должно быть `SUPABASE_KEY` или service-role key.** Процесс входит через Supabase Auth и перед созданием клиента очереди проверяет, что доверенный `app_metadata.bot_id` совпадает с `BOT_ID`.

Для запуска одного пакета:

```powershell
python bot_worker_service.py --once
```

Для постоянной обработки:

```powershell
python bot_worker_service.py
```

Чтобы проверить полный обмен, оставь worker запущенным и на доверенной административной машине выполни:

```powershell
$env:SUPABASE_URL="https://<project-ref>.supabase.co"
$env:SUPABASE_SERVICE_ROLE_KEY="<service-role-key>"
$env:SMOKE_TEST_WORKER_ID="finance"
python scripts/smoke_test_worker.py
```

Скрипт отправляет задачу от `dimka`, ждёт ответ worker до 90 секунд и проверяет, что результат вернулся в очередь. Service-role key используется только на доверенной машине для smoke test.

Пока зарегистрирован только безопасный smoke-test обработчик `diagnostics.echo`. Он подтверждает аутентификацию, RLS, доставку задачи, возврат результата и завершение сообщения, но не выполняет финансовые операции и не читает пользовательские таблицы. Финансовые и проектные обработчики будут подключаться отдельно после проверки их owner-scoping и прав доступа.
