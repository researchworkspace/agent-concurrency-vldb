"""Benchmark tasks: schemas, prompts, functional tests, request plans, invariants and reference programs."""

from __future__ import annotations

import re
from typing import Any

FRAMEWORKS = ("sqlalchemy", "django")
CONDITIONS = ("plain", "hint")
CONTROL_CLASS = "lost_update"
TEST_CLASSES = ("check_then_insert", "write_skew", "predicate", "multi_row")


SQLALCHEMY_HEADER = """from sqlalchemy import BigInteger, Boolean, ForeignKey, Integer, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass
"""

DJANGO_HEADER = """from django.db import models
"""

FRAMEWORK_TEXT = {
    "sqlalchemy": (
        "The application uses SQLAlchemy 2.0 (ORM) with PostgreSQL through psycopg 3. The "
        "models already exist in the module `app_models`:\n\n```python\n{models}```\n\n"
        "Import them with `from app_models import {names}`. The function receives "
        "`Session`, a `sqlalchemy.orm.sessionmaker` bound to the "
        "application's engine with default settings; open sessions from it. The web layer "
        "calls the function once per HTTP request."
    ),
    "django": (
        "The application uses Django 5.2 with PostgreSQL through psycopg 3, with the default "
        "database settings (autocommit on, ATOMIC_REQUESTS off). The models already exist in "
        "the module `shop.models`:\n\n```python\n{models}```\n\n"
        "Import them with `from shop.models import {names}`. The view calls the function "
        "once per HTTP request."
    ),
}

PROMPT = """You are implementing a service function in an existing web application backed by PostgreSQL.

{framework}

Task: {spec}

Write a Python module that defines exactly this function:

```python
{signature}
```

Return {{"ok": True, ...}} when the request succeeds and {{"ok": False, "error": "<reason>"}} when it is rejected. Do not change the database schema or the models.{hint}

Respond with the complete module in a single ```python code block."""

HINT = (
    " The endpoint receives many concurrent requests in production; make sure it stays "
    "correct under concurrent execution."
)


