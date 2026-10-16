"""Кнопки бота.

Reply-клавиатура — постоянное меню под полем ввода.
Inline-кнопки — кнопки прямо под сообщением; при нажатии бот получает их callback_data.
"""
from aiogram.types import InlineKeyboardButton, KeyboardButton, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import SERVICES

BTN_BOOK = "📅 Записаться"
BTN_MY = "📋 Мои записи"
BTN_CONTACTS = "📞 Контакты"


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_BOOK)],
            [KeyboardButton(text=BTN_MY), KeyboardButton(text=BTN_CONTACTS)],
        ],
        resize_keyboard=True,
    )


def phone_kb() -> ReplyKeyboardMarkup:
    # request_contact=True — Telegram сам отправит номер пользователя по нажатию
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Отправить мой номер", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def services_kb():
    builder = InlineKeyboardBuilder()
    for key, (name, price) in SERVICES.items():
        builder.button(text=f"{name} — {price} ₽", callback_data=f"service:{key}")
    builder.button(text="✖ Отмена", callback_data="abort")
    builder.adjust(1)  # по одной кнопке в ряд
    return builder.as_markup()


def dates_kb(dates: list[tuple[str, str]]):
    """dates — список пар (дата 2026-10-17, подпись «Пт 17.10»)."""
    builder = InlineKeyboardBuilder()
    for day, label in dates:
        builder.button(text=label, callback_data=f"date:{day}")
    builder.adjust(3)
    builder.row(_back("back:service"), _abort())
    return builder.as_markup()


def times_kb(times: list[str]):
    builder = InlineKeyboardBuilder()
    for slot in times:
        builder.button(text=slot, callback_data=f"time:{slot}")
    builder.adjust(4)
    builder.row(_back("back:date"), _abort())
    return builder.as_markup()


def confirm_kb():
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Подтвердить", callback_data="confirm")
    builder.button(text="✖ Отмена", callback_data="abort")
    return builder.as_markup()


def my_bookings_kb(bookings: list[tuple[int, str]]):
    """bookings — список пар (номер записи, подпись кнопки)."""
    builder = InlineKeyboardBuilder()
    for booking_id, label in bookings:
        builder.button(text=label, callback_data=f"cancel:{booking_id}")
    builder.adjust(1)
    return builder.as_markup()


def reminder_kb(booking_id: int):
    builder = InlineKeyboardBuilder()
    builder.button(text="❌ Не смогу прийти — отменить", callback_data=f"cancel:{booking_id}")
    return builder.as_markup()


def _back(callback_data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text="◀ Назад", callback_data=callback_data)


def _abort() -> InlineKeyboardButton:
    return InlineKeyboardButton(text="✖ Отмена", callback_data="abort")
