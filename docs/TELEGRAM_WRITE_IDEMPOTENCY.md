# Идемпотентность записывающих операций Telegram

## Цель

Повторная доставка одного Telegram update не должна создавать второй платёж, покупку, проект, этап, сообщение истории, опрос или отчёт.

## Ключи

| Операция | Ключ идемпотентности |
|---|---|
| Финансовая транзакция | `(telegram_id, source_update_id, source_item_index)` — уже существует |
| Черновик финансового действия | `(telegram_id, source_update_id)` — уже существует |
| Вложение проекта | `(telegram_id, project_id, telegram_file_unique_id)` — уже существует |
| Сообщение истории | `(telegram_id, source_update_id, source_item_index)` |
| Платёж | `(telegram_id, source_update_id)` |
| Покупка | `(telegram_id, source_update_id)` |
| Проект | `(telegram_id, source_update_id)` |
| Этап/задача/заметка проекта | `(telegram_id, source_update_id, source_item_index)` |
| Опрос | `(telegram_id, survey_date, kind)` |
| Отчёт | `(telegram_id, kind, period_start, period_end)` |
| Ежедневная запись | `(telegram_id, log_date, checkin_type)` |

Ответ на опрос дополнительно хранит `last_answer_update_id`, чтобы один и тот же входящий update не дописывался в ответ повторно.

## Совместимость до миграции

Код сначала использует `upsert(..., ignore_duplicates=True)` по новым ключам. Если Supabase сообщает, что колонка или уникальный индекс ещё не развёрнуты, код временно использует старый insert и пишет предупреждение в лог. Это сохраняет работу текущего бота до применения SQL, но **не обеспечивает полную защиту от дублей** до применения proposal.

## Безопасность

- Все записи по-прежнему включают `telegram_id`.
- Все обновления и чтения существующих пользовательских записей остаются ограничены владельцем.
- Повторный update не должен повторять финансовую транзакцию.
- Создание проекта/этапов возвращает уже созданную запись при повторе.
- SQL proposal добавляет nullable columns и unique indexes; существующие строки не переписываются.
- SQL не применён к рабочему Supabase.

## Проверки

- Python unit tests проверяют вызов idempotent upsert и compatibility fallback.
- PostgreSQL 17 integration tests применяют proposal к disposable schema и проверяют уникальные ключи, в том числе несколько элементов одного плана.