TASKS: dict[str, dict[str, Any]] = {
    "stock_purchase": {
        "hazard": "lost_update",
        "function": "purchase",
        "arguments": ["product_id", "quantity"],
        "spec": (
            "Implement `purchase(product_id, quantity)`. If the product's stock is at least "
            "`quantity`, create an order for that quantity, reduce the product's stock by "
            '`quantity`, and return {"ok": True, "order_id": <new order id>}. Otherwise '
            'change nothing and return {"ok": False, "error": "insufficient_stock"}.'
        ),
        "ddl": [
            "CREATE TABLE products (id BIGSERIAL PRIMARY KEY, name TEXT NOT NULL, "
            "stock INTEGER NOT NULL)",
            "CREATE TABLE orders (id BIGSERIAL PRIMARY KEY, product_id BIGINT NOT NULL "
            "REFERENCES products(id), quantity INTEGER NOT NULL)",
        ],
        "tables": ["orders", "products"],
        "sqlalchemy_models": """

class Product(Base):
    __tablename__ = "products"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    stock: Mapped[int] = mapped_column(Integer)


class Order(Base):
    __tablename__ = "orders"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    product_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("products.id"))
    quantity: Mapped[int] = mapped_column(Integer)
""",
        "django_models": """

class Product(models.Model):
    id = models.BigAutoField(primary_key=True)
    name = models.TextField()
    stock = models.IntegerField()

    class Meta:
        db_table = "products"
        managed = False


class Order(models.Model):
    id = models.BigAutoField(primary_key=True)
    product = models.ForeignKey(Product, on_delete=models.PROTECT, db_column="product_id")
    quantity = models.IntegerField()

    class Meta:
        db_table = "orders"
        managed = False
""",
        "functional": {
            "seed": ["INSERT INTO products (id, name, stock) VALUES (1, 'lamp', 2)"],
            "calls": [
                [{"product_id": 1, "quantity": 1}, True],
                [{"product_id": 1, "quantity": 2}, False],
                [{"product_id": 1, "quantity": 1}, True],
                [{"product_id": 1, "quantity": 1}, False],
            ],
            "state": [
                ["SELECT stock FROM products WHERE id = 1", 0],
                ["SELECT COUNT(*) FROM orders", 2],
                ["SELECT COALESCE(SUM(quantity), 0) FROM orders", 2],
            ],
        },
        "concurrent": {
            "seed": ["INSERT INTO products (id, name, stock) VALUES (1, 'lamp', 10)"],
            "requests_per_worker": 3,
            "optimal_successes": 10,
        },
    },
    "coupon_redeem": {
        "hazard": "check_then_insert",
        "function": "redeem",
        "arguments": ["code", "user_id"],
        "spec": (
            "Implement `redeem(code, user_id)`. A user may redeem a given coupon at most "
            "once, and a coupon may be redeemed at most `max_uses` times in total. If the "
            'user has already redeemed the coupon, return {"ok": False, "error": '
            '"already_redeemed"}. If the coupon has reached `max_uses` redemptions, return '
            '{"ok": False, "error": "exhausted"}. Otherwise record the redemption and '
            'return {"ok": True, "redemption_id": <new redemption id>}.'
        ),
        "ddl": [
            "CREATE TABLE coupons (code TEXT PRIMARY KEY, max_uses INTEGER NOT NULL)",
            "CREATE TABLE redemptions (id BIGSERIAL PRIMARY KEY, code TEXT NOT NULL "
            "REFERENCES coupons(code), user_id INTEGER NOT NULL)",
        ],
        "tables": ["redemptions", "coupons"],
        "sqlalchemy_models": """

class Coupon(Base):
    __tablename__ = "coupons"
    code: Mapped[str] = mapped_column(Text, primary_key=True)
    max_uses: Mapped[int] = mapped_column(Integer)


class Redemption(Base):
    __tablename__ = "redemptions"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    code: Mapped[str] = mapped_column(Text, ForeignKey("coupons.code"))
    user_id: Mapped[int] = mapped_column(Integer)
""",
        "django_models": """

class Coupon(models.Model):
    code = models.TextField(primary_key=True)
    max_uses = models.IntegerField()

    class Meta:
        db_table = "coupons"
        managed = False


class Redemption(models.Model):
    id = models.BigAutoField(primary_key=True)
    coupon = models.ForeignKey(Coupon, on_delete=models.PROTECT, db_column="code")
    user_id = models.IntegerField()

    class Meta:
        db_table = "redemptions"
        managed = False
""",
        "functional": {
            "seed": ["INSERT INTO coupons (code, max_uses) VALUES ('SAVE10', 2)"],
            "calls": [
                [{"code": "SAVE10", "user_id": 1}, True],
                [{"code": "SAVE10", "user_id": 1}, False],
                [{"code": "SAVE10", "user_id": 2}, True],
                [{"code": "SAVE10", "user_id": 3}, False],
            ],
            "state": [["SELECT COUNT(*) FROM redemptions", 2]],
        },
        "concurrent": {
            "seed": ["INSERT INTO coupons (code, max_uses) VALUES ('SAVE10', 5)"],
            "requests_per_worker": 3,
            "optimal_successes": 5,
        },
    },
    "oncall_leave": {
        "hazard": "write_skew",
        "function": "leave_shift",
        "arguments": ["doctor_id"],
        "spec": (
            "Implement `leave_shift(doctor_id)`. At least one doctor must remain on call at "
            'all times. If the doctor is not on call, return {"ok": False, "error": '
            '"not_on_call"}. If no other doctor is on call, return {"ok": False, '
            '"error": "last_on_call"}. Otherwise take the doctor off call and return '
            '{"ok": True}.'
        ),
        "ddl": [
            "CREATE TABLE doctors (id BIGSERIAL PRIMARY KEY, name TEXT NOT NULL, "
            "on_call BOOLEAN NOT NULL)",
        ],
        "tables": ["doctors"],
        "sqlalchemy_models": """

class Doctor(Base):
    __tablename__ = "doctors"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    on_call: Mapped[bool] = mapped_column(Boolean)
""",
        "django_models": """

class Doctor(models.Model):
    id = models.BigAutoField(primary_key=True)
    name = models.TextField()
    on_call = models.BooleanField()

    class Meta:
        db_table = "doctors"
        managed = False
""",
        "functional": {
            "seed": [
                "INSERT INTO doctors (id, name, on_call) VALUES (1, 'Ada', true), (2, 'Ben', true)"
            ],
            "calls": [
                [{"doctor_id": 1}, True],
                [{"doctor_id": 2}, False],
                [{"doctor_id": 1}, False],
            ],
            "state": [["SELECT COUNT(*) FROM doctors WHERE on_call", 1]],
        },
        "concurrent": {
            "seed": [
                "INSERT INTO doctors (id, name, on_call) VALUES (1, 'Ada', true), "
                "(2, 'Ben', true), (3, 'Cy', true), (4, 'Dee', true)"
            ],
            "requests_per_worker": 1,
            "optimal_successes": 3,
        },
    },
    "username_signup": {
        "hazard": "check_then_insert",
        "function": "register",
        "arguments": ["username", "email"],
        "spec": (
            "Implement `register(username, email)`. Usernames must be unique. If a member "
            'with this username already exists, return {"ok": False, "error": '
            '"username_taken"}. Otherwise create the member and return {"ok": True, '
            '"member_id": <new member id>}.'
        ),
        "ddl": [
            "CREATE TABLE members (id BIGSERIAL PRIMARY KEY, username TEXT NOT NULL, "
            "email TEXT NOT NULL)",
        ],
        "tables": ["members"],
        "sqlalchemy_models": """

class Member(Base):
    __tablename__ = "members"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str] = mapped_column(Text)
    email: Mapped[str] = mapped_column(Text)
""",
        "django_models": """

class Member(models.Model):
    id = models.BigAutoField(primary_key=True)
    username = models.TextField()
    email = models.TextField()

    class Meta:
        db_table = "members"
        managed = False
""",
        "functional": {
            "seed": [],
            "calls": [
                [{"username": "ada", "email": "ada@example.org"}, True],
                [{"username": "ada", "email": "other@example.org"}, False],
                [{"username": "ben", "email": "ben@example.org"}, True],
            ],
            "state": [["SELECT COUNT(*) FROM members", 2]],
        },
        "concurrent": {"seed": [], "requests_per_worker": 2, "optimal_successes": 4},
    },
    "joint_withdraw": {
        "hazard": "write_skew",
        "function": "withdraw",
        "arguments": ["account_id", "amount"],
        "spec": (
            "Implement `withdraw(account_id, amount)`. A customer may hold several accounts. "
            "One account may go negative, but the total balance of all the customer's "
            "accounts must never go below zero. If the account does not exist, return "
            '{"ok": False, "error": "unknown_account"}. If the withdrawal would make the '
            'customer\'s total balance negative, return {"ok": False, "error": '
            '"insufficient_funds"}. Otherwise reduce the account\'s balance by `amount`, '
            'record the withdrawal, and return {"ok": True, "withdrawal_id": <new '
            "withdrawal id>}."
        ),
        "ddl": [
            "CREATE TABLE bank_accounts (id BIGSERIAL PRIMARY KEY, customer_id INTEGER NOT NULL, "
            "balance INTEGER NOT NULL)",
            "CREATE TABLE withdrawals (id BIGSERIAL PRIMARY KEY, account_id BIGINT NOT NULL "
            "REFERENCES bank_accounts(id), amount INTEGER NOT NULL)",
        ],
        "tables": ["withdrawals", "bank_accounts"],
        "sqlalchemy_models": """

class Account(Base):
    __tablename__ = "bank_accounts"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    customer_id: Mapped[int] = mapped_column(Integer)
    balance: Mapped[int] = mapped_column(Integer)


class Withdrawal(Base):
    __tablename__ = "withdrawals"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    account_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("bank_accounts.id"))
    amount: Mapped[int] = mapped_column(Integer)
""",
        "django_models": """

class Account(models.Model):
    id = models.BigAutoField(primary_key=True)
    customer_id = models.IntegerField()
    balance = models.IntegerField()

    class Meta:
        db_table = "bank_accounts"
        managed = False


class Withdrawal(models.Model):
    id = models.BigAutoField(primary_key=True)
    account = models.ForeignKey(Account, on_delete=models.PROTECT, db_column="account_id")
    amount = models.IntegerField()

    class Meta:
        db_table = "withdrawals"
        managed = False
""",
        "functional": {
            "seed": [
                "INSERT INTO bank_accounts (id, customer_id, balance) VALUES (1, 1, 30), "
                "(2, 1, 30), (3, 2, 100)"
            ],
            "calls": [
                [{"account_id": 1, "amount": 50}, True],
                [{"account_id": 2, "amount": 20}, False],
                [{"account_id": 2, "amount": 10}, True],
                [{"account_id": 3, "amount": 100}, True],
            ],
            "state": [
                ["SELECT SUM(balance) FROM bank_accounts WHERE customer_id = 1", 0],
                ["SELECT balance FROM bank_accounts WHERE id = 1", -20],
                ["SELECT COUNT(*) FROM withdrawals", 3],
            ],
        },
        "concurrent": {
            "seed": [
                "INSERT INTO bank_accounts (id, customer_id, balance) VALUES (1, 1, 50), (2, 1, 50)"
            ],
            "requests_per_worker": 1,
            "optimal_successes": 3,
        },
    },
    "seat_reserve": {
        "hazard": "predicate",
        "function": "reserve",
        "arguments": ["event_id", "seats"],
        "spec": (
            "Implement `reserve(event_id, seats)`. The reservations of an event may hold at "
            "most `capacity` seats in total. If the event does not exist, return "
            '{"ok": False, "error": "unknown_event"}. If the reservation would exceed the '
            'capacity, return {"ok": False, "error": "sold_out"}. Otherwise record a '
            'reservation for `seats` seats and return {"ok": True, "reservation_id": <new '
            "reservation id>}."
        ),
        "ddl": [
            "CREATE TABLE events (id BIGSERIAL PRIMARY KEY, name TEXT NOT NULL, "
            "capacity INTEGER NOT NULL)",
            "CREATE TABLE reservations (id BIGSERIAL PRIMARY KEY, event_id BIGINT NOT NULL "
            "REFERENCES events(id), seats INTEGER NOT NULL)",
        ],
        "tables": ["reservations", "events"],
        "sqlalchemy_models": """

class Event(Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    capacity: Mapped[int] = mapped_column(Integer)


class Reservation(Base):
    __tablename__ = "reservations"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    event_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("events.id"))
    seats: Mapped[int] = mapped_column(Integer)
""",
        "django_models": """

class Event(models.Model):
    id = models.BigAutoField(primary_key=True)
    name = models.TextField()
    capacity = models.IntegerField()

    class Meta:
        db_table = "events"
        managed = False


class Reservation(models.Model):
    id = models.BigAutoField(primary_key=True)
    event = models.ForeignKey(Event, on_delete=models.PROTECT, db_column="event_id")
    seats = models.IntegerField()

    class Meta:
        db_table = "reservations"
        managed = False
""",
        "functional": {
            "seed": ["INSERT INTO events (id, name, capacity) VALUES (1, 'gala', 5)"],
            "calls": [
                [{"event_id": 1, "seats": 3}, True],
                [{"event_id": 1, "seats": 3}, False],
                [{"event_id": 1, "seats": 2}, True],
                [{"event_id": 1, "seats": 1}, False],
            ],
            "state": [
                ["SELECT COALESCE(SUM(seats), 0) FROM reservations", 5],
                ["SELECT COUNT(*) FROM reservations", 2],
            ],
        },
        "concurrent": {
            "seed": ["INSERT INTO events (id, name, capacity) VALUES (1, 'gala', 9)"],
            "requests_per_worker": 2,
            "optimal_successes": 4,
        },
    },
    "room_book": {
        "hazard": "predicate",
        "function": "book",
        "arguments": ["room_id", "start_hour", "end_hour"],
        "spec": (
            "Implement `book(room_id, start_hour, end_hour)`. A booking occupies the "
            "half-open interval [start_hour, end_hour), and bookings of the same room must "
            'not overlap. If the room does not exist, return {"ok": False, "error": '
            '"unknown_room"}. If the interval overlaps an existing booking of the room, '
            'return {"ok": False, "error": "conflict"}. Otherwise record the booking and '
            'return {"ok": True, "booking_id": <new booking id>}.'
        ),
        "ddl": [
            "CREATE TABLE rooms (id BIGSERIAL PRIMARY KEY, name TEXT NOT NULL)",
            "CREATE TABLE bookings (id BIGSERIAL PRIMARY KEY, room_id BIGINT NOT NULL "
            "REFERENCES rooms(id), start_hour INTEGER NOT NULL, end_hour INTEGER NOT NULL)",
        ],
        "tables": ["bookings", "rooms"],
        "sqlalchemy_models": """

class Room(Base):
    __tablename__ = "rooms"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(Text)


class Booking(Base):
    __tablename__ = "bookings"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    room_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("rooms.id"))
    start_hour: Mapped[int] = mapped_column(Integer)
    end_hour: Mapped[int] = mapped_column(Integer)
""",
        "django_models": """

class Room(models.Model):
    id = models.BigAutoField(primary_key=True)
    name = models.TextField()

    class Meta:
        db_table = "rooms"
        managed = False


class Booking(models.Model):
    id = models.BigAutoField(primary_key=True)
    room = models.ForeignKey(Room, on_delete=models.PROTECT, db_column="room_id")
    start_hour = models.IntegerField()
    end_hour = models.IntegerField()

    class Meta:
        db_table = "bookings"
        managed = False
""",
        "functional": {
            "seed": ["INSERT INTO rooms (id, name) VALUES (1, 'A'), (2, 'B')"],
            "calls": [
                [{"room_id": 1, "start_hour": 9, "end_hour": 11}, True],
                [{"room_id": 1, "start_hour": 10, "end_hour": 12}, False],
                [{"room_id": 1, "start_hour": 11, "end_hour": 12}, True],
                [{"room_id": 2, "start_hour": 9, "end_hour": 11}, True],
                [{"room_id": 1, "start_hour": 8, "end_hour": 10}, False],
            ],
            "state": [["SELECT COUNT(*) FROM bookings", 3]],
        },
        "concurrent": {
            "seed": ["INSERT INTO rooms (id, name) VALUES (1, 'A')"],
            "requests_per_worker": 1,
            "optimal_successes": 2,
        },
    },
    "wallet_transfer": {
        "hazard": "multi_row",
        "function": "transfer",
        "arguments": ["from_wallet_id", "to_wallet_id", "amount"],
        "spec": (
            "Implement `transfer(from_wallet_id, to_wallet_id, amount)`. Move `amount` from "
            "one wallet to another; no balance may go negative. If either wallet does not "
            'exist, return {"ok": False, "error": "unknown_wallet"}. If the source balance '
            'is below `amount`, return {"ok": False, "error": "insufficient_funds"}. '
            'Otherwise debit the source, credit the destination, and return {"ok": True}.'
        ),
        "ddl": [
            "CREATE TABLE wallets (id BIGSERIAL PRIMARY KEY, owner TEXT NOT NULL, "
            "balance INTEGER NOT NULL)",
        ],
        "tables": ["wallets"],
        "sqlalchemy_models": """

class Wallet(Base):
    __tablename__ = "wallets"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    owner: Mapped[str] = mapped_column(Text)
    balance: Mapped[int] = mapped_column(Integer)
""",
        "django_models": """

class Wallet(models.Model):
    id = models.BigAutoField(primary_key=True)
    owner = models.TextField()
    balance = models.IntegerField()

    class Meta:
        db_table = "wallets"
        managed = False
""",
        "functional": {
            "seed": [
                "INSERT INTO wallets (id, owner, balance) VALUES (1, 'ann', 50), (2, 'bo', 0)"
            ],
            "calls": [
                [{"from_wallet_id": 1, "to_wallet_id": 2, "amount": 30}, True],
                [{"from_wallet_id": 1, "to_wallet_id": 2, "amount": 30}, False],
                [{"from_wallet_id": 2, "to_wallet_id": 1, "amount": 30}, True],
                [{"from_wallet_id": 1, "to_wallet_id": 3, "amount": 10}, False],
            ],
            "state": [
                ["SELECT balance FROM wallets WHERE id = 1", 50],
                ["SELECT balance FROM wallets WHERE id = 2", 0],
            ],
        },
        "concurrent": {
            "seed": [
                "INSERT INTO wallets (id, owner, balance) VALUES (1, 'ann', 100), (2, 'bo', 0), "
                "(3, 'cy', 0)"
            ],
            "requests_per_worker": 2,
            "optimal_successes": 3,
        },
    },
}


