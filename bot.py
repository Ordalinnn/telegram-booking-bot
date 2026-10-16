"""Telegram-бот для записи клиентов. Запуск: python bot.py"""
import asyncio
import logging
import re
import sys
from datetime import date, datetime, timedelta
from html import escape
from logging.handlers import RotatingFileHandler
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BotCommand, CallbackQuery, Message

import db
import keyboards as kb
from config import (
    ADMIN_ID, BOT_TOKEN, BUSINESS_NAME, CLOSE_HOUR, CONTACTS, DAYS_AHEAD,
    LATE_REMIND_HOURS, OPEN_HOUR, REMIND_HOURS, SERVICES, WORK_DAYS, money,
)

dp = Dispatcher()

WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
PHONE_RE = re.compile(r"^\+?\d{10,15}$")


class Booking(StatesGroup):
    """Шаги записи. Бот помнит, на каком шаге каждый пользователь (это называется FSM)."""
    service = State()
    date = State()
    time = State()
    name = State()
    phone = State()
    confirm = State()


# ---------- Вспомогательные функции ----------

def format_date(day: str) -> str:
    """2026-10-17 → «Сб 17.10»"""
    d = date.fromisoformat(day)
    return f"{WEEKDAYS[d.weekday()]} {d:%d.%m}"


def free_times(day: str) -> list[str]:
    """Свободное время на день. Сегодня прошедшие часы не предлагаем."""
    taken = db.taken_times(day)
    now = datetime.now()
    times = []
    for hour in range(OPEN_HOUR, CLOSE_HOUR):
        slot = f"{hour:02d}:00"
        if slot in taken:
            continue
        if day == now.date().isoformat() and hour <= now.hour:
            continue
        times.append(slot)
    return times


def available_dates() -> list[str]:
    """Ближайшие рабочие дни, в которых есть хотя бы одно свободное время."""
    result = []
    for offset in range(60):
        day = date.today() + timedelta(days=offset)
        if day.weekday() in WORK_DAYS and free_times(day.isoformat()):
            result.append(day.isoformat())
            if len(result) == DAYS_AHEAD:
                break
    return result


def booking_text(service: str, day: str, time: str, name: str, phone: str) -> str:
    # escape() защищает от поломки разметки, если клиент введёт в имени символы < или >
    return (
        f"💈 Услуга: <b>{escape(service)}</b>\n"
        f"📅 Дата: <b>{format_date(day)}, {time}</b>\n"
        f"👤 Имя: {escape(name)}\n"
        f"📞 Телефон: {escape(phone)}"
    )


def summary(data: dict) -> str:
    """Текст записи из данных, которые клиент ввёл по шагам."""
    service_name = SERVICES[data["service"]][0]
    return booking_text(service_name, data["date"], data["time"], data["name"], data["phone"])


def reminder_time(day: str, time: str) -> str | None:
    """Когда напомнить о визите: за сутки, а если до визита меньше суток — за пару часов.

    None — визит слишком скоро, напоминать нет смысла.
    """
    visit = datetime.fromisoformat(f"{day} {time}")
    now = datetime.now()
    for hours in (REMIND_HOURS, LATE_REMIND_HOURS):
        remind_at = visit - timedelta(hours=hours)
        if remind_at > now + timedelta(minutes=30):
            return remind_at.isoformat(sep=" ", timespec="seconds")
    return None


async def notify_admin(bot: Bot, text: str) -> None:
    if not ADMIN_ID:
        return
    try:
        await bot.send_message(ADMIN_ID, text)
    except Exception:
        # Чаще всего причина в том, что администратор ни разу не нажал /start в боте
        logging.exception("Не удалось отправить уведомление администратору")


# ---------- Команды и главное меню ----------
# Порядок важен: обработчики проверяются сверху вниз, срабатывает первый подходящий.

@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        f"Здравствуйте! Это бот записи в {BUSINESS_NAME}.\n\nВыберите действие в меню ниже 👇",
        reply_markup=kb.main_menu(),
    )


@dp.message(Command("myid"))
async def cmd_myid(message: Message):
    await message.answer(f"Ваш Telegram ID: <code>{message.from_user.id}</code>")


