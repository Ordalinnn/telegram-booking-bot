"""Кнопки бота.

Reply-клавиатура — постоянное меню под полем ввода.
Inline-кнопки — кнопки прямо под сообщением; при нажатии бот получает их callback_data.
"""
from aiogram.types import InlineKeyboardButton, KeyboardButton, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import LANGUAGES, LEVELS, MASTERS, SERVICES, money, service_price
from texts import t


def lang_kb():
    builder = InlineKeyboardBuilder()
    for code, label in LANGUAGES.items():
        builder.button(text=label, callback_data=f"lang:{code}")
    return builder.as_markup()


def main_menu(lang: str) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=t(lang, "btn_book"))],
            [KeyboardButton(text=t(lang, "btn_my")), KeyboardButton(text=t(lang, "btn_contacts"))],
            [KeyboardButton(text=t(lang, "btn_lang"))],
        ],
        resize_keyboard=True,
    )


def phone_kb(lang: str) -> ReplyKeyboardMarkup:
    # request_contact=True — Telegram сам отправит номер пользователя по нажатию
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=t(lang, "btn_send_phone"), request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def services_kb(lang: str):
    builder = InlineKeyboardBuilder()
    for key, service in SERVICES.items():
        cheapest = min(service["price"].values())
        builder.button(text=f"{service[lang]} — {t(lang, 'from_price', price=money(cheapest))}", callback_data=f"service:{key}")
    builder.button(text=t(lang, "btn_abort"), callback_data="abort")
    builder.adjust(1)  # по одной кнопке в ряд
    return builder.as_markup()


def masters_kb(lang: str, service: str):
    builder = InlineKeyboardBuilder()
    for key, master in MASTERS.items():
        level = LEVELS[master["level"]][lang]
        builder.button(text=f"{master['name']} · {level} — {money(service_price(service, key))}", callback_data=f"master:{key}")
    builder.adjust(1)
    builder.row(_back(lang, "back:service"), _abort(lang))
    return builder.as_markup()


def dates_kb(lang: str, dates: list[tuple[str, str]]):
    """dates — список пар (дата 2026-10-17, подпись «Сб 17.10»)."""
    builder = InlineKeyboardBuilder()
    for day, label in dates:
        builder.button(text=label, callback_data=f"date:{day}")
    builder.adjust(3)
    builder.row(_back(lang, "back:master"), _abort(lang))
    return builder.as_markup()


def times_kb(lang: str, times: list[str]):
    builder = InlineKeyboardBuilder()
    for slot in times:
        builder.button(text=slot, callback_data=f"time:{slot}")
    builder.adjust(4)
    builder.row(_back(lang, "back:date"), _abort(lang))
    return builder.as_markup()


def confirm_kb(lang: str):
    builder = InlineKeyboardBuilder()
    builder.button(text=t(lang, "btn_confirm"), callback_data="confirm")
    builder.button(text=t(lang, "btn_abort"), callback_data="abort")
    return builder.as_markup()


def my_bookings_kb(bookings: list[tuple[int, str]]):
    """bookings — список пар (номер записи, подпись кнопки)."""
    builder = InlineKeyboardBuilder()
    for booking_id, label in bookings:
        builder.button(text=label, callback_data=f"cancel:{booking_id}")
    builder.adjust(1)
    return builder.as_markup()


def reminder_kb(lang: str, booking_id: int):
    builder = InlineKeyboardBuilder()
    builder.button(text=t(lang, "btn_reminder_cancel"), callback_data=f"cancel:{booking_id}")
    return builder.as_markup()


def _back(lang: str, callback_data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=t(lang, "btn_back"), callback_data=callback_data)


def _abort(lang: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=t(lang, "btn_abort"), callback_data="abort")