def request_plan(task: str, worker: int) -> list[dict[str, Any]]:
    count = TASKS[task]["concurrent"]["requests_per_worker"]
    if task == "stock_purchase":
        return [{"product_id": 1, "quantity": 1} for _ in range(count)]
    if task == "coupon_redeem":
        return [{"code": "SAVE10", "user_id": (worker * count + k) % 6 + 1} for k in range(count)]
    if task == "oncall_leave":
        return [{"doctor_id": worker % 4 + 1}]
    if task == "username_signup":
        names = [f"user{(worker * count + k) % 4}" for k in range(count)]
        return [{"username": n, "email": f"{n}.{worker}@example.org"} for n in names]
    if task == "joint_withdraw":
        return [{"account_id": worker % 2 + 1, "amount": 30}]
    if task == "seat_reserve":
        return [{"event_id": 1, "seats": 2} for _ in range(count)]
    if task == "room_book":
        start, end = [(9, 11), (10, 12), (14, 16), (15, 17)][worker % 4]
        return [{"room_id": 1, "start_hour": start, "end_hour": end}]
    if task == "wallet_transfer":
        target = 2 if worker % 2 == 0 else 3
        return [{"from_wallet_id": 1, "to_wallet_id": target, "amount": 30} for _ in range(count)]
    raise KeyError(task)


