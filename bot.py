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
    ADMIN_ID, ADMIN_LANG, BOT_TOKEN, BUSINESS_NAME, CLOSE_HOUR, CONTACTS, DAYS_AHEAD, LANGUAGES,
    LATE_REMIND_HOURS, LEVELS, MASTERS, OPEN_HOUR, REMIND_HOURS, SERVICES, WORK_DAYS, money, service_price,
)
from texts import WEEKDAYS, all_langs, t

dp = Dispatcher()

PHONE_RE = re.compile(r"^\+?\d{10,15}$")


class Booking(StatesGroup):
    """Шаги записи. Бот помнит, на каком шаге каждый пользователь (это называется FSM)."""
    service = State()
    master = State()
    date = State()
    time = State()
    name = State()
    phone = State()
    confirm = State()


# ---------- Вспомогательные функции ----------

def lang_of(user_id: int) -> str:
    return db.get_lang(user_id) or next(iter(LANGUAGES))


def format_date(day: str, lang: str) -> str:
    """2026-10-17 → «Сб 17.10» / «Сн 17.10»"""
    d = date.fromisoformat(day)
    return f"{WEEKDAYS[lang][d.weekday()]} {d:%d.%m}"


def service_label(service: str, lang: str) -> str:
    # В старых записях вместо ключа услуги хранится её название — показываем как есть
    return SERVICES[service][lang] if service in SERVICES else service


def master_label(master: str, lang: str) -> str:
    if master not in MASTERS:
        return master
    return f"{MASTERS[master]['name']} ({LEVELS[MASTERS[master]['level']][lang]})"


def free_times(master: str, day: str) -> list[str]:
    """Свободное время мастера на день. Сегодня прошедшие часы не предлагаем."""
    taken = db.taken_times(master, day)
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


def available_dates(master: str) -> list[str]:
    """Ближайшие рабочие дни, в которых у мастера есть хотя бы одно свободное время."""
    result = []
    for offset in range(60):
        day = date.today() + timedelta(days=offset)
        if day.weekday() in WORK_DAYS and free_times(master, day.isoformat()):
            result.append(day.isoformat())
            if len(result) == DAYS_AHEAD:
                break
    return result


def booking_text(lang: str, service: str, master: str, day: str, time: str, name: str, phone: str) -> str:
    # escape() защищает от поломки разметки, если клиент введёт в имени символы < или >
    service_line = escape(service_label(service, lang))
    if service in SERVICES and master in MASTERS:
        service_line += f" — {money(service_price(service, master))}"
    return (
        f"{t(lang, 'l_service')}: <b>{service_line}</b>\n"
        f"{t(lang, 'l_master')}: {escape(master_label(master, lang))}\n"
        f"{t(lang, 'l_date')}: <b>{format_date(day, lang)}, {time}</b>\n"
        f"{t(lang, 'l_name')}: {escape(name)}\n"
        f"{t(lang, 'l_phone')}: {escape(phone)}"
    )


def row_text(lang: str, row) -> str:
    return booking_text(lang, row["service"], row["master"], row["date"], row["time"], row["name"], row["phone"])


def summary(lang: str, data: dict) -> str:
    """Текст записи из данных, которые клиент ввёл по шагам."""
    return booking_text(lang, data["service"], data["master"], data["date"], data["time"], data["name"], data["phone"])


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


async def show_welcome(message: Message, lang: str) -> None:
    await message.answer(t(lang, "welcome", business=BUSINESS_NAME), reply_markup=kb.main_menu(lang))


# ---------- Язык, команды и главное меню ----------
# Порядок важен: обработчики проверяются сверху вниз, срабатывает первый подходящий.

@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    lang = db.get_lang(message.from_user.id)
    if lang is None:
        await message.answer(t("ru", "choose_lang"), reply_markup=kb.lang_kb())
        return
    await show_welcome(message, lang)


