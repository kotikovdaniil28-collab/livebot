"""MySQL/MariaDB вместо SQLite — постоянная база, данные не пропадают.

Включается, если в .env задан MYSQL_URL (mysql://user:pass@host:3306/dbname).
Интерфейс тот же, что у sqlite3 (execute/executescript/fetchone/fetchall),
SQLite-диалект запросов транслируется в MySQL на лету — остальной код бота
не меняется.
"""

import re
from urllib.parse import unquote, urlparse

import pymysql
from pymysql.cursors import DictCursor

from config import MYSQL_URL


def _conn_params() -> dict:
    # Терпимость к опечаткам в .env: берём часть строки от "mysql://",
    # даже если в значение случайно попало что-то вроде "DATABASE_URL=mysql://..."
    raw = MYSQL_URL.strip().strip('"').strip("'")
    m = re.search(r"mysql://\S+", raw)
    if m:
        raw = m.group(0)
    u = urlparse(raw)
    if not u.hostname:
        raise ValueError(
            f"MYSQL_URL не распознан: {raw!r}. "
            "Нужен формат: mysql://user:password@host:3306/dbname"
        )
    return dict(
        host=u.hostname or "localhost",
        port=u.port or 3306,
        user=unquote(u.username or ""),
        password=unquote(u.password or ""),
        database=u.path.lstrip("/"),
        charset="utf8mb4",
        autocommit=False,
        cursorclass=DictCursor,
    )


# --- трансляция SQLite-диалекта в MySQL --------------------------------------

_INSERT_IGNORE = re.compile(r"INSERT OR IGNORE", re.I)
_ON_CONFLICT = re.compile(r"ON CONFLICT\s*\([^)]*\)\s*DO UPDATE SET", re.I)
_EXCLUDED = re.compile(r"excluded\.(\w+)", re.I)


def translate(sql: str) -> str:
    """Запросы: плейсхолдеры и upsert-синтаксис."""
    sql = sql.replace("?", "%s")
    sql = _INSERT_IGNORE.sub("INSERT IGNORE", sql)
    sql = _ON_CONFLICT.sub("ON DUPLICATE KEY UPDATE", sql)
    sql = _EXCLUDED.sub(r"VALUES(\1)", sql)
    # `key` — зарезервированное слово MySQL (колонка в notion_meta)
    if "notion_meta" in sql:
        sql = re.sub(r"(?<![\w`])key(?![\w`])", "`key`", sql)
    return sql


_KEY_COLS = re.compile(r"(?:UNIQUE|PRIMARY KEY)\s*\(([^)]+)\)", re.I)


def translate_ddl(sql: str) -> str:
    """CREATE TABLE: типы и индексы под MySQL."""
    sql = re.sub(r"\bAUTOINCREMENT\b", "AUTO_INCREMENT", sql, flags=re.I)
    # chat_id Телеграма не влезает в 32-битный INTEGER MySQL
    sql = re.sub(r"\bINTEGER\b", "BIGINT", sql, flags=re.I)
    # TEXT-колонка не может быть ключом/индексом без длины -> VARCHAR
    # (имя может быть в \`обратных кавычках\`, напр. \`key\` в notion_meta)
    sql = re.sub(
        r"(`?\w+`?)\s+TEXT\s+PRIMARY KEY", r"\1 VARCHAR(191) PRIMARY KEY", sql, flags=re.I
    )
    key_cols: set[str] = set()
    for m in _KEY_COLS.finditer(sql):
        key_cols |= {c.strip() for c in m.group(1).split(",")}
    for col in key_cols:
        sql = re.sub(rf"\b({col})\s+TEXT\b", r"\1 VARCHAR(191)", sql, flags=re.I)
    # MySQL 5.7 не разрешает DEFAULT у TEXT -> VARCHAR
    sql = re.sub(r"\bTEXT(\s+DEFAULT\b)", r"VARCHAR(1000)\1", sql, flags=re.I)
    return sql


# --- обёртка соединения с интерфейсом sqlite3 ---------------------------------

class Connection:
    def __init__(self) -> None:
        self._conn = pymysql.connect(**_conn_params())

    def execute(self, sql: str, params=()):  # noqa: ANN001
        cur = self._conn.cursor()
        cur.execute(translate(sql), tuple(params))
        return cur

    def executescript(self, script: str) -> None:
        for stmt in script.split(";"):
            stmt = stmt.strip()
            if stmt:
                cur = self._conn.cursor()
                cur.execute(translate_ddl(translate(stmt)))

    def __enter__(self) -> "Connection":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        try:
            if exc_type is None:
                self._conn.commit()
            else:
                self._conn.rollback()
        finally:
            self._conn.close()


def connect() -> Connection:
    return Connection()
