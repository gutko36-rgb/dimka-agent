# Агент целей и привычек

## Назначение

Отдельный owner-scoped модуль для целей, привычек и истории отметок. Существующие опросы, daily_logs, память и профили не изменяются.

## Команды

- /life — сводка активных целей и привычек за последние 7 дней.
- /goals — активные цели.
- /goal_add Название | описание|- | срок YYYY-MM-DD|- — создать цель.
- /goal_progress ID | 0-100 | заметка|- — записать новый прогресс с историей.
- /habits — активные привычки.
- /habit_add Название | раз в неделю 1-7 | описание|- — создать привычку.
- /habit_log ID | done|skipped | заметка|- — отметить выполнение или пропуск за текущий локальный день.

Все изменения показывают предпросмотр и требуют нажатия «Подтвердить». Кнопки подтверждения привязаны к уникальному идентификатору черновика, поэтому старая кнопка не может подтвердить более новую операцию. Прогресс цели записывается атомарной RPC с историей и идемпотентностью. История отметок append-only и идемпотентна по Telegram update ID; сводка использует последнюю отметку по каждой привычке и дате.

## Безопасность

- Каждая операция фильтруется по telegram_id.
- SQL включает composite foreign keys, owner-scoped unique keys и RLS.
- anon и authenticated не получают доступ; серверный service_role остаётся только в локальном .env.
- Никакие существующие таблицы или данные не изменяются.
- SQL находится в docs/sql/LIFESTYLE_AGENT_PROPOSAL.sql и не применён к Supabase.
- Feature flag LIFESTYLE_AGENT_ENABLED=false по умолчанию. До проверки на отдельной Supabase development branch команды не обращаются к новым таблицам.

## Проверка

Unit:
```powershell
python -m py_compile lifestyle_agent.py lifestyle_workflow.py lifestyle_telegram.py
python -m unittest discover -s tests -p "test_lifestyle*.py" -v
```

PostgreSQL 17 integration:
```powershell
python -m unittest discover -s tests -p "test_lifestyle_agent_postgres.py" -v
```

Последняя команда требует DATABASE_URL тестовой PostgreSQL 17. CI запускает её на одноразовой базе.
