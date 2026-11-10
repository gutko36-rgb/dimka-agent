"""Confirmation-based Telegram handlers for goals and habits."""
from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any, Callable

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from lifestyle_workflow import (
    parse_goal_create,
    parse_goal_progress,
    parse_habit_create,
    parse_habit_log,
    render_goals,
    render_habit_history,
    render_habits,
)


def build_lifestyle_handlers(
    *,
    store: Any,
    enabled: bool,
    is_authorized: Callable[[Any], bool],
    reject_unauthorized: Callable[[Any], Any],
    user_today: Callable[[int], Any],
    main_menu: Any,
) -> dict[str, Callable[..., Any]]:
    async def _ready(update: Any) -> bool:
        if not is_authorized(update):
            await reject_unauthorized(update)
            return False
        if not enabled:
            await update.effective_message.reply_text(
                "Агент целей и привычек пока выключен. Сначала нужно проверить его SQL-схему "
                "на отдельной базе; существующие опросы и записи не меняются.",
                reply_markup=main_menu,
            )
            return False
        return True

    def _keyboard(draft_id: str) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Подтвердить", callback_data=f"life:confirm:{draft_id}"),
            InlineKeyboardButton("❌ Отмена", callback_data=f"life:cancel:{draft_id}"),
        ]])

    async def goals_command(update: Any, context: Any) -> None:
        if not await _ready(update):
            return
        try:
            rows = await store.list_goals(update.effective_user.id)
        except Exception:
            await update.effective_message.reply_text("Не удалось получить цели из Supabase.", reply_markup=main_menu)
            return
        await update.effective_message.reply_text(render_goals(rows), reply_markup=main_menu)

    async def goal_add_command(update: Any, context: Any) -> None:
        if not await _ready(update):
            return
        try:
            payload = parse_goal_create(" ".join(context.args or []))
        except ValueError as exc:
            await update.effective_message.reply_text(
                f"{exc}\nФормат: /goal_add Название | описание|- | срок YYYY-MM-DD|-",
                reply_markup=main_menu,
            )
            return
        draft_id = uuid.uuid4().hex[:12]
        context.user_data["lifestyle_pending"] = {
            "draft_id": draft_id,
            "action": "goal_create",
            "payload": payload,
            "source_update_id": update.update_id,
        }
        await update.effective_message.reply_text(
            "Черновик новой цели:\n"
            f"Название: {payload['title']}\n"
            f"Описание: {payload['description'] or '—'}\n"
            f"Срок: {payload['target_date'] or 'не указан'}\n"
            "До подтверждения база не меняется.",
            reply_markup=_keyboard(draft_id),
        )

    async def goal_progress_command(update: Any, context: Any) -> None:
        if not await _ready(update):
            return
        try:
            payload = parse_goal_progress(" ".join(context.args or []))
            goal = await store.get_goal(update.effective_user.id, payload["goal_id"])
            if not goal or goal.get("status") != "active":
                raise ValueError("Активная цель с таким номером не найдена.")
        except ValueError as exc:
            await update.effective_message.reply_text(
                f"{exc}\nФормат: /goal_progress ID | прогресс 0-100 | заметка|-",
                reply_markup=main_menu,
            )
            return
        except Exception:
            await update.effective_message.reply_text("Не удалось проверить цель. Ничего не записано.", reply_markup=main_menu)
            return
        draft_id = uuid.uuid4().hex[:12]
        context.user_data["lifestyle_pending"] = {
            "draft_id": draft_id,
            "action": "goal_progress",
            "payload": {**payload, "title": goal["title"]},
            "source_update_id": update.update_id,
        }
        await update.effective_message.reply_text(
            "Черновик изменения прогресса:\n"
            f"Цель: {goal['title']} (№{goal['id']})\n"
            f"Новый прогресс: {payload['progress']}%\n"
            f"Заметка: {payload['note'] or '—'}\n"
            "История прогресса сохранится отдельной записью.",
            reply_markup=_keyboard(draft_id),
        )

    async def habits_command(update: Any, context: Any) -> None:
        if not await _ready(update):
            return
        try:
            rows = await store.list_habits(update.effective_user.id)
        except Exception:
            await update.effective_message.reply_text("Не удалось получить привычки из Supabase.", reply_markup=main_menu)
            return
        await update.effective_message.reply_text(render_habits(rows), reply_markup=main_menu)

    async def habit_add_command(update: Any, context: Any) -> None:
        if not await _ready(update):
            return
        try:
            payload = parse_habit_create(" ".join(context.args or []))
            start_date = await user_today(update.effective_user.id)
        except ValueError as exc:
            await update.effective_message.reply_text(
                f"{exc}\nФормат: /habit_add Название | раз в неделю 1-7 | описание|-",
                reply_markup=main_menu,
            )
            return
        draft_id = uuid.uuid4().hex[:12]
        context.user_data["lifestyle_pending"] = {
            "draft_id": draft_id,
            "action": "habit_create",
            "payload": {**payload, "start_date": start_date},
            "source_update_id": update.update_id,
        }
        await update.effective_message.reply_text(
            "Черновик новой привычки:\n"
            f"Название: {payload['title']}\n"
            f"Цель: {payload['target_per_week']} раз(а) в неделю\n"
            f"Описание: {payload['description'] or '—'}\n"
            f"Начало: {start_date.isoformat()}\n"
            "До подтверждения база не меняется.",
            reply_markup=_keyboard(draft_id),
        )

    async def habit_log_command(update: Any, context: Any) -> None:
        if not await _ready(update):
            return
        try:
            payload = parse_habit_log(" ".join(context.args or []))
            habit = await store.get_habit(update.effective_user.id, payload["habit_id"])
            if not habit or not habit.get("is_active"):
                raise ValueError("Активная привычка с таким номером не найдена.")
            log_date = await user_today(update.effective_user.id)
        except ValueError as exc:
            await update.effective_message.reply_text(
                f"{exc}\nФормат: /habit_log ID | done|skipped | заметка|-",
                reply_markup=main_menu,
            )
            return
        except Exception:
            await update.effective_message.reply_text("Не удалось проверить привычку. Ничего не записано.", reply_markup=main_menu)
            return
        draft_id = uuid.uuid4().hex[:12]
        context.user_data["lifestyle_pending"] = {
            "draft_id": draft_id,
            "action": "habit_log",
            "payload": {**payload, "title": habit["title"], "log_date": log_date},
            "source_update_id": update.update_id,
        }
        status = "выполнено" if payload["status"] == "done" else "пропущено"
        await update.effective_message.reply_text(
            "Черновик отметки привычки:\n"
            f"Привычка: {habit['title']} (№{habit['id']})\n"
            f"Дата: {log_date.isoformat()}\n"
            f"Статус: {status}\n"
            f"Заметка: {payload['note'] or '—'}\n"
            "Если за сегодня уже есть отметка, она будет обновлена после подтверждения.",
            reply_markup=_keyboard(draft_id),
        )

    async def life_command(update: Any, context: Any) -> None:
        if not await _ready(update):
            return
        user_id = update.effective_user.id
        try:
            today = await user_today(user_id)
            start = today - timedelta(days=6)
            goals = await store.list_goals(user_id)
            habits = await store.list_habits(user_id)
            logs = await store.list_habit_logs(user_id, start_date=start, end_date=today)
            report = (
                render_goals(goals) + "\n\n"
                + render_habit_history(habits, logs, start_date=start, end_date=today)
            )
        except Exception:
            await update.effective_message.reply_text("Не удалось собрать сводку целей и привычек.", reply_markup=main_menu)
            return
        await update.effective_message.reply_text(report, reply_markup=main_menu)

    async def callback(update: Any, context: Any) -> None:
        query = update.callback_query
        if not is_authorized(update):
            await query.answer("Доступ запрещён.", show_alert=True)
            return
        if not enabled:
            await query.answer("Агент целей и привычек выключен.", show_alert=True)
            return
        await query.answer()
        parts = (query.data or "").split(":")
        action = parts[1] if len(parts) > 1 else ""
        token = parts[2] if len(parts) > 2 else ""
        draft = context.user_data.get("lifestyle_pending")
        if not draft or not token or draft.get("draft_id") != token:
            await query.edit_message_text(
                "Эта кнопка относится к устаревшему черновику. Используй последнее сообщение с предпросмотром."
            )
            return
        if action == "cancel":
            context.user_data.pop("lifestyle_pending", None)
            await query.edit_message_text("Отменено. Данные в Supabase не изменялись.")
            return
        if action != "confirm":
            await query.edit_message_text("Неизвестное действие. Повтори команду.")
            return
        user_id = update.effective_user.id
        payload = draft["payload"]
        try:
            if draft["action"] == "goal_create":
                saved = await store.create_goal(
                    user_id, **payload, source_update_id=draft["source_update_id"]
                )
                result_text = f"Цель сохранена: №{saved['id']} · {saved['title']}."
            elif draft["action"] == "goal_progress":
                result = await store.update_goal_progress(
                    user_id,
                    payload["goal_id"],
                    payload["progress"],
                    note=payload["note"],
                    source_update_id=draft["source_update_id"],
                )
                result_text = f"Прогресс цели «{payload['title']}» сохранён: {result['progress']}%."
                if result.get("duplicate"):
                    result_text = "Это обновление уже было обработано.\n" + result_text
            elif draft["action"] == "habit_create":
                saved = await store.create_habit(
                    user_id, **payload, source_update_id=draft["source_update_id"]
                )
                result_text = f"Привычка сохранена: №{saved['id']} · {saved['title']}."
            elif draft["action"] == "habit_log":
                saved = await store.log_habit(
                    user_id,
                    payload["habit_id"],
                    log_date=payload["log_date"],
                    status=payload["status"],
                    note=payload["note"],
                    source_update_id=draft["source_update_id"],
                )
                result_text = (
                    f"Отметка сохранена: {payload['title']} · "
                    f"{saved['log_date']} · {saved['status']}."
                )
            else:
                raise ValueError("Неизвестный тип черновика")
        except Exception:
            await query.edit_message_text(
                "Не удалось подтвердить действие. Я не сообщаю об успехе без проверки Supabase. "
                "Повторная обработка защищена ключом исходного Telegram update."
            )
            return
        context.user_data.pop("lifestyle_pending", None)
        await query.edit_message_text("✅ " + result_text)

    return {
        "goals": goals_command,
        "goal_add": goal_add_command,
        "goal_progress": goal_progress_command,
        "habits": habits_command,
        "habit_add": habit_add_command,
        "habit_log": habit_log_command,
        "life": life_command,
        "callback": callback,
    }