@dp.message(Command("bookings"))
async def cmd_bookings(message: Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("Эта команда доступна только администратору.")
        return
    rows = db.upcoming_bookings()
    if not rows:
        await message.answer("Предстоящих записей нет.")
        return
    blocks = [
        f"#{row['id']}\n" + booking_text(row["service"], row["date"], row["time"], row["name"], row["phone"])
        for row in rows
    ]
    await message.answer("📋 Предстоящие записи:\n\n" + "\n\n".join(blocks))


@dp.message(F.text == kb.BTN_CONTACTS)
async def show_contacts(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(CONTACTS, reply_markup=kb.main_menu())


@dp.message(F.text == kb.BTN_MY)
async def my_bookings(message: Message, state: FSMContext):
    await state.clear()
    rows = db.user_bookings(message.from_user.id)
    if not rows:
        await message.answer("У вас нет предстоящих записей.", reply_markup=kb.main_menu())
        return
    lines = [f"• {format_date(row['date'])}, {row['time']} — {escape(row['service'])}" for row in rows]
    buttons = [(row["id"], f"❌ Отменить {format_date(row['date'])}, {row['time']}") for row in rows]
    await message.answer(
        "Ваши записи:\n\n" + "\n".join(lines),
        reply_markup=kb.my_bookings_kb(buttons),
    )


@dp.callback_query(F.data.startswith("cancel:"))
async def cancel_booking(callback: CallbackQuery):
    booking_id = int(callback.data.split(":", 1)[1])
    row = db.cancel_booking(booking_id, callback.from_user.id)
    if row is None:
        await callback.answer("Запись не найдена или уже отменена.", show_alert=True)
        return
    when = f"{format_date(row['date'])}, {row['time']}"
    await callback.message.edit_text(f"Запись на <b>{when}</b> отменена.")
    await callback.answer()
    await notify_admin(
        callback.bot,
        f"❌ Клиент отменил запись #{row['id']}\n\n"
        + booking_text(row["service"], row["date"], row["time"], row["name"], row["phone"]),
    )


# ---------- Запись: шаг за шагом ----------

@dp.message(F.text == kb.BTN_BOOK)
@dp.message(Command("book"))
async def start_booking(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(Booking.service)
    await message.answer("Выберите услугу:", reply_markup=kb.services_kb())


@dp.callback_query(F.data == "abort")
async def abort_booking(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("Запись отменена. Чтобы начать заново, нажмите «📅 Записаться».")
    await callback.answer()


@dp.callback_query(Booking.service, F.data.startswith("service:"))
async def choose_service(callback: CallbackQuery, state: FSMContext):
    key = callback.data.split(":", 1)[1]
    if key not in SERVICES:
        await callback.answer()
        return
    await state.update_data(service=key)
    await show_dates(callback, state)


async def show_dates(callback: CallbackQuery, state: FSMContext):
    dates = available_dates()
    if not dates:
        await state.clear()
        await callback.message.edit_text("К сожалению, на ближайшие дни свободного времени нет. Позвоните нам 🙏")
        await callback.answer()
        return
    data = await state.get_data()
    name, price = SERVICES[data["service"]]
    await state.set_state(Booking.date)
    await callback.message.edit_text(
        f"Услуга: <b>{name}</b> — {money(price)}\n\nВыберите день:",
        reply_markup=kb.dates_kb([(day, format_date(day)) for day in dates]),
    )
    await callback.answer()


@dp.callback_query(Booking.date, F.data == "back:service")
async def back_to_services(callback: CallbackQuery, state: FSMContext):
    await state.set_state(Booking.service)
    await callback.message.edit_text("Выберите услугу:", reply_markup=kb.services_kb())
    await callback.answer()


@dp.callback_query(Booking.date, F.data.startswith("date:"))
async def choose_date(callback: CallbackQuery, state: FSMContext):
    day = callback.data.split(":", 1)[1]
    times = free_times(day)
    if not times:
        await callback.answer("На этот день всё занято, выберите другой.", show_alert=True)
        return
    await state.update_data(date=day)
    await state.set_state(Booking.time)
    await callback.message.edit_text(
        f"<b>{format_date(day)}</b> — выберите время:",
        reply_markup=kb.times_kb(times),
    )
    await callback.answer()


@dp.callback_query(Booking.time, F.data == "back:date")
async def back_to_dates(callback: CallbackQuery, state: FSMContext):
    await show_dates(callback, state)


@dp.callback_query(Booking.time, F.data.startswith("time:"))
async def choose_time(callback: CallbackQuery, state: FSMContext):
    slot = callback.data.split(":", 1)[1]
    data = await state.get_data()
    if slot not in free_times(data["date"]):
        await callback.answer("Это время уже заняли, выберите другое.", show_alert=True)
        return
    await state.update_data(time=slot)
    await state.set_state(Booking.name)
    await callback.message.edit_text(f"Вы выбрали: <b>{format_date(data['date'])}, {slot}</b>")
    await callback.message.answer("Как вас зовут?")
    await callback.answer()


@dp.message(Booking.name, F.text)
async def enter_name(message: Message, state: FSMContext):
    name = message.text.strip()
    if not 2 <= len(name) <= 50:
        await message.answer("Пожалуйста, введите имя (от 2 до 50 символов).")
        return
    await state.update_data(name=name)
    await state.set_state(Booking.phone)
    await message.answer(
        "Отправьте номер телефона кнопкой ниже или напишите его вручную:",
        reply_markup=kb.phone_kb(),
    )


@dp.message(Booking.phone, F.contact)
async def enter_phone_contact(message: Message, state: FSMContext):
    await save_phone(message, state, message.contact.phone_number)


@dp.message(Booking.phone, F.text)
async def enter_phone_text(message: Message, state: FSMContext):
    phone = re.sub(r"[\s\-()]", "", message.text)  # убираем пробелы, дефисы и скобки
    if not PHONE_RE.match(phone):
        await message.answer("Не похоже на номер телефона. Пример: +79001234567")
        return
    await save_phone(message, state, phone)


async def save_phone(message: Message, state: FSMContext, phone: str):
    await state.update_data(phone=phone)
    await state.set_state(Booking.confirm)
    data = await state.get_data()
    await message.answer("Почти готово!", reply_markup=kb.main_menu())
    await message.answer(summary(data) + "\n\nВсё верно?", reply_markup=kb.confirm_kb())


@dp.callback_query(Booking.confirm, F.data == "confirm")
async def confirm_booking(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await state.clear()
    booking_id = None
    # Повторная проверка: пока клиент вводил имя, время могли занять или оно уже прошло
    if data["time"] in free_times(data["date"]):
        booking_id = db.add_booking(
            callback.from_user.id, data["name"], data["phone"],
            SERVICES[data["service"]][0], data["date"], data["time"],
            reminder_time(data["date"], data["time"]),
        )
    if booking_id is None:
        await callback.message.edit_text(
            "😔 Пока вы оформляли запись, это время стало недоступно. "
            "Нажмите «📅 Записаться» и выберите другое."
        )
        await callback.answer()
        return
    await callback.message.edit_text(
        f"✅ Вы записаны!\n\n{summary(data)}\n\n"
        "Ждём вас! Мы пришлём напоминание перед визитом. "
        "Отменить запись можно в разделе «📋 Мои записи»."
    )
    await callback.answer()
    await notify_admin(callback.bot, f"🆕 Новая запись #{booking_id}\n\n{summary(data)}")


# ---------- Если пользователь сделал что-то неожиданное ----------

@dp.message(Booking.name)
async def name_not_text(message: Message):
    await message.answer("Напишите, пожалуйста, имя текстом.")


@dp.message(Booking.phone)
async def phone_not_text(message: Message):
    await message.answer("Отправьте номер кнопкой «📱 Отправить мой номер» или напишите его.")


@dp.message(StateFilter(Booking.service, Booking.date, Booking.time, Booking.confirm))
async def use_buttons(message: Message):
    await message.answer("Пожалуйста, выберите вариант кнопками в сообщении выше 👆")


@dp.message()
async def unknown_message(message: Message):
    await message.answer("Не понял вас 🙂 Воспользуйтесь меню ниже.", reply_markup=kb.main_menu())


@dp.callback_query()
async def outdated_button(callback: CallbackQuery):
    # Нажатие на кнопку из старого сообщения, когда запись уже завершена или начата заново
    await callback.answer("Эта кнопка устарела. Нажмите «📅 Записаться», чтобы начать заново.", show_alert=True)


# ---------- Напоминания ----------

async def send_due_reminders(bot: Bot) -> None:
    now = datetime.now().isoformat(sep=" ", timespec="seconds")
    for row in db.due_reminders(now):
        visit_day = date.fromisoformat(row["date"])
        if visit_day == date.today():
            when = "сегодня"
        elif visit_day == date.today() + timedelta(days=1):
            when = "завтра"
        else:
            when = format_date(row["date"])
        try:
            await bot.send_message(
                row["user_id"],
                f"⏰ Напоминаем: вы записаны в {BUSINESS_NAME} <b>{when} в {row['time']}</b>.\n"
                f"Услуга: {escape(row['service'])}\n\n"
                "Если планы изменились, отмените запись кнопкой ниже — так время освободится для других.",
                reply_markup=kb.reminder_kb(row["id"]),
            )
        except Exception:
            # Например, клиент заблокировал бота. Повторять не будем, иначе ошибка будет каждую минуту
            logging.exception("Не удалось отправить напоминание по записи #%s", row["id"])
        db.mark_reminded(row["id"])


async def reminder_loop(bot: Bot) -> None:
    """Раз в минуту проверяет, не пора ли кому-нибудь напомнить о визите."""
    while True:
        try:
            await send_due_reminders(bot)
        except Exception:
            logging.exception("Ошибка при отправке напоминаний")
        await asyncio.sleep(60)


# ---------- Запуск ----------

def setup_logging() -> None:
    """Журнал пишется в bot.log рядом с ботом (и в консоль, если она есть)."""
    handlers: list[logging.Handler] = [
        RotatingFileHandler(Path(__file__).with_name("bot.log"), maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    ]
    if sys.stderr is not None:  # при фоновом запуске через pythonw консоли нет
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=handlers)


async def main():
    setup_logging()
    db.init_db()
    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    await bot.set_my_commands([
        BotCommand(command="start", description="Главное меню"),
        BotCommand(command="book", description="Записаться"),
    ])
    # Напоминания работают параллельно с приёмом сообщений
    reminders = asyncio.create_task(reminder_loop(bot))
    logging.info("Бот запущен")
    try:
        await dp.start_polling(bot)
    finally:
        reminders.cancel()


if __name__ == "__main__":
    asyncio.run(main())