@dp.message(F.text.in_(all_langs("btn_lang")))
async def ask_lang(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(t("ru", "choose_lang"), reply_markup=kb.lang_kb())


@dp.callback_query(F.data.startswith("lang:"))
async def choose_lang(callback: CallbackQuery, state: FSMContext):
    lang = callback.data.split(":", 1)[1]
    if lang not in LANGUAGES:
        await callback.answer()
        return
    await state.clear()
    db.set_lang(callback.from_user.id, lang)
    await callback.message.edit_text(t(lang, "lang_set"))
    await callback.answer()
    await show_welcome(callback.message, lang)


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
    blocks = [f"#{row['id']}\n" + row_text(ADMIN_LANG, row) for row in rows]
    await message.answer("📋 Предстоящие записи:\n\n" + "\n\n".join(blocks))


@dp.message(F.text.in_(all_langs("btn_contacts")))
async def show_contacts(message: Message, state: FSMContext):
    await state.clear()
    lang = lang_of(message.from_user.id)
    await message.answer(CONTACTS[lang], reply_markup=kb.main_menu(lang))


@dp.message(F.text.in_(all_langs("btn_my")))
async def my_bookings(message: Message, state: FSMContext):
    await state.clear()
    lang = lang_of(message.from_user.id)
    rows = db.user_bookings(message.from_user.id)
    if not rows:
        await message.answer(t(lang, "no_bookings"), reply_markup=kb.main_menu(lang))
        return
    lines = [
        f"• {format_date(row['date'], lang)}, {row['time']} — {escape(service_label(row['service'], lang))}, "
        f"{escape(master_label(row['master'], lang))}"
        for row in rows
    ]
    buttons = [
        (row["id"], t(lang, "btn_cancel_booking", when=f"{format_date(row['date'], lang)}, {row['time']}"))
        for row in rows
    ]
    await message.answer(t(lang, "your_bookings") + "\n\n" + "\n".join(lines), reply_markup=kb.my_bookings_kb(buttons))


@dp.callback_query(F.data.startswith("cancel:"))
async def cancel_booking(callback: CallbackQuery):
    lang = lang_of(callback.from_user.id)
    booking_id = int(callback.data.split(":", 1)[1])
    row = db.cancel_booking(booking_id, callback.from_user.id)
    if row is None:
        await callback.answer(t(lang, "not_found"), show_alert=True)
        return
    await callback.message.edit_text(t(lang, "cancelled", when=f"{format_date(row['date'], lang)}, {row['time']}"))
    await callback.answer()
    await notify_admin(callback.bot, f"❌ Клиент отменил запись #{row['id']}\n\n" + row_text(ADMIN_LANG, row))


# ---------- Запись: шаг за шагом ----------

@dp.message(F.text.in_(all_langs("btn_book")))
@dp.message(Command("book"))
async def start_booking(message: Message, state: FSMContext):
    await state.clear()
    lang = lang_of(message.from_user.id)
    await state.set_state(Booking.service)
    await message.answer(t(lang, "choose_service"), reply_markup=kb.services_kb(lang))


@dp.callback_query(F.data == "abort")
async def abort_booking(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    lang = lang_of(callback.from_user.id)
    await callback.message.edit_text(t(lang, "aborted", btn_book=t(lang, "btn_book")))
    await callback.answer()


@dp.callback_query(Booking.service, F.data.startswith("service:"))
async def choose_service(callback: CallbackQuery, state: FSMContext):
    key = callback.data.split(":", 1)[1]
    if key not in SERVICES:
        await callback.answer()
        return
    await state.update_data(service=key)
    await show_masters(callback, state)


async def show_masters(callback: CallbackQuery, state: FSMContext):
    lang = lang_of(callback.from_user.id)
    data = await state.get_data()
    await state.set_state(Booking.master)
    await callback.message.edit_text(
        t(lang, "choose_master", service=SERVICES[data["service"]][lang]),
        reply_markup=kb.masters_kb(lang, data["service"]),
    )
    await callback.answer()


@dp.callback_query(Booking.master, F.data == "back:service")
async def back_to_services(callback: CallbackQuery, state: FSMContext):
    lang = lang_of(callback.from_user.id)
    await state.set_state(Booking.service)
    await callback.message.edit_text(t(lang, "choose_service"), reply_markup=kb.services_kb(lang))
    await callback.answer()


@dp.callback_query(Booking.master, F.data.startswith("master:"))
async def choose_master(callback: CallbackQuery, state: FSMContext):
    key = callback.data.split(":", 1)[1]
    if key not in MASTERS:
        await callback.answer()
        return
    if not available_dates(key):
        await callback.answer(t(lang_of(callback.from_user.id), "no_dates"), show_alert=True)
        return
    await state.update_data(master=key)
    await show_dates(callback, state)


async def show_dates(callback: CallbackQuery, state: FSMContext):
    lang = lang_of(callback.from_user.id)
    data = await state.get_data()
    dates = available_dates(data["master"])
    await state.set_state(Booking.date)
    await callback.message.edit_text(
        t(
            lang, "choose_day",
            service=SERVICES[data["service"]][lang],
            master=master_label(data["master"], lang),
            price=money(service_price(data["service"], data["master"])),
        ),
        reply_markup=kb.dates_kb(lang, [(day, format_date(day, lang)) for day in dates]),
    )
    await callback.answer()


@dp.callback_query(Booking.date, F.data == "back:master")
async def back_to_masters(callback: CallbackQuery, state: FSMContext):
    await show_masters(callback, state)


@dp.callback_query(Booking.date, F.data.startswith("date:"))
async def choose_date(callback: CallbackQuery, state: FSMContext):
    lang = lang_of(callback.from_user.id)
    data = await state.get_data()
    day = callback.data.split(":", 1)[1]
    times = free_times(data["master"], day)
    if not times:
        await callback.answer(t(lang, "day_full"), show_alert=True)
        return
    await state.update_data(date=day)
    await state.set_state(Booking.time)
    await callback.message.edit_text(
        t(lang, "choose_time", day=format_date(day, lang)),
        reply_markup=kb.times_kb(lang, times),
    )
    await callback.answer()


@dp.callback_query(Booking.time, F.data == "back:date")
async def back_to_dates(callback: CallbackQuery, state: FSMContext):
    await show_dates(callback, state)


@dp.callback_query(Booking.time, F.data.startswith("time:"))
async def choose_time(callback: CallbackQuery, state: FSMContext):
    lang = lang_of(callback.from_user.id)
    slot = callback.data.split(":", 1)[1]
    data = await state.get_data()
    if slot not in free_times(data["master"], data["date"]):
        await callback.answer(t(lang, "time_taken"), show_alert=True)
        return
    await state.update_data(time=slot)
    await state.set_state(Booking.name)
    await callback.message.edit_text(t(lang, "you_chose", day=format_date(data["date"], lang), time=slot))
    await callback.message.answer(t(lang, "ask_name"))
    await callback.answer()


@dp.message(Booking.name, F.text)
async def enter_name(message: Message, state: FSMContext):
    lang = lang_of(message.from_user.id)
    name = message.text.strip()
    if not 2 <= len(name) <= 50:
        await message.answer(t(lang, "bad_name"))
        return
    await state.update_data(name=name)
    await state.set_state(Booking.phone)
    await message.answer(t(lang, "ask_phone"), reply_markup=kb.phone_kb(lang))


@dp.message(Booking.phone, F.contact)
async def enter_phone_contact(message: Message, state: FSMContext):
    await save_phone(message, state, message.contact.phone_number)


@dp.message(Booking.phone, F.text)
async def enter_phone_text(message: Message, state: FSMContext):
    phone = re.sub(r"[\s\-()]", "", message.text)  # убираем пробелы, дефисы и скобки
    if not PHONE_RE.match(phone):
        await message.answer(t(lang_of(message.from_user.id), "bad_phone"))
        return
    await save_phone(message, state, phone)


async def save_phone(message: Message, state: FSMContext, phone: str):
    lang = lang_of(message.from_user.id)
    await state.update_data(phone=phone)
    await state.set_state(Booking.confirm)
    data = await state.get_data()
    await message.answer(t(lang, "almost"), reply_markup=kb.main_menu(lang))
    await message.answer(summary(lang, data) + "\n\n" + t(lang, "confirm_q"), reply_markup=kb.confirm_kb(lang))


@dp.callback_query(Booking.confirm, F.data == "confirm")
async def confirm_booking(callback: CallbackQuery, state: FSMContext):
    lang = lang_of(callback.from_user.id)
    data = await state.get_data()
    await state.clear()
    booking_id = None
    # Повторная проверка: пока клиент вводил имя, время могли занять или оно уже прошло
    if data["time"] in free_times(data["master"], data["date"]):
        booking_id = db.add_booking(
            callback.from_user.id, data["name"], data["phone"], data["service"], data["master"],
            data["date"], data["time"], reminder_time(data["date"], data["time"]),
        )
    if booking_id is None:
        await callback.message.edit_text(t(lang, "slot_lost", btn_book=t(lang, "btn_book")))
        await callback.answer()
        return
    await callback.message.edit_text(t(lang, "booked", summary=summary(lang, data), btn_my=t(lang, "btn_my")))
    await callback.answer()
    await notify_admin(callback.bot, f"🆕 Новая запись #{booking_id}\n\n{summary(ADMIN_LANG, data)}")


# ---------- Если пользователь сделал что-то неожиданное ----------

@dp.message(Booking.name)
async def name_not_text(message: Message):
    await message.answer(t(lang_of(message.from_user.id), "name_text"))


@dp.message(Booking.phone)
async def phone_not_text(message: Message):
    lang = lang_of(message.from_user.id)
    await message.answer(t(lang, "phone_text", btn=t(lang, "btn_send_phone")))


@dp.message(StateFilter(Booking.service, Booking.master, Booking.date, Booking.time, Booking.confirm))
async def use_buttons(message: Message):
    await message.answer(t(lang_of(message.from_user.id), "use_buttons"))


@dp.message()
async def unknown_message(message: Message):
    lang = db.get_lang(message.from_user.id)
    if lang is None:
        await message.answer(t("ru", "choose_lang"), reply_markup=kb.lang_kb())
        return
    await message.answer(t(lang, "unknown"), reply_markup=kb.main_menu(lang))


@dp.callback_query()
async def outdated_button(callback: CallbackQuery):
    # Нажатие на кнопку из старого сообщения, когда запись уже завершена или начата заново
    lang = lang_of(callback.from_user.id)
    await callback.answer(t(lang, "outdated", btn_book=t(lang, "btn_book")), show_alert=True)


# ---------- Напоминания ----------

async def send_due_reminders(bot: Bot) -> None:
    now = datetime.now().isoformat(sep=" ", timespec="seconds")
    for row in db.due_reminders(now):
        lang = lang_of(row["user_id"])
        visit_day = date.fromisoformat(row["date"])
        if visit_day == date.today():
            when = t(lang, "today")
        elif visit_day == date.today() + timedelta(days=1):
            when = t(lang, "tomorrow")
        else:
            when = format_date(row["date"], lang)
        try:
            await bot.send_message(
                row["user_id"],
                t(
                    lang, "reminder", business=BUSINESS_NAME, when=when, time=row["time"],
                    service=escape(service_label(row["service"], lang)),
                    master=escape(master_label(row["master"], lang)),
                ),
                reply_markup=kb.reminder_kb(lang, row["id"]),
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
    db.init_db(default_master=next(iter(MASTERS)))
    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    await bot.set_my_commands([
        BotCommand(command="start", description="Бастау / Главное меню"),
        BotCommand(command="book", description="Жазылу / Записаться"),
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