def invariant(task: str, query: Any) -> list[str]:
    found: list[str] = []
    if task == "stock_purchase":
        stock = query("SELECT stock FROM products WHERE id = 1")
        sold = query("SELECT COALESCE(SUM(quantity), 0) FROM orders")
        if stock < 0:
            found.append("negative_stock")
        if stock + sold != 10:
            found.append("stock_not_conserved")
    elif task == "coupon_redeem":
        if query("SELECT COUNT(*) FROM redemptions") > 5:
            found.append("over_redeemed")
        duplicates = query(
            "SELECT COUNT(*) FROM (SELECT user_id FROM redemptions GROUP BY user_id "
            "HAVING COUNT(*) > 1) AS d"
        )
        if duplicates:
            found.append("duplicate_redemption")
    elif task == "oncall_leave":
        if query("SELECT COUNT(*) FROM doctors WHERE on_call") < 1:
            found.append("nobody_on_call")
    elif task == "username_signup":
        if query(
            "SELECT COUNT(*) FROM (SELECT username FROM members GROUP BY username "
            "HAVING COUNT(*) > 1) AS d"
        ):
            found.append("duplicate_username")
    elif task == "joint_withdraw":
        total = query("SELECT COALESCE(SUM(balance), 0) FROM bank_accounts")
        withdrawn = query("SELECT COALESCE(SUM(amount), 0) FROM withdrawals")
        if total < 0:
            found.append("overdrawn")
        if total + withdrawn != 100:
            found.append("balance_not_conserved")
    elif task == "seat_reserve":
        if query("SELECT COALESCE(SUM(seats), 0) FROM reservations") > 9:
            found.append("over_capacity")
    elif task == "room_book":
        if query(
            "SELECT COUNT(*) FROM bookings a JOIN bookings b ON a.room_id = b.room_id "
            "AND a.id < b.id AND a.start_hour < b.end_hour AND b.start_hour < a.end_hour"
        ):
            found.append("overlapping_bookings")
    elif task == "wallet_transfer":
        if query("SELECT COUNT(*) FROM wallets WHERE balance < 0"):
            found.append("negative_balance")
        if query("SELECT COALESCE(SUM(balance), 0) FROM wallets") != 100:
            found.append("money_not_conserved")
    else:
        raise KeyError(task)
    return found


