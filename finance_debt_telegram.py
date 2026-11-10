"""Telegram handlers for debt and budget workflows.

All write operations are previewed and require an explicit owner confirmation.
The module is disabled by default until the SQL proposal is applied and verified.
"""
from __future__ import annotations

import calendar
from datetime import date
from decimal import Decimal
from typing import Any, Callable

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from finance_debt_workflow import (
    parse_budget_set,
    parse_budget_weight,
    parse_debt_create,
    parse_debt_payment,
    parse_distribution,
    render_budget_report,
    render_debts,
    render_distribution,
    render_weights,
)
from finance_planning import money


def build_finance_debt_handlers(
    *,
    store: Any,
    enabled: bool,
    is_authorized: Callable[[Any], bool],
    reject_unauthorized: Callable[[Any], Any],
    user_today: Callable[[int], Any],
    load_transactions: Callable[[int, str, str], Any],
    format_money: Callable[[Any], str],
    main_menu: Any,
) -> dict[str, Callable[..., Any]]:
    """Create handlers with injected dependencies; safe to import without .env."""

    async def _ready(update: Any) -> bool:
        if not is_authorized(update):
            await reject_unauthorized(update)
            return False
        if not enabled:
            await update.effective_message.reply_text(
                "Учёт долгов и бюджетов пока выключен. Сначала нужно применить и проверить "
                "схему Supabase; до этого бот не обращается к новым таблицам и ничего не записывает.",
                reply_markup=main_menu,
            )
            return False
        return True

    def _confirm_keyboard() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Подтвердить", callback_data="debt:confirm"),
            InlineKeyboardButton("❌ Отмена", callback_data="debt:cancel"),
        ]])

    async def debts_command(update: Any, context: Any) -> None:
        if not await _ready(update):
            return
        try:
            rows = await store.list_debts(update.effective_user.id)
        except Exception:
            await update.effective_message.reply_text(
                "Не удалось прочитать долги. Проверь подключение к Supabase и наличие SQL-схемы.",
                reply_markup=main_menu,
            )
            return
        await update.effective_message.reply_text(render_debts(rows, format_money), reply_markup=main_menu)

    async def debt_add_command(update: Any, context: Any) -> None:
        if not await _ready(update):
            return
        try:
            draft = parse_debt_create(" ".join(context.args or []))
        except ValueError as exc:
            await update.effective_message.reply_text(
                "Формат:\n/debt_add направление | сумма | кому | за что | срок|- | заметка\n"
                "Пример:\n/debt_add owed_by_me | 12000 | Банк | Кредит | 2026-12-01 | Автокредит\n"
                "Если срока нет, укажи - и не добавляй заметку.",
                reply_markup=main_menu,
            )
            return
        context.user_data["finance_debt_pending"] = {
            "action": "create",
            "payload": draft,
            "source_update_id": update.update_id,
        }
        direction = "Я должен" if draft["direction"] == "owed_by_me" else "Мне должны"
        due = draft["due_date"].isoformat() if draft["due_date"] else "не указан"
        preview = (
            "Черновик нового долга:\n"
            f"Направление: {direction}\n"
            f"Контрагент: {draft['counterparty']}\n"
            f"Название: {draft['title']}\n"
            f"Сумма: {format_money(draft['amount'])}\n"
            f"Срок: {due}\n"
            f"Заметка: {draft['note'] or '—'}\n\n"
            "До подтверждения база не меняется."
        )
        await update.effective_message.reply_text(preview, reply_markup=_confirm_keyboard())

    async def debt_pay_command(update: Any, context: Any) -> None:
        if not await _ready(update):
            return
        try:
            parsed = parse_debt_payment(" ".join(context.args or []))
            debt = await store.get_debt(update.effective_user.id, parsed["debt_id"])
            if not debt or debt.get("status") != "open":
                raise ValueError("Открытый долг с таким номером не найден.")
            remaining = money(debt.get("amount_remaining", debt.get("amount")))
            if parsed["amount"] > remaining:
                raise ValueError(f"Сумма больше остатка долга: {format_money(remaining)}.")
        except ValueError as exc:
            await update.effective_message.reply_text(
                f"{exc}\nФормат: /debt_pay ID | сумма\nПример: /debt_pay 3 | 2500",
                reply_markup=main_menu,
            )
            return
        except Exception:
            await update.effective_message.reply_text(
                "Не удалось проверить долг. Ничего не записано.",
                reply_markup=main_menu,
            )
            return
        context.user_data["finance_debt_pending"] = {
            "action": "payment",
            "payload": {
                **parsed,
                "direction": debt["direction"],
                "counterparty": debt["counterparty"],
                "title": debt["title"],
                "amount_remaining": remaining,
                "payment_date": await user_today(update.effective_user.id),
            },
            "source_update_id": update.update_id,
        }
        sign = "расход" if debt["direction"] == "owed_by_me" else "доход"
        await update.effective_message.reply_text(
            "Подтверди погашение долга:\n"
            f"№{debt['id']} · {debt['counterparty']} · {debt['title']}\n"
            f"Сумма: {format_money(parsed['amount'])} ({sign})\n"
            f"Остаток после операции: {format_money(remaining - parsed['amount'])}\n"
            f"Дата: {(await user_today(update.effective_user.id)).isoformat()}\n\n"
            "Операция и финансовая транзакция будут записаны одной транзакцией PostgreSQL.",
            reply_markup=_confirm_keyboard(),
        )

    async def budgets_command(update: Any, context: Any) -> None:
        if not await _ready(update):
            return
        user_id = update.effective_user.id
        try:
            today = await user_today(user_id)
            start = date(today.year, today.month, 1)
            end = date(today.year, today.month, calendar.monthrange(today.year, today.month)[1])
            budgets = await store.list_budgets(user_id, today)
            transactions = await load_transactions(user_id, start.isoformat(), end.isoformat())
            report = render_budget_report(transactions, budgets, today, format_money)
        except Exception:
            await update.effective_message.reply_text(
                "Не удалось собрать бюджет по данным Supabase.",
                reply_markup=main_menu,
            )
            return
        await update.effective_message.reply_text(report, reply_markup=main_menu)

    async def budget_set_command(update: Any, context: Any) -> None:
        if not await _ready(update):
            return
        try:
            today = await user_today(update.effective_user.id)
            draft = parse_budget_set(" ".join(context.args or []), today)
        except ValueError as exc:
            await update.effective_message.reply_text(
                f"{exc}\nФормат: /budget_set Категория | лимит\n"
                "Или: /budget_set ГГГГ-ММ | Категория | лимит",
                reply_markup=main_menu,
            )
            return
        context.user_data["finance_debt_pending"] = {
            "action": "budget",
            "payload": draft,
            "source_update_id": update.update_id,
        }
        await update.effective_message.reply_text(
            "Черновик месячного бюджета:\n"
            f"Месяц: {draft['period_month'].strftime('%Y-%m')}\n"
            f"Категория: {draft['category']}\n"
            f"Лимит: {format_money(draft['limit_amount'])}\n\n"
            "До подтверждения база не меняется.",
            reply_markup=_confirm_keyboard(),
        )

    async def budget_weights_command(update: Any, context: Any) -> None:
        if not await _ready(update):
            return
        try:
            rows = await store.list_weights(update.effective_user.id)
        except Exception:
            await update.effective_message.reply_text(
                "Не удалось получить веса категорий.", reply_markup=main_menu
            )
            return
        await update.effective_message.reply_text(render_weights(rows), reply_markup=main_menu)

    async def budget_weight_command(update: Any, context: Any) -> None:
        if not await _ready(update):
            return
        try:
            draft = parse_budget_weight(" ".join(context.args or []))
        except ValueError as exc:
            await update.effective_message.reply_text(
                f"{exc}\nФормат: /budget_weight Категория | вес\nПример: /budget_weight Еда | 2",
                reply_markup=main_menu,
            )
            return
        context.user_data["finance_debt_pending"] = {
            "action": "weight",
            "payload": draft,
            "source_update_id": update.update_id,
        }
        await update.effective_message.reply_text(
            "Черновик веса для распределения денег:\n"
            f"Категория: {draft['category']}\nВес: {draft['weight']}\n\n"
            "Вес — относительная доля, а не процент. До подтверждения база не меняется.",
            reply_markup=_confirm_keyboard(),
        )

    async def allocate_command(update: Any, context: Any) -> None:
        if not await _ready(update):
            return
        try:
            values = parse_distribution(" ".join(context.args or []))
            weights = await store.list_weights(update.effective_user.id)
            weight_map = {row["category"]: row["weight"] for row in weights}
            report = render_distribution(values, weight_map, format_money)
        except ValueError as exc:
            await update.effective_message.reply_text(
                f"{exc}\nФормат: /allocate доход | обязательные расходы | долги | накопления",
                reply_markup=main_menu,
            )
            return
        except Exception:
            await update.effective_message.reply_text(
                "Не удалось получить веса категорий. Распределение не рассчитано.",
                reply_markup=main_menu,
            )
            return
        await update.effective_message.reply_text(report, reply_markup=main_menu)

    async def debt_callback(update: Any, context: Any) -> None:
        query = update.callback_query
        if not is_authorized(update):
            await query.answer("Доступ запрещён.", show_alert=True)
            return
        if not enabled:
            await query.answer("Учёт долгов и бюджетов выключен.", show_alert=True)
            return
        await query.answer()
        action = (query.data or "").split(":", 1)[1]
        if action == "cancel":
            context.user_data.pop("finance_debt_pending", None)
            await query.edit_message_text("Отменено. Данные в Supabase не изменялись.")
            return
        draft = context.user_data.get("finance_debt_pending")
        if action != "confirm" or not draft:
            await query.edit_message_text("Черновик не найден или уже обработан. Повтори команду.")
            return
        user_id = update.effective_user.id
        payload = draft["payload"]
        try:
            if draft["action"] == "create":
                saved = await store.create_debt(
                    user_id,
                    **payload,
                    source_update_id=draft["source_update_id"],
                )
                result_text = (
                    f"Долг сохранён: №{saved['id']} · {saved['counterparty']} · {saved['title']}\n"
                    f"Остаток: {format_money(saved.get('amount_remaining', saved['amount']))}"
                )
            elif draft["action"] == "payment":
                result = await store.record_payment(
                    user_id,
                    payload["debt_id"],
                    payload["amount"],
                    payment_date=payload["payment_date"],
                    source_update_id=draft["source_update_id"],
                )
                result_text = (
                    f"Погашение записано. Финансовая транзакция №{result.get('transaction_id')} создана.\n"
                    f"Остаток долга: {format_money(result['amount_remaining'])}. "
                    f"Статус: {result['status']}."
                )
                if result.get("duplicate"):
                    result_text = "Это подтверждение уже обработано. Повторная операция не создана.\n" + result_text
            elif draft["action"] == "budget":
                await store.set_budget(user_id, **payload)
                rows = await store.list_budgets(user_id, payload["period_month"])
                saved = next(
                    (row for row in rows if row["category"] == payload["category"]),
                    None,
                )
                if not saved or money(saved["limit_amount"]) != payload["limit_amount"]:
                    raise RuntimeError("Не удалось подтвердить сохранение бюджета чтением из Supabase")
                result_text = (
                    f"Лимит сохранён: {saved['category']} · "
                    f"{format_money(saved['limit_amount'])} за {payload['period_month'].strftime('%Y-%m')}."
                )
            elif draft["action"] == "weight":
                await store.set_weight(user_id, **payload)
                rows = await store.list_weights(user_id)
                saved = next((row for row in rows if row["category"] == payload["category"]), None)
                if not saved or money(saved["weight"]) != payload["weight"]:
                    raise RuntimeError("Не удалось подтвердить сохранение веса чтением из Supabase")
                result_text = f"Вес сохранён: {saved['category']} · {saved['weight']}."
            else:
                raise ValueError("Неизвестный тип черновика")
        except Exception:
            await query.edit_message_text(
                "Не удалось подтвердить операцию. Я не сообщаю об успехе без проверки Supabase. "
                "Если часть операции уже прошла, повтор будет защищён ключом идемпотентности."
            )
            return
        context.user_data.pop("finance_debt_pending", None)
        await query.edit_message_text("✅ " + result_text)

    return {
        "debts": debts_command,
        "debt_add": debt_add_command,
        "debt_pay": debt_pay_command,
        "budgets": budgets_command,
        "budget_set": budget_set_command,
        "budget_weights": budget_weights_command,
        "budget_weight": budget_weight_command,
        "allocate": allocate_command,
        "callback": debt_callback,
    }
