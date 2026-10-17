"""Хранение записей в SQLite. База — это обычный файл bookings.db рядом с ботом."""
import sqlite3
from contextlib import contextmanager
from datetime import date
from pathlib import Path

DB_PATH = Path(__file__).with_name("bookings.db")

# UNIQUE (master, date, time) не даёт двум клиентам записаться к одному мастеру на одно и то же время
BOOKINGS_SCHEMA = """
    CREATE TABLE bookings (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id    INTEGER NOT NULL,
        name       TEXT NOT NULL,
        phone      TEXT NOT NULL,
        service    TEXT NOT NULL,
        master     TEXT NOT NULL,
        date       TEXT NOT NULL,
        time       TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        remind_at  TEXT,
        reminded   INTEGER NOT NULL DEFAULT 0,
        UNIQUE (master, date, time)
    )
"""


@contextmanager
def connect():
    """Открывает соединение с базой, сохраняет изменения и закрывает его."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row  # строки можно читать по имени колонки: row["name"]
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(default_master: str) -> None:
    """Создаёт таблицы, а базу от старой версии бота переводит на новый формат.

    default_master — к какому мастеру отнести записи, сделанные до появления выбора мастера.
    """
    with connect() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, lang TEXT NOT NULL)")
        exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'bookings'").fetchone()
        if not exists:
            conn.execute(BOOKINGS_SCHEMA)
            return
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(bookings)")}
        if "remind_at" not in columns:
            conn.execute("ALTER TABLE bookings ADD COLUMN remind_at TEXT")
        if "reminded" not in columns:
            conn.execute("ALTER TABLE bookings ADD COLUMN reminded INTEGER NOT NULL DEFAULT 0")
        if "master" not in columns:
            # Ограничение UNIQUE в SQLite не изменить, поэтому пересоздаём таблицу и переносим записи
            conn.execute("ALTER TABLE bookings RENAME TO bookings_old")
            conn.execute(BOOKINGS_SCHEMA)
            conn.execute(
                "INSERT INTO bookings (id, user_id, name, phone, service, master, date, time, created_at, remind_at, reminded)"
                " SELECT id, user_id, name, phone, service, ?, date, time, created_at, remind_at, reminded FROM bookings_old",
                (default_master,),
            )
            conn.execute("DROP TABLE bookings_old")


# ---------- Язык пользователя ----------

def get_lang(user_id: int) -> str | None:
    with connect() as conn:
        row = conn.execute("SELECT lang FROM users WHERE user_id = ?", (user_id,)).fetchone()
    return row["lang"] if row else None


def set_lang(user_id: int, lang: str) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO users (user_id, lang) VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET lang = excluded.lang",
            (user_id, lang),
        )


# ---------- Записи ----------

def taken_times(master: str, day: str) -> set[str]:
    """Занятое время мастера на указанный день (день в формате 2026-10-17)."""
    with connect() as conn:
        rows = conn.execute("SELECT time FROM bookings WHERE master = ? AND date = ?", (master, day)).fetchall()
    return {row["time"] for row in rows}


def add_booking(
    user_id: int, name: str, phone: str, service: str, master: str, day: str, time: str, remind_at: str | None
) -> int | None:
    """Создаёт запись и возвращает её номер. None — если это время у мастера уже кто-то занял.

    remind_at — когда отправить напоминание (2026-10-17 10:00:00), None — не напоминать.
    """
    try:
        with connect() as conn:
            cursor = conn.execute(
                "INSERT INTO bookings (user_id, name, phone, service, master, date, time, remind_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (user_id, name, phone, service, master, day, time, remind_at),
            )
            return cursor.lastrowid
    except sqlite3.IntegrityError:
        return None


def user_bookings(user_id: int) -> list[sqlite3.Row]:
    """Предстоящие записи одного клиента."""
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM bookings WHERE user_id = ? AND date >= ? ORDER BY date, time",
            (user_id, date.today().isoformat()),
        ).fetchall()


def upcoming_bookings() -> list[sqlite3.Row]:
    """Все предстоящие записи — для администратора."""
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM bookings WHERE date >= ? ORDER BY date, time, master",
            (date.today().isoformat(),),
        ).fetchall()


def due_reminders(now: str) -> list[sqlite3.Row]:
    """Записи, которым пора отправить напоминание и которым оно ещё не отправлено."""
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM bookings WHERE reminded = 0 AND remind_at IS NOT NULL AND remind_at <= ?",
            (now,),
        ).fetchall()


def mark_reminded(booking_id: int) -> None:
    with connect() as conn:
        conn.execute("UPDATE bookings SET reminded = 1 WHERE id = ?", (booking_id,))


def cancel_booking(booking_id: int, user_id: int) -> sqlite3.Row | None:
    """Удаляет запись клиента. Возвращает удалённую запись или None, если её нет."""
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM bookings WHERE id = ? AND user_id = ?", (booking_id, user_id)
        ).fetchone()
        if row is not None:
            conn.execute("DELETE FROM bookings WHERE id = ?", (booking_id,))
        return row