def models_module(task: str, framework: str) -> str:
    header = SQLALCHEMY_HEADER if framework == "sqlalchemy" else DJANGO_HEADER
    return header + TASKS[task][f"{framework}_models"]


def model_names(task: str, framework: str) -> list[str]:
    source = TASKS[task][f"{framework}_models"]
    return re.findall(r"^class (\w+)\((?:Base|models\.Model)\):", source, re.M)


def signature(task: str, framework: str) -> str:
    spec = TASKS[task]
    arguments = (["Session"] if framework == "sqlalchemy" else []) + spec["arguments"]
    return f"def {spec['function']}({', '.join(arguments)}) -> dict:"


def prompt(task: str, framework: str, condition: str) -> str:
    if framework not in FRAMEWORKS or condition not in CONDITIONS:
        raise ValueError("unknown framework or condition")
    return PROMPT.format(
        framework=FRAMEWORK_TEXT[framework].format(
            models=models_module(task, framework), names=", ".join(model_names(task, framework))
        ),
        spec=TASKS[task]["spec"],
        signature=signature(task, framework),
        hint=HINT if condition == "hint" else "",
    )


REFERENCES: dict[tuple[str, str, str], str] = {
    ("stock_purchase", "sqlalchemy", "correct"): """
from sqlalchemy import select
from app_models import Order, Product


def purchase(Session, product_id, quantity):
    with Session() as session, session.begin():
        product = session.execute(
            select(Product).where(Product.id == product_id).with_for_update()
        ).scalar_one_or_none()
        if product is None or product.stock < quantity:
            return {"ok": False, "error": "insufficient_stock"}
        product.stock -= quantity
        order = Order(product_id=product_id, quantity=quantity)
        session.add(order)
        session.flush()
        return {"ok": True, "order_id": order.id}
""",
    ("stock_purchase", "sqlalchemy", "buggy"): """
from app_models import Order, Product


def purchase(Session, product_id, quantity):
    with Session() as session:
        product = session.get(Product, product_id)
        if product is None or product.stock < quantity:
            return {"ok": False, "error": "insufficient_stock"}
        product.stock = product.stock - quantity
        order = Order(product_id=product_id, quantity=quantity)
        session.add(order)
        session.commit()
        return {"ok": True, "order_id": order.id}
""",
    ("stock_purchase", "django", "correct"): """
from django.db import transaction
from shop.models import Order, Product


def purchase(product_id, quantity):
    with transaction.atomic():
        product = Product.objects.select_for_update().filter(id=product_id).first()
        if product is None or product.stock < quantity:
            return {"ok": False, "error": "insufficient_stock"}
        product.stock -= quantity
        product.save(update_fields=["stock"])
        order = Order.objects.create(product_id=product_id, quantity=quantity)
    return {"ok": True, "order_id": order.id}
""",
    ("stock_purchase", "django", "buggy"): """
from shop.models import Order, Product


def purchase(product_id, quantity):
    product = Product.objects.filter(id=product_id).first()
    if product is None or product.stock < quantity:
        return {"ok": False, "error": "insufficient_stock"}
    product.stock = product.stock - quantity
    product.save()
    order = Order.objects.create(product=product, quantity=quantity)
    return {"ok": True, "order_id": order.id}
""",
    ("coupon_redeem", "sqlalchemy", "correct"): """
from sqlalchemy import func, select
from app_models import Coupon, Redemption


def redeem(Session, code, user_id):
    with Session() as session, session.begin():
        coupon = session.execute(
            select(Coupon).where(Coupon.code == code).with_for_update()
        ).scalar_one_or_none()
        if coupon is None:
            return {"ok": False, "error": "unknown_coupon"}
        mine = session.scalar(
            select(func.count()).select_from(Redemption).where(
                Redemption.code == code, Redemption.user_id == user_id
            )
        )
        if mine:
            return {"ok": False, "error": "already_redeemed"}
        used = session.scalar(
            select(func.count()).select_from(Redemption).where(Redemption.code == code)
        )
        if used >= coupon.max_uses:
            return {"ok": False, "error": "exhausted"}
        redemption = Redemption(code=code, user_id=user_id)
        session.add(redemption)
        session.flush()
        return {"ok": True, "redemption_id": redemption.id}
""",
    ("coupon_redeem", "sqlalchemy", "buggy"): """
from sqlalchemy import func, select
from app_models import Coupon, Redemption


def redeem(Session, code, user_id):
    with Session() as session:
        coupon = session.get(Coupon, code)
        if coupon is None:
            return {"ok": False, "error": "unknown_coupon"}
        mine = session.scalar(
            select(func.count()).select_from(Redemption).where(
                Redemption.code == code, Redemption.user_id == user_id
            )
        )
        if mine:
            return {"ok": False, "error": "already_redeemed"}
        used = session.scalar(
            select(func.count()).select_from(Redemption).where(Redemption.code == code)
        )
        if used >= coupon.max_uses:
            return {"ok": False, "error": "exhausted"}
        redemption = Redemption(code=code, user_id=user_id)
        session.add(redemption)
        session.commit()
        return {"ok": True, "redemption_id": redemption.id}
""",
    ("coupon_redeem", "django", "correct"): """
from django.db import transaction
from shop.models import Coupon, Redemption


def redeem(code, user_id):
    with transaction.atomic():
        coupon = Coupon.objects.select_for_update().filter(code=code).first()
        if coupon is None:
            return {"ok": False, "error": "unknown_coupon"}
        if Redemption.objects.filter(coupon=coupon, user_id=user_id).exists():
            return {"ok": False, "error": "already_redeemed"}
        if Redemption.objects.filter(coupon=coupon).count() >= coupon.max_uses:
            return {"ok": False, "error": "exhausted"}
        redemption = Redemption.objects.create(coupon=coupon, user_id=user_id)
    return {"ok": True, "redemption_id": redemption.id}
""",
    ("coupon_redeem", "django", "buggy"): """
from shop.models import Coupon, Redemption


def redeem(code, user_id):
    coupon = Coupon.objects.filter(code=code).first()
    if coupon is None:
        return {"ok": False, "error": "unknown_coupon"}
    if Redemption.objects.filter(coupon=coupon, user_id=user_id).exists():
        return {"ok": False, "error": "already_redeemed"}
    if Redemption.objects.filter(coupon=coupon).count() >= coupon.max_uses:
        return {"ok": False, "error": "exhausted"}
    redemption = Redemption.objects.create(coupon=coupon, user_id=user_id)
    return {"ok": True, "redemption_id": redemption.id}
""",
    ("oncall_leave", "sqlalchemy", "correct"): """
from sqlalchemy import select
from app_models import Doctor


def leave_shift(Session, doctor_id):
    with Session() as session, session.begin():
        doctors = session.execute(
            select(Doctor).order_by(Doctor.id).with_for_update()
        ).scalars().all()
        me = next((d for d in doctors if d.id == doctor_id), None)
        if me is None or not me.on_call:
            return {"ok": False, "error": "not_on_call"}
        if not any(d.on_call for d in doctors if d.id != doctor_id):
            return {"ok": False, "error": "last_on_call"}
        me.on_call = False
        return {"ok": True}
""",
    ("oncall_leave", "sqlalchemy", "buggy"): """
from sqlalchemy import func, select
from app_models import Doctor


def leave_shift(Session, doctor_id):
    with Session() as session, session.begin():
        me = session.get(Doctor, doctor_id)
        if me is None or not me.on_call:
            return {"ok": False, "error": "not_on_call"}
        others = session.scalar(
            select(func.count()).select_from(Doctor).where(
                Doctor.on_call.is_(True), Doctor.id != doctor_id
            )
        )
        if others < 1:
            return {"ok": False, "error": "last_on_call"}
        me.on_call = False
        return {"ok": True}
""",
    ("oncall_leave", "django", "correct"): """
from django.db import transaction
from shop.models import Doctor


def leave_shift(doctor_id):
    with transaction.atomic():
        doctors = list(Doctor.objects.select_for_update().order_by("id"))
        me = next((d for d in doctors if d.id == doctor_id), None)
        if me is None or not me.on_call:
            return {"ok": False, "error": "not_on_call"}
        if not any(d.on_call for d in doctors if d.id != doctor_id):
            return {"ok": False, "error": "last_on_call"}
        me.on_call = False
        me.save(update_fields=["on_call"])
    return {"ok": True}
""",
    ("oncall_leave", "django", "buggy"): """
from django.db import transaction
from shop.models import Doctor


def leave_shift(doctor_id):
    with transaction.atomic():
        me = Doctor.objects.filter(id=doctor_id).first()
        if me is None or not me.on_call:
            return {"ok": False, "error": "not_on_call"}
        if Doctor.objects.filter(on_call=True).exclude(id=doctor_id).count() < 1:
            return {"ok": False, "error": "last_on_call"}
        me.on_call = False
        me.save(update_fields=["on_call"])
    return {"ok": True}
""",
    ("username_signup", "sqlalchemy", "correct"): """
from sqlalchemy import func, select
from app_models import Member


def register(Session, username, email):
    with Session() as session, session.begin():
        # No row exists yet to lock, so lock the username's key instead.
        session.execute(select(func.pg_advisory_xact_lock(func.hashtext(username))))
        if session.scalar(select(Member.id).where(Member.username == username)):
            return {"ok": False, "error": "username_taken"}
        member = Member(username=username, email=email)
        session.add(member)
        session.flush()
        return {"ok": True, "member_id": member.id}
""",
    ("username_signup", "sqlalchemy", "buggy"): """
from sqlalchemy import select
from app_models import Member


def register(Session, username, email):
    with Session() as session:
        if session.scalar(select(Member.id).where(Member.username == username)):
            return {"ok": False, "error": "username_taken"}
        member = Member(username=username, email=email)
        session.add(member)
        session.commit()
        return {"ok": True, "member_id": member.id}
""",
    ("username_signup", "django", "correct"): """
from django.db import connection, transaction
from shop.models import Member


def register(username, email):
    with transaction.atomic():
        # No row exists yet to lock, so lock the username's key instead.
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", [username])
        if Member.objects.filter(username=username).exists():
            return {"ok": False, "error": "username_taken"}
        member = Member.objects.create(username=username, email=email)
    return {"ok": True, "member_id": member.id}
""",
    ("username_signup", "django", "buggy"): """
from shop.models import Member


def register(username, email):
    if Member.objects.filter(username=username).exists():
        return {"ok": False, "error": "username_taken"}
    member = Member.objects.create(username=username, email=email)
    return {"ok": True, "member_id": member.id}
""",
    ("joint_withdraw", "sqlalchemy", "correct"): """
from sqlalchemy import select
from app_models import Account, Withdrawal


def withdraw(Session, account_id, amount):
    with Session() as session, session.begin():
        owner = session.scalar(select(Account.customer_id).where(Account.id == account_id))
        if owner is None:
            return {"ok": False, "error": "unknown_account"}
        accounts = session.execute(
            select(Account)
            .where(Account.customer_id == owner)
            .order_by(Account.id)
            .with_for_update()
        ).scalars().all()
        if sum(a.balance for a in accounts) < amount:
            return {"ok": False, "error": "insufficient_funds"}
        me = next(a for a in accounts if a.id == account_id)
        me.balance -= amount
        withdrawal = Withdrawal(account_id=account_id, amount=amount)
        session.add(withdrawal)
        session.flush()
        return {"ok": True, "withdrawal_id": withdrawal.id}
""",
    ("joint_withdraw", "sqlalchemy", "buggy"): """
from sqlalchemy import func, select
from app_models import Account, Withdrawal


def withdraw(Session, account_id, amount):
    with Session() as session, session.begin():
        me = session.get(Account, account_id)
        if me is None:
            return {"ok": False, "error": "unknown_account"}
        total = session.scalar(
            select(func.sum(Account.balance)).where(Account.customer_id == me.customer_id)
        )
        if total < amount:
            return {"ok": False, "error": "insufficient_funds"}
        me.balance = me.balance - amount
        withdrawal = Withdrawal(account_id=account_id, amount=amount)
        session.add(withdrawal)
        session.flush()
        return {"ok": True, "withdrawal_id": withdrawal.id}
""",
    ("joint_withdraw", "django", "correct"): """
from django.db import transaction
from shop.models import Account, Withdrawal


def withdraw(account_id, amount):
    with transaction.atomic():
        owner = Account.objects.filter(id=account_id).values_list("customer_id", flat=True).first()
        if owner is None:
            return {"ok": False, "error": "unknown_account"}
        accounts = list(
            Account.objects.select_for_update().filter(customer_id=owner).order_by("id")
        )
        if sum(a.balance for a in accounts) < amount:
            return {"ok": False, "error": "insufficient_funds"}
        me = next(a for a in accounts if a.id == account_id)
        me.balance -= amount
        me.save(update_fields=["balance"])
        withdrawal = Withdrawal.objects.create(account=me, amount=amount)
    return {"ok": True, "withdrawal_id": withdrawal.id}
""",
    ("joint_withdraw", "django", "buggy"): """
from django.db import transaction
from django.db.models import Sum
from shop.models import Account, Withdrawal


def withdraw(account_id, amount):
    with transaction.atomic():
        me = Account.objects.filter(id=account_id).first()
        if me is None:
            return {"ok": False, "error": "unknown_account"}
        total = Account.objects.filter(customer_id=me.customer_id).aggregate(t=Sum("balance"))["t"]
        if total < amount:
            return {"ok": False, "error": "insufficient_funds"}
        me.balance = me.balance - amount
        me.save(update_fields=["balance"])
        withdrawal = Withdrawal.objects.create(account=me, amount=amount)
    return {"ok": True, "withdrawal_id": withdrawal.id}
""",
    ("seat_reserve", "sqlalchemy", "correct"): """
from sqlalchemy import func, select
from app_models import Event, Reservation


def reserve(Session, event_id, seats):
    with Session() as session, session.begin():
        event = session.execute(
            select(Event).where(Event.id == event_id).with_for_update()
        ).scalar_one_or_none()
        if event is None:
            return {"ok": False, "error": "unknown_event"}
        taken = session.scalar(
            select(func.coalesce(func.sum(Reservation.seats), 0)).where(
                Reservation.event_id == event_id
            )
        )
        if taken + seats > event.capacity:
            return {"ok": False, "error": "sold_out"}
        reservation = Reservation(event_id=event_id, seats=seats)
        session.add(reservation)
        session.flush()
        return {"ok": True, "reservation_id": reservation.id}
""",
    ("seat_reserve", "sqlalchemy", "buggy"): """
from sqlalchemy import func, select
from app_models import Event, Reservation


def reserve(Session, event_id, seats):
    with Session() as session:
        event = session.get(Event, event_id)
        if event is None:
            return {"ok": False, "error": "unknown_event"}
        taken = session.scalar(
            select(func.coalesce(func.sum(Reservation.seats), 0)).where(
                Reservation.event_id == event_id
            )
        )
        if taken + seats > event.capacity:
            return {"ok": False, "error": "sold_out"}
        reservation = Reservation(event_id=event_id, seats=seats)
        session.add(reservation)
        session.commit()
        return {"ok": True, "reservation_id": reservation.id}
""",
    ("seat_reserve", "django", "correct"): """
from django.db import transaction
from django.db.models import Sum
from shop.models import Event, Reservation


def reserve(event_id, seats):
    with transaction.atomic():
        event = Event.objects.select_for_update().filter(id=event_id).first()
        if event is None:
            return {"ok": False, "error": "unknown_event"}
        taken = Reservation.objects.filter(event=event).aggregate(t=Sum("seats"))["t"] or 0
        if taken + seats > event.capacity:
            return {"ok": False, "error": "sold_out"}
        reservation = Reservation.objects.create(event=event, seats=seats)
    return {"ok": True, "reservation_id": reservation.id}
""",
    ("seat_reserve", "django", "buggy"): """
from django.db.models import Sum
from shop.models import Event, Reservation


def reserve(event_id, seats):
    event = Event.objects.filter(id=event_id).first()
    if event is None:
        return {"ok": False, "error": "unknown_event"}
    taken = Reservation.objects.filter(event=event).aggregate(t=Sum("seats"))["t"] or 0
    if taken + seats > event.capacity:
        return {"ok": False, "error": "sold_out"}
    reservation = Reservation.objects.create(event=event, seats=seats)
    return {"ok": True, "reservation_id": reservation.id}
""",
    ("room_book", "sqlalchemy", "correct"): """
from sqlalchemy import select
from app_models import Booking, Room


def book(Session, room_id, start_hour, end_hour):
    with Session() as session, session.begin():
        room = session.execute(
            select(Room).where(Room.id == room_id).with_for_update()
        ).scalar_one_or_none()
        if room is None:
            return {"ok": False, "error": "unknown_room"}
        clash = session.scalar(
            select(Booking.id).where(
                Booking.room_id == room_id,
                Booking.start_hour < end_hour,
                Booking.end_hour > start_hour,
            )
        )
        if clash:
            return {"ok": False, "error": "conflict"}
        booking = Booking(room_id=room_id, start_hour=start_hour, end_hour=end_hour)
        session.add(booking)
        session.flush()
        return {"ok": True, "booking_id": booking.id}
""",
    ("room_book", "sqlalchemy", "buggy"): """
from sqlalchemy import select
from app_models import Booking, Room


def book(Session, room_id, start_hour, end_hour):
    with Session() as session:
        if session.get(Room, room_id) is None:
            return {"ok": False, "error": "unknown_room"}
        clash = session.scalar(
            select(Booking.id).where(
                Booking.room_id == room_id,
                Booking.start_hour < end_hour,
                Booking.end_hour > start_hour,
            )
        )
        if clash:
            return {"ok": False, "error": "conflict"}
        booking = Booking(room_id=room_id, start_hour=start_hour, end_hour=end_hour)
        session.add(booking)
        session.commit()
        return {"ok": True, "booking_id": booking.id}
""",
    ("room_book", "django", "correct"): """
from django.db import transaction
from shop.models import Booking, Room


def book(room_id, start_hour, end_hour):
    with transaction.atomic():
        room = Room.objects.select_for_update().filter(id=room_id).first()
        if room is None:
            return {"ok": False, "error": "unknown_room"}
        if Booking.objects.filter(
            room=room, start_hour__lt=end_hour, end_hour__gt=start_hour
        ).exists():
            return {"ok": False, "error": "conflict"}
        booking = Booking.objects.create(room=room, start_hour=start_hour, end_hour=end_hour)
    return {"ok": True, "booking_id": booking.id}
""",
    ("room_book", "django", "buggy"): """
from shop.models import Booking, Room


def book(room_id, start_hour, end_hour):
    room = Room.objects.filter(id=room_id).first()
    if room is None:
        return {"ok": False, "error": "unknown_room"}
    if Booking.objects.filter(room=room, start_hour__lt=end_hour, end_hour__gt=start_hour).exists():
        return {"ok": False, "error": "conflict"}
    booking = Booking.objects.create(room=room, start_hour=start_hour, end_hour=end_hour)
    return {"ok": True, "booking_id": booking.id}
""",
    ("wallet_transfer", "sqlalchemy", "correct"): """
from sqlalchemy import select
from app_models import Wallet


def transfer(Session, from_wallet_id, to_wallet_id, amount):
    with Session() as session, session.begin():
        wallets = {
            w.id: w
            for w in session.execute(
                select(Wallet)
                .where(Wallet.id.in_([from_wallet_id, to_wallet_id]))
                .order_by(Wallet.id)
                .with_for_update()
            ).scalars()
        }
        if from_wallet_id not in wallets or to_wallet_id not in wallets:
            return {"ok": False, "error": "unknown_wallet"}
        if wallets[from_wallet_id].balance < amount:
            return {"ok": False, "error": "insufficient_funds"}
        wallets[from_wallet_id].balance -= amount
        wallets[to_wallet_id].balance += amount
        return {"ok": True}
""",
    ("wallet_transfer", "sqlalchemy", "buggy"): """
from app_models import Wallet


def transfer(Session, from_wallet_id, to_wallet_id, amount):
    with Session() as session:
        source = session.get(Wallet, from_wallet_id)
        target = session.get(Wallet, to_wallet_id)
        if source is None or target is None:
            return {"ok": False, "error": "unknown_wallet"}
        if source.balance < amount:
            return {"ok": False, "error": "insufficient_funds"}
        source.balance = source.balance - amount
        target.balance = target.balance + amount
        session.commit()
        return {"ok": True}
""",
    ("wallet_transfer", "django", "correct"): """
from django.db import transaction
from shop.models import Wallet


def transfer(from_wallet_id, to_wallet_id, amount):
    with transaction.atomic():
        wallets = {
            w.id: w
            for w in Wallet.objects.select_for_update()
            .filter(id__in=[from_wallet_id, to_wallet_id])
            .order_by("id")
        }
        if from_wallet_id not in wallets or to_wallet_id not in wallets:
            return {"ok": False, "error": "unknown_wallet"}
        if wallets[from_wallet_id].balance < amount:
            return {"ok": False, "error": "insufficient_funds"}
        wallets[from_wallet_id].balance -= amount
        wallets[to_wallet_id].balance += amount
        wallets[from_wallet_id].save(update_fields=["balance"])
        wallets[to_wallet_id].save(update_fields=["balance"])
    return {"ok": True}
""",
    ("wallet_transfer", "django", "buggy"): """
from shop.models import Wallet


def transfer(from_wallet_id, to_wallet_id, amount):
    source = Wallet.objects.filter(id=from_wallet_id).first()
    target = Wallet.objects.filter(id=to_wallet_id).first()
    if source is None or target is None:
        return {"ok": False, "error": "unknown_wallet"}
    if source.balance < amount:
        return {"ok": False, "error": "insufficient_funds"}
    source.balance = source.balance - amount
    source.save(update_fields=["balance"])
    target.balance = target.balance + amount
    target.save(update_fields=["balance"])
    return {"ok": True}
""",
}
