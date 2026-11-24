"""A private MySQL (InnoDB) server and the MySQL versions of the benchmark's references."""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

import tasks

LEVELS = {
    "read committed": "READ COMMITTED",
    "repeatable read": "REPEATABLE READ",
    "serializable": "SERIALIZABLE",
}

INLINE_REFERENCE = re.compile(r"(\w+) (BIGINT|VARCHAR\(64\)) NOT NULL REFERENCES (\w+)\((\w+)\)")


def mysql_ddl(statement: str) -> str:
    sql = statement.replace("BIGSERIAL PRIMARY KEY", "BIGINT AUTO_INCREMENT PRIMARY KEY")
    sql = sql.replace("code TEXT", "code VARCHAR(64)")
    keys = [
        f"FOREIGN KEY ({c}) REFERENCES {t}({r})" for c, _, t, r in INLINE_REFERENCE.findall(sql)
    ]
    sql = INLINE_REFERENCE.sub(r"\1 \2 NOT NULL", sql)
    if keys:
        sql = sql[:-1] + ", " + ", ".join(keys) + ")"
    return sql + " ENGINE=InnoDB"


DDL = {name: [mysql_ddl(s) for s in task["ddl"]] for name, task in tasks.TASKS.items()}

REFERENCES: dict[tuple[str, str, str], str] = {
    ("username_signup", "sqlalchemy", "correct"): """
from sqlalchemy import select, text
from app_models import Member


def register(Session, username, email):
    with Session() as session:
        # No row exists yet to lock, so lock the username's key instead.
        key = "members:" + username
        session.execute(text("SELECT GET_LOCK(:key, 30)"), {"key": key})
        try:
            if session.scalar(select(Member.id).where(Member.username == username)):
                return {"ok": False, "error": "username_taken"}
            member = Member(username=username, email=email)
            session.add(member)
            session.flush()
            member_id = member.id
            session.commit()
        finally:
            session.execute(text("SELECT RELEASE_LOCK(:key)"), {"key": key})
            session.commit()
        return {"ok": True, "member_id": member_id}
""",
    ("username_signup", "django", "correct"): """
from django.db import connection, transaction
from shop.models import Member


def register(username, email):
    # No row exists yet to lock, so lock the username's key instead.
    key = "members:" + username
    with connection.cursor() as cursor:
        cursor.execute("SELECT GET_LOCK(%s, 30)", [key])
    try:
        with transaction.atomic():
            if Member.objects.filter(username=username).exists():
                return {"ok": False, "error": "username_taken"}
            member = Member.objects.create(username=username, email=email)
    finally:
        with connection.cursor() as cursor:
            cursor.execute("SELECT RELEASE_LOCK(%s)", [key])
    return {"ok": True, "member_id": member.id}
""",
}


def references() -> dict[tuple[str, str, str], str]:
    return {**tasks.REFERENCES, **REFERENCES}


class MySQL:

    port = 0

    def __init__(
        self, binaries: Path, root: Path, sockets: Path, settings: dict[str, str], log: Path
    ) -> None:
        self.binaries = binaries
        self.messages = binaries.parent / "share" / "mysql"
        self.root = Path(tempfile.mkdtemp(prefix="c1my-", dir=root))
        self.socket_dir = Path(tempfile.mkdtemp(prefix="c1s-", dir=sockets))
        self.sockets = self.socket_dir / "mysqld.sock"
        self.settings = settings
        self.log = log
        self.admin: Any = None
        self.process: subprocess.Popen[bytes] | None = None

    def __enter__(self) -> MySQL:
        data = self.root / "data"
        subprocess.run(
            [
                str(self.binaries / "mysqld"),
                "--no-defaults",
                "--initialize-insecure",
                f"--lc-messages-dir={self.messages}",
                f"--datadir={data}",
                f"--log-error={self.log}",
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        options = {
            **self.settings,
            "datadir": str(data),
            "lc-messages-dir": str(self.messages),
            "socket": str(self.sockets),
            "pid-file": str(self.root / "mysqld.pid"),
            "log-error": str(self.log),
            "skip-networking": "ON",
            "mysqlx": "OFF",
            "tmpdir": str(self.root),
        }
        self.process = subprocess.Popen(
            [str(self.binaries / "mysqld"), "--no-defaults"]
            + [f"--{key}={value}" for key, value in sorted(options.items())],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + 120
        while True:
            try:
                with self.connect(None) as admin:
                    admin.cursor().execute("CREATE DATABASE app")
                break
            except Exception:
                if self.process.poll() is not None or time.monotonic() > deadline:
                    self.__exit__()
                    raise RuntimeError(f"mysqld did not start; see {self.log}") from None
                time.sleep(0.5)
        self.admin = self.connect()
        return self

    def __exit__(self, *_: Any) -> None:
        if self.admin is not None:
            self.admin.close()
        if self.process is not None:
            self.process.kill()
            self.process.wait(timeout=30)
        shutil.rmtree(self.root, ignore_errors=True)
        shutil.rmtree(self.socket_dir, ignore_errors=True)

    def connect(self, name: str | None = "app") -> Any:
        import MySQLdb

        options: dict[str, Any] = {"unix_socket": str(self.sockets), "user": "root"}
        if name:
            options["database"] = name
        connection = MySQLdb.connect(**options)
        connection.autocommit(True)
        return connection

    def execute(self, sql: str) -> Any:
        cursor = self.admin.cursor()
        cursor.execute(sql)
        return cursor

    def set_isolation(self, level: str) -> None:
        self.execute(f"SET GLOBAL TRANSACTION ISOLATION LEVEL {LEVELS[level]}")

    def reset(self, task: str, seed: list[str]) -> None:
        own = self.execute("SELECT CONNECTION_ID()").fetchone()[0]
        for row in self.execute("SHOW FULL PROCESSLIST").fetchall():
            if row[3] == "app" and row[0] != own:
                try:
                    self.execute(f"KILL {int(row[0])}")
                except Exception:
                    pass
        self.execute("SET FOREIGN_KEY_CHECKS = 0")
        for table in tasks.TASKS[task]["tables"]:
            self.execute(f"DROP TABLE IF EXISTS {table}")
        self.execute("SET FOREIGN_KEY_CHECKS = 1")
        for statement in [*DDL[task], *seed]:
            self.execute(statement)

    def value(self, sql: str) -> Any:
        row = self.execute(sql).fetchone()
        return row[0] if row else None
