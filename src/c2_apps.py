"""Application tasks from django-oscar 4.2.1 and CTFd 3.8.9: prompts, schemas, seeds, rules and references."""

from __future__ import annotations

from pathlib import Path
from typing import Any

APPS = {
    "oscar": {"package": "django-oscar", "version": "4.2.1", "framework": "django"},
    "ctfd": {"package": "CTFd", "version": "3.8.9", "framework": "flask_sqlalchemy"},
}
DEFAULT_KEY = {"django": "django", "flask_sqlalchemy": "sqlalchemy"}
CONDITIONS = ("plain", "hint")
TABLES = {
    "oscar": ["voucher_voucherapplication", "order_order", "voucher_voucher", "auth_user"],
    "ctfd": ["unlocks", "awards", "solves", "submissions", "hints", "flags", "challenges", "users"],
}

EXCERPTS: dict[str, list[tuple[str, int, int, str]]] = {
    'oscar': [
        ('apps/voucher/abstract_models.py', 86, 147, '''class AbstractVoucher(models.Model):
    """
    A voucher.  This is simply a link to a collection of offers.

    Note that there are three possible "usage" modes:
    (a) Single use
    (b) Multi-use
    (c) Once per customer

    Oscar enforces those modes by creating VoucherApplication
    instances when a voucher is used for an order.
    """

    name = models.CharField(
        _("Name"),
        max_length=128,
        unique=True,
        help_text=_(
            "This will be shown in the checkout"
            " and basket once the voucher is"
            " entered"
        ),
    )
    code = models.CharField(
        _("Code"),
        max_length=128,
        db_index=True,
        unique=True,
        help_text=_("Case insensitive / No spaces allowed"),
    )
    offers = models.ManyToManyField(
        "offer.ConditionalOffer",
        related_name="vouchers",
        verbose_name=_("Offers"),
        limit_choices_to={"offer_type": "Voucher"},
    )

    SINGLE_USE, MULTI_USE, ONCE_PER_CUSTOMER = (
        "Single use",
        "Multi-use",
        "Once per customer",
    )
    USAGE_CHOICES = (
        (SINGLE_USE, _("Can be used once by one customer")),
        (MULTI_USE, _("Can be used multiple times by multiple customers")),
        (ONCE_PER_CUSTOMER, _("Can only be used once per customer")),
    )
    usage = models.CharField(
        _("Usage"), max_length=128, choices=USAGE_CHOICES, default=MULTI_USE
    )

    start_datetime = models.DateTimeField(_("Start datetime"), db_index=True)
    end_datetime = models.DateTimeField(_("End datetime"), db_index=True)

    # Reporting information. Not used to enforce any consumption limits.
    num_basket_additions = models.PositiveIntegerField(
        _("Times added to basket"), default=0
    )
    num_orders = models.PositiveIntegerField(_("Times on orders"), default=0)
    total_discount = models.DecimalField(
        _("Total discount"), decimal_places=2, max_digits=12, default=Decimal("0.00")
    )
'''),
        ('apps/voucher/abstract_models.py', 184, 258, '''    def is_active(self, test_datetime=None):
        """
        Test whether this voucher is currently active.
        """
        test_datetime = test_datetime or timezone.now()
        return self.start_datetime <= test_datetime <= self.end_datetime

    def is_expired(self):
        """
        Test whether this voucher has passed its expiration date
        """
        now = timezone.now()
        return self.end_datetime < now

    def is_available_to_user(self, user=None):
        """
        Test whether this voucher is available to the passed user.

        Returns a tuple of a boolean for whether it is successful, and a
        availability message.
        """
        is_available, message = False, ""
        if self.usage == self.SINGLE_USE:
            is_available = not self.applications.exists()
            if not is_available:
                message = _("This voucher has already been used")
        elif self.usage == self.MULTI_USE:
            is_available = True
        elif self.usage == self.ONCE_PER_CUSTOMER:
            if not user.is_authenticated:
                is_available = False
                message = _("This voucher is only available to signed in users")
            else:
                is_available = not self.applications.filter(
                    voucher=self, user=user
                ).exists()
                if not is_available:
                    message = _(
                        "You have already used this voucher in a previous order"
                    )
        return is_available, message

    def is_available_for_basket(self, basket):
        """
        Tests whether this voucher is available to the passed basket.

        Returns a tuple of a boolean for whether it is successful, and a
        availability message.
        """
        is_available, message = self.is_available_to_user(user=basket.owner)
        if not is_available:
            return False, message

        is_available, message = False, _(
            "This voucher is not available for this basket"
        )
        for offer in self.offers.all():
            if offer.is_condition_satisfied(basket=basket):
                is_available = True
                message = ""
                break
        return is_available, message

    def record_usage(self, order, user):
        """
        Records a usage of this voucher in an order.
        """
        if user.is_authenticated:
            self.applications.create(voucher=self, order=order, user=user)
        else:
            self.applications.create(voucher=self, order=order)
        self.num_orders += 1
        self.save()

    record_usage.alters_data = True
'''),
        ('apps/voucher/abstract_models.py', 281, 308, '''class AbstractVoucherApplication(models.Model):
    """
    For tracking how often a voucher has been used in an order.

    This is used to enforce the voucher usage mode in
    Voucher.is_available_to_user, and created in Voucher.record_usage.
    """

    voucher = models.ForeignKey(
        "voucher.Voucher",
        on_delete=models.CASCADE,
        related_name="applications",
        verbose_name=_("Voucher"),
    )

    # It is possible for an anonymous user to apply a voucher so we need to
    # allow the user to be nullable
    user = models.ForeignKey(
        AUTH_USER_MODEL,
        blank=True,
        null=True,
        on_delete=models.CASCADE,
        verbose_name=_("User"),
    )
    order = models.ForeignKey(
        "order.Order", on_delete=models.CASCADE, verbose_name=_("Order")
    )
    date_created = models.DateTimeField(auto_now_add=True, db_index=True)
'''),
        ('apps/order/abstract_models.py', 29, 60, '''class AbstractOrder(models.Model):
    """
    The main order model
    """

    number = models.CharField(
        _("Order number"), max_length=128, db_index=True, unique=True
    )

    # We track the site that each order is placed within
    site = models.ForeignKey(
        "sites.Site", verbose_name=_("Site"), null=True, on_delete=models.SET_NULL
    )

    basket = models.ForeignKey(
        "basket.Basket",
        verbose_name=_("Basket"),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )

    # Orders can be placed without the user authenticating so we don't always
    # have a customer ID.
    user = models.ForeignKey(
        AUTH_USER_MODEL,
        related_name="orders",
        null=True,
        blank=True,
        verbose_name=_("User"),
        on_delete=models.SET_NULL,
    )
'''),
    ],
    'ctfd': [
        ('models/__init__.py', 110, 130, '''class Challenges(db.Model):
    __tablename__ = "challenges"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80))
    description = db.Column(db.Text)
    attribution = db.Column(db.Text)
    connection_info = db.Column(db.Text)
    next_id = db.Column(db.Integer, db.ForeignKey("challenges.id", ondelete="SET NULL"))
    max_attempts = db.Column(db.Integer, default=0)
    value = db.Column(db.Integer)
    category = db.Column(db.String(80))
    type = db.Column(db.String(80))
    state = db.Column(db.String(80), nullable=False, default="visible")
    logic = db.Column(db.String(80), nullable=False, default="any")
    initial = db.Column(db.Integer, nullable=True)
    minimum = db.Column(db.Integer, nullable=True)
    decay = db.Column(db.Integer, nullable=True)
    position = db.Column(db.Integer, nullable=False, default=0)
    function = db.Column(db.String(32), default="static")

    requirements = db.Column(db.JSON)
'''),
        ('models/__init__.py', 191, 203, '''class Hints(db.Model):
    __tablename__ = "hints"
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(80))
    type = db.Column(db.String(80), default="standard")
    challenge_id = db.Column(
        db.Integer, db.ForeignKey("challenges.id", ondelete="CASCADE")
    )
    content = db.Column(db.Text)
    cost = db.Column(db.Integer, default=0)
    requirements = db.Column(db.JSON)

    __mapper_args__ = {"polymorphic_identity": "standard", "polymorphic_on": type}
'''),
        ('models/__init__.py', 237, 264, '''class Awards(db.Model):
    __tablename__ = "awards"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"))
    team_id = db.Column(db.Integer, db.ForeignKey("teams.id", ondelete="CASCADE"))
    type = db.Column(db.String(80), default="standard")
    name = db.Column(db.String(80))
    description = db.Column(db.Text)
    date = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    value = db.Column(db.Integer)
    category = db.Column(db.String(80))
    icon = db.Column(db.Text)
    requirements = db.Column(db.JSON)

    user = db.relationship("Users", foreign_keys="Awards.user_id", lazy="select")
    team = db.relationship("Teams", foreign_keys="Awards.team_id", lazy="select")

    __mapper_args__ = {"polymorphic_identity": "standard", "polymorphic_on": type}

    @hybrid_property
    def account_id(self):
        from CTFd.utils import get_config

        user_mode = get_config("user_mode")
        if user_mode == "teams":
            return self.team_id
        elif user_mode == "users":
            return self.user_id
'''),
        ('models/__init__.py', 374, 384, '''class Flags(db.Model):
    __tablename__ = "flags"
    id = db.Column(db.Integer, primary_key=True)
    challenge_id = db.Column(
        db.Integer, db.ForeignKey("challenges.id", ondelete="CASCADE")
    )
    type = db.Column(db.String(80))
    content = db.Column(db.Text)
    data = db.Column(db.Text)

    __mapper_args__ = {"polymorphic_on": type}
'''),
        ('models/__init__.py', 393, 420, '''class Users(db.Model):
    __tablename__ = "users"
    __table_args__ = (db.UniqueConstraint("id", "oauth_id"), {})
    # Core attributes
    id = db.Column(db.Integer, primary_key=True)
    oauth_id = db.Column(db.Integer, unique=True)
    # User names are not constrained to be unique to allow for official/unofficial teams.
    name = db.Column(db.String(128))
    password = db.Column(db.String(128))
    email = db.Column(db.String(128), unique=True)
    type = db.Column(db.String(80))
    secret = db.Column(db.String(128))

    # Supplementary attributes
    website = db.Column(db.String(128))
    affiliation = db.Column(db.String(128))
    country = db.Column(db.String(32))
    bracket_id = db.Column(
        db.Integer, db.ForeignKey("brackets.id", ondelete="SET NULL")
    )
    hidden = db.Column(db.Boolean, default=False)
    banned = db.Column(db.Boolean, default=False)
    verified = db.Column(db.Boolean, default=False)
    language = db.Column(db.String(32), nullable=True, default=None)
    change_password = db.Column(db.Boolean, default=False)

    # Relationship for Teams
    team_id = db.Column(db.Integer, db.ForeignKey("teams.id"))
'''),
        ('models/__init__.py', 432, 434, '''    created = db.Column(db.DateTime, default=datetime.datetime.utcnow)

    __mapper_args__ = {"polymorphic_identity": "user", "polymorphic_on": type}
'''),
        ('models/__init__.py', 445, 463, '''    @hybrid_property
    def account_id(self):
        from CTFd.utils import get_config

        user_mode = get_config("user_mode")
        if user_mode == "teams":
            return self.team_id
        elif user_mode == "users":
            return self.id

    @hybrid_property
    def account(self):
        from CTFd.utils import get_config

        user_mode = get_config("user_mode")
        if user_mode == "teams":
            return self.team
        elif user_mode == "users":
            return self
'''),
        ('models/__init__.py', 558, 589, '''    @cache.memoize()
    def get_score(self, admin=False):
        score = db.func.sum(Challenges.value).label("score")
        user = (
            db.session.query(Solves.user_id, score)
            .join(Users, Solves.user_id == Users.id)
            .join(Challenges, Solves.challenge_id == Challenges.id)
            .filter(Users.id == self.id)
        )

        award_score = db.func.sum(Awards.value).label("award_score")
        award = db.session.query(award_score).filter_by(user_id=self.id)

        if not admin:
            freeze = Configs.query.filter_by(key="freeze").first()
            if freeze and freeze.value:
                freeze = int(freeze.value)
                freeze = datetime.datetime.utcfromtimestamp(freeze)
                user = user.filter(Solves.date < freeze)
                award = award.filter(Awards.date < freeze)

        user = user.group_by(Solves.user_id).first()
        award = award.first()

        if user and award:
            return int(user.score or 0) + int(award.award_score or 0)
        elif user:
            return int(user.score or 0)
        elif award:
            return int(award.award_score or 0)
        else:
            return 0
'''),
        ('models/__init__.py', 872, 883, '''class Submissions(db.Model):
    __tablename__ = "submissions"
    id = db.Column(db.Integer, primary_key=True)
    challenge_id = db.Column(
        db.Integer, db.ForeignKey("challenges.id", ondelete="CASCADE")
    )
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"))
    team_id = db.Column(db.Integer, db.ForeignKey("teams.id", ondelete="CASCADE"))
    ip = db.Column(db.String(46))
    provided = db.Column(db.Text)
    type = db.Column(db.String(32))
    date = db.Column(db.DateTime, default=datetime.datetime.utcnow)
'''),
        ('models/__init__.py', 892, 902, '''    __mapper_args__ = {"polymorphic_on": type}

    @hybrid_property
    def account_id(self):
        from CTFd.utils import get_config

        user_mode = get_config("user_mode")
        if user_mode == "teams":
            return self.team_id
        elif user_mode == "users":
            return self.user_id
'''),
        ('models/__init__.py', 926, 960, '''class Solves(Submissions):
    __tablename__ = "solves"
    __table_args__ = (
        db.UniqueConstraint("challenge_id", "user_id"),
        db.UniqueConstraint("challenge_id", "team_id"),
        {},
    )
    id = db.Column(
        None, db.ForeignKey("submissions.id", ondelete="CASCADE"), primary_key=True
    )
    challenge_id = column_property(
        db.Column(db.Integer, db.ForeignKey("challenges.id", ondelete="CASCADE")),
        Submissions.challenge_id,
    )
    user_id = column_property(
        db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE")),
        Submissions.user_id,
    )
    team_id = column_property(
        db.Column(db.Integer, db.ForeignKey("teams.id", ondelete="CASCADE")),
        Submissions.team_id,
    )

    user = db.relationship("Users", foreign_keys="Solves.user_id", lazy="select")
    team = db.relationship("Teams", foreign_keys="Solves.team_id", lazy="select")
    challenge = db.relationship(
        "Challenges", foreign_keys="Solves.challenge_id", lazy="select"
    )

    __mapper_args__ = {"polymorphic_identity": "correct"}


class Fails(Submissions):
    __mapper_args__ = {"polymorphic_identity": "incorrect"}

'''),
        ('models/__init__.py', 974, 1000, '''class Unlocks(db.Model):
    __tablename__ = "unlocks"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"))
    team_id = db.Column(db.Integer, db.ForeignKey("teams.id", ondelete="CASCADE"))
    target = db.Column(db.Integer)
    date = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    type = db.Column(db.String(32))

    __mapper_args__ = {"polymorphic_on": type}

    @hybrid_property
    def account_id(self):
        from CTFd.utils import get_config

        user_mode = get_config("user_mode")
        if user_mode == "teams":
            return self.team_id
        elif user_mode == "users":
            return self.user_id

    def __repr__(self):
        return "<Unlock %r>" % self.id


class HintUnlocks(Unlocks):
    __mapper_args__ = {"polymorphic_identity": "hints"}
'''),
    ],
}


def verify_excerpts(app: str, root: Path) -> list[str]:
    wrong = []
    for relative, first, last, text in EXCERPTS[app]:
        lines = (root / relative).read_text("utf-8").split("\n")[first - 1:last]
        if "\n".join(lines) + "\n" != text:
            wrong.append(f"{relative}:{first}-{last}")
    return wrong


def models_text(app: str) -> str:
    return "\n# ...\n\n".join(text for _, _, _, text in EXCERPTS[app])


FRAMEWORK_TEXT = {
    "oscar": (
        "The application is django-oscar 4.2.1, an e-commerce framework, on Django 5.2 with "
        "PostgreSQL through psycopg 3, with the default database settings (autocommit on, "
        "ATOMIC_REQUESTS off). The concrete models `Voucher`, `VoucherApplication` and `Order` "
        "inherit these abstract models unchanged (excerpts from Oscar's source):\n\n"
        "```python\n{models}```\n\n"
        "Load the models as Oscar does, with `from oscar.core.loading import get_model`, for "
        'example `Voucher = get_model("voucher", "Voucher")`. The user model is Django\'s '
        "`django.contrib.auth.models.User`. The checkout calls the function once per HTTP request."
    ),
    "ctfd": (
        "The application is CTFd 3.8.9, a capture-the-flag platform, on Flask 2.1 with "
        "Flask-SQLAlchemy 2.5 (SQLAlchemy 1.4) and PostgreSQL through psycopg2, in users mode "
        "(no teams). The models exist in the module `CTFd.models` (excerpts from CTFd's "
        "source):\n\n```python\n{models}```\n\n"
        "Import them with `from CTFd.models import db, {names}`. The function runs inside the "
        "Flask application and request context of an API request and uses `db.session`, as "
        "CTFd's API does. The API calls the function once per HTTP request."
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

HANDLERS: dict[str, dict[str, Any]] = {
    "oscar_voucher": {
        "app": "oscar",
        "function": "apply_voucher",
        "arguments": ["order_number", "code"],
        "names": [],
        "rules": ["oscar_voucher_single", "oscar_voucher_customer"],
        "spec": (
            "Implement `apply_voucher(order_number, code)`, which the checkout calls just after an "
            "order is placed to record that the order used a voucher. Look up the order by its "
            "number and the voucher by its code. If there is no voucher with this code, return "
            '{"ok": False, "error": "unknown_voucher"}. If the voucher is not active now, return '
            '{"ok": False, "error": "inactive"}. If the voucher is not available to the order\'s '
            "user under its usage mode, return "
            '{"ok": False, "error": "unavailable"}. An order without a user was placed by an '
            "anonymous customer; Oscar's voucher methods take Django's `AnonymousUser` "
            "(`django.contrib.auth.models`) for such a customer, as the checkout's `request.user` "
            "is. Otherwise record the usage as Oscar does, with a `VoucherApplication` for the "
            "order and its user and one more in the voucher's `num_orders`, and return "
            '{"ok": True}.'
        ),
    },
    "ctfd_attempts": {
        "app": "ctfd",
        "function": "attempt",
        "arguments": ["user_id", "challenge_id", "provided"],
        "names": ["Challenges", "Fails", "Flags", "Solves", "Submissions", "Users"],
        "rules": ["ctfd_attempts"],
        "spec": (
            "Implement `attempt(user_id, challenge_id, provided)`, which records a flag "
            "submission. If the user has already solved the challenge, return "
            '{"ok": False, "error": "already_solved"}. If the challenge\'s `max_attempts` is '
            "greater than 0 and the user already has `max_attempts` failed submissions for it, "
            'return {"ok": False, "error": "max_attempts"} without recording anything. Otherwise '
            "compare `provided` with the challenge's static flags (`Flags.type == \"static\"`, an "
            "exact match with `Flags.content`). On a match, record a `Solves` row and return "
            '{"ok": True, "status": "correct"}; otherwise record a `Fails` row and return '
            '{"ok": True, "status": "incorrect"}. Each submission records the user\'s id, the '
            "challenge's id and `provided`, and leaves `team_id` and `ip` empty."
        ),
    },
    "ctfd_hint_unlock": {
        "app": "ctfd",
        "function": "unlock_hint",
        "arguments": ["user_id", "hint_id"],
        "names": ["Awards", "Challenges", "HintUnlocks", "Hints", "Solves", "Unlocks", "Users"],
        "rules": ["ctfd_hint_unlock"],
        "spec": (
            "Implement `unlock_hint(user_id, hint_id)`, which lets a user buy a hint with points. "
            "If the user has already unlocked the hint, return "
            '{"ok": False, "error": "already_unlocked"}. If the hint\'s `cost` is greater than the '
            'user\'s current score, return {"ok": False, "error": "insufficient_score"}. Otherwise '
            "record a `HintUnlocks` row for the user with `target` set to the hint's id, and an "
            "`Awards` row for the user whose `value` is minus the hint's cost and whose `name`, "
            "`description` and `category` are the hint's, as CTFd does, and return "
            '{"ok": True, "unlock_id": <new unlock id>}. A user\'s score is the sum of the values '
            "of the challenges they have solved plus the sum of their awards."
        ),
    },
}


def signature(handler: str) -> str:
    spec = HANDLERS[handler]
    return f"def {spec['function']}({', '.join(spec['arguments'])}) -> dict:"


def prompt(handler: str, condition: str) -> str:
    if condition not in CONDITIONS:
        raise ValueError("unknown condition")
    spec = HANDLERS[handler]
    app = spec["app"]
    framework = FRAMEWORK_TEXT[app].format(models=models_text(app),
                                           names=", ".join(spec["names"]))
    return PROMPT.format(framework=framework, spec=spec["spec"], signature=signature(handler),
                         hint=HINT if condition == "hint" else "")


STAMP = "'2026-01-01 00:00:00'"


def quote(engine: str, name: str) -> str:
    return f"`{name}`" if engine == "mysql" else f'"{name}"'


def oscar_users(ids: list[int]) -> str:
    rows = ", ".join(
        f"({i}, '!', FALSE, 'user{i}', '', '', 'user{i}@example.org', FALSE, TRUE, {STAMP})"
        for i in ids)
    return ("INSERT INTO auth_user (id, password, is_superuser, username, first_name, last_name, "
            f"email, is_staff, is_active, date_joined) VALUES {rows}")


def oscar_orders(orders: list[tuple[int, str, int | None]]) -> str:
    rows = ", ".join(
        f"({i}, '{number}', 'GBP', 10.00, 10.00, 0.00, 0.00, 'Free shipping', 'free-shipping', "
        f"'Pending', '', {STAMP}, FALSE, {'NULL' if user is None else user})"
        for i, number, user in orders)
    return ("INSERT INTO order_order (id, number, currency, total_incl_tax, total_excl_tax, "
            "shipping_incl_tax, shipping_excl_tax, shipping_method, shipping_code, status, "
            f"guest_email, date_placed, analytics_tracked, user_id) VALUES {rows}")


def oscar_vouchers(engine: str, vouchers: list[tuple[int, str, str, str]]) -> str:
    rows = ", ".join(
        f"({i}, '{code} voucher', '{code}', '{usage}', '2020-01-01 00:00:00', '{end} 00:00:00', "
        f"0, 0, 0.00, {STAMP})"
        for i, code, usage, end in vouchers)
    return (f"INSERT INTO voucher_voucher (id, name, code, {quote(engine, 'usage')}, "
            "start_datetime, end_datetime, num_basket_additions, num_orders, total_discount, "
            f"date_created) VALUES {rows}")


def ctfd_users(ids: list[int]) -> str:
    rows = ", ".join(
        f"({i}, 'user{i}', 'user{i}@example.org', 'user', FALSE, FALSE, TRUE, {STAMP})"
        for i in ids)
    return ("INSERT INTO users (id, name, email, type, hidden, banned, verified, created) "
            f"VALUES {rows}")


def ctfd_challenges(engine: str, challenges: list[tuple[int, int, int]]) -> list[str]:
    rows = ", ".join(
        f"({i}, 'challenge{i}', 'Find the flag.', {attempts}, {value}, 'web', 'standard', "
        "'visible', 'any', 0, 'static')"
        for i, value, attempts in challenges)
    flags = ", ".join(f"({i}, {i}, 'static', 'flag{{{i}}}', '')" for i, _, _ in challenges)
    return [
        "INSERT INTO challenges (id, name, description, max_attempts, value, category, type, "
        f"state, logic, position, {quote(engine, 'function')}) VALUES {rows}",
        f"INSERT INTO flags (id, challenge_id, type, content, data) VALUES {flags}",
    ]


def ctfd_solves(solves: list[tuple[int, int, int]]) -> list[str]:
    submissions = ", ".join(
        f"({s}, {c}, {u}, 'flag{{{c}}}', 'correct', {STAMP})" for s, u, c in solves)
    rows = ", ".join(f"({s}, {c}, {u})" for s, u, c in solves)
    return [
        "INSERT INTO submissions (id, challenge_id, user_id, provided, type, date) "
        f"VALUES {submissions}",
        f"INSERT INTO solves (id, challenge_id, user_id) VALUES {rows}",
    ]


def ctfd_hints(hints: list[tuple[int, int, int]]) -> str:
    rows = ", ".join(f"({i}, 'Hint {i}', 'standard', {c}, 'Look closer.', {cost})"
                     for i, c, cost in hints)
    return f"INSERT INTO hints (id, title, type, challenge_id, content, cost) VALUES {rows}"


def seed(rule_or_handler: str, kind: str, engine: str) -> list[str]:
    if kind == "functional":
        if rule_or_handler == "oscar_voucher":
            return [
                oscar_users([1, 2]),
                oscar_orders([(1, "100001", 1), (2, "100002", 2), (3, "100003", 1),
                              (4, "100004", 2), (5, "100005", 1), (6, "100006", None)]),
                oscar_vouchers(engine, [(1, "ONCE", "Single use", "2099-01-01"),
                                        (2, "PERCUST", "Once per customer", "2099-01-01"),
                                        (3, "MULTI", "Multi-use", "2099-01-01"),
                                        (4, "OLD", "Multi-use", "2021-01-01")]),
            ]
        if rule_or_handler == "ctfd_attempts":
            return [ctfd_users([1, 2]),
                    *ctfd_challenges(engine, [(1, 100, 2), (2, 50, 0)])]
        if rule_or_handler == "ctfd_hint_unlock":
            return [ctfd_users([1, 2]),
                    *ctfd_challenges(engine, [(1, 100, 0)]),
                    *ctfd_solves([(1, 1, 1)]),
                    ctfd_hints([(1, 1, 60), (2, 1, 30), (3, 1, 0)])]
    else:
        if rule_or_handler == "oscar_voucher_single":
            return [oscar_users(list(range(1, 9))),
                    oscar_orders([(i, f"C{i}", i) for i in range(1, 9)]),
                    oscar_vouchers(engine, [(1, "ONCE", "Single use", "2099-01-01")])]
        if rule_or_handler == "oscar_voucher_customer":
            return [oscar_users([1, 2]),
                    oscar_orders([(i, f"C{i}", (i - 1) % 2 + 1) for i in range(1, 9)]),
                    oscar_vouchers(engine, [(1, "PERCUST", "Once per customer", "2099-01-01")])]
        if rule_or_handler == "ctfd_attempts":
            return [ctfd_users([1]),
                    *ctfd_challenges(engine, [(1, 100, 3)])]
        if rule_or_handler == "ctfd_hint_unlock":
            return [ctfd_users([1]),
                    *ctfd_challenges(engine, [(1, 100, 0)]),
                    *ctfd_solves([(1, 1, 1)]),
                    ctfd_hints([(1, 1, 60), (2, 1, 60)])]
    raise KeyError((rule_or_handler, kind))


FUNCTIONAL: dict[str, dict[str, Any]] = {
    "oscar_voucher": {
        "calls": [
            [{"order_number": "100001", "code": "ONCE"}, True],
            [{"order_number": "100002", "code": "ONCE"}, False],
            [{"order_number": "100001", "code": "PERCUST"}, True],
            [{"order_number": "100003", "code": "PERCUST"}, False],
            [{"order_number": "100002", "code": "PERCUST"}, True],
            [{"order_number": "100006", "code": "PERCUST"}, False],
            [{"order_number": "100004", "code": "MULTI"}, True],
            [{"order_number": "100005", "code": "MULTI"}, True],
            [{"order_number": "100004", "code": "OLD"}, False],
            [{"order_number": "100004", "code": "NOPE"}, False],
        ],
        "state": [
            ["SELECT COUNT(*) FROM voucher_voucherapplication", 5],
            ["SELECT COUNT(*) FROM voucher_voucherapplication WHERE voucher_id = 2 AND user_id = 2",
             1],
            ["SELECT COUNT(*) FROM voucher_voucherapplication WHERE voucher_id = 3 AND order_id = 5",
             1],
            ["SELECT num_orders FROM voucher_voucher WHERE id = 1", 1],
            ["SELECT num_orders FROM voucher_voucher WHERE id = 2", 2],
            ["SELECT num_orders FROM voucher_voucher WHERE id = 3", 2],
            ["SELECT num_orders FROM voucher_voucher WHERE id = 4", 0],
        ],
    },
    "ctfd_attempts": {
        "calls": [
            [{"user_id": 1, "challenge_id": 1, "provided": "guess1"}, True],
            [{"user_id": 1, "challenge_id": 1, "provided": "guess2"}, True],
            [{"user_id": 1, "challenge_id": 1, "provided": "flag{1}"}, False],
            [{"user_id": 2, "challenge_id": 1, "provided": "flag{1}"}, True],
            [{"user_id": 2, "challenge_id": 1, "provided": "flag{1}"}, False],
            [{"user_id": 1, "challenge_id": 2, "provided": "guess3"}, True],
            [{"user_id": 1, "challenge_id": 2, "provided": "guess4"}, True],
            [{"user_id": 1, "challenge_id": 2, "provided": "guess5"}, True],
            [{"user_id": 1, "challenge_id": 2, "provided": "flag{2}"}, True],
        ],
        "state": [
            ["SELECT COUNT(*) FROM submissions WHERE type = 'incorrect' AND user_id = 1 "
             "AND challenge_id = 1", 2],
            ["SELECT COUNT(*) FROM submissions WHERE type = 'incorrect' AND user_id = 1 "
             "AND challenge_id = 2", 3],
            ["SELECT COUNT(*) FROM solves", 2],
            ["SELECT COUNT(*) FROM submissions WHERE type = 'correct' AND user_id = 2 "
             "AND challenge_id = 1 AND provided = 'flag{1}'", 1],
            ["SELECT COUNT(*) FROM submissions", 7],
        ],
    },
    "ctfd_hint_unlock": {
        "calls": [
            [{"user_id": 1, "hint_id": 1}, True],
            [{"user_id": 1, "hint_id": 1}, False],
            [{"user_id": 1, "hint_id": 2}, True],
            [{"user_id": 2, "hint_id": 3}, True],
            [{"user_id": 2, "hint_id": 2}, False],
        ],
        "state": [
            ["SELECT COUNT(*) FROM unlocks WHERE type = 'hints'", 3],
            ["SELECT COUNT(*) FROM unlocks WHERE user_id = 1 AND target = 2", 1],
            ["SELECT COALESCE(SUM(value), 0) FROM awards WHERE user_id = 1", -90],
            ["SELECT COALESCE(SUM(value), 0) FROM awards WHERE user_id = 2", 0],
            ["SELECT COUNT(*) FROM awards WHERE user_id = 1", 2],
        ],
    },
}

RULES: dict[str, dict[str, Any]] = {
    "oscar_voucher_single": {"handler": "oscar_voucher", "hazard": "check_then_insert",
                             "requests_per_worker": 1, "optimal_successes": 1},
    "oscar_voucher_customer": {"handler": "oscar_voucher", "hazard": "check_then_insert",
                               "requests_per_worker": 1, "optimal_successes": 2},
    "ctfd_attempts": {"handler": "ctfd_attempts", "hazard": "check_then_insert",
                      "requests_per_worker": 2, "optimal_successes": 3},
    "ctfd_hint_unlock": {"handler": "ctfd_hint_unlock", "hazard": "write_skew",
                         "requests_per_worker": 1, "optimal_successes": 1},
}


def app_of(rule_or_handler: str) -> str:
    handler = RULES.get(rule_or_handler, {}).get("handler", rule_or_handler)
    return HANDLERS[handler]["app"]


def framework_of(rule_or_handler: str) -> str:
    return APPS[app_of(rule_or_handler)]["framework"]


def request_plan(rule: str, worker: int) -> list[dict[str, Any]]:
    if rule in ("oscar_voucher_single", "oscar_voucher_customer"):
        code = "ONCE" if rule == "oscar_voucher_single" else "PERCUST"
        return [{"order_number": f"C{worker % 8 + 1}", "code": code}]
    if rule == "ctfd_attempts":
        return [{"user_id": 1, "challenge_id": 1, "provided": f"guess{worker}.{k}"}
                for k in range(RULES[rule]["requests_per_worker"])]
    if rule == "ctfd_hint_unlock":
        return [{"user_id": 1, "hint_id": worker % 2 + 1}]
    raise KeyError(rule)


def invariant(rule: str, query: Any) -> list[str]:
    found: list[str] = []
    if rule == "oscar_voucher_single":
        if query("SELECT COUNT(*) FROM voucher_voucherapplication WHERE voucher_id = 1") > 1:
            found.append("single_use_voucher_reused")
    elif rule == "oscar_voucher_customer":
        if query("SELECT COUNT(*) FROM (SELECT user_id FROM voucher_voucherapplication "
                 "WHERE voucher_id = 1 GROUP BY user_id HAVING COUNT(*) > 1) AS d"):
            found.append("customer_voucher_reused")
    elif rule == "ctfd_attempts":
        if query("SELECT COUNT(*) FROM submissions WHERE type = 'incorrect' AND user_id = 1 "
                 "AND challenge_id = 1") > 3:
            found.append("attempts_exceeded")
    elif rule == "ctfd_hint_unlock":
        score = query(
            "SELECT (SELECT COALESCE(SUM(c.value), 0) FROM solves s JOIN challenges c "
            "ON c.id = s.challenge_id WHERE s.user_id = 1) + "
            "(SELECT COALESCE(SUM(value), 0) FROM awards WHERE user_id = 1)")
        if score < 0:
            found.append("negative_score")
        if query("SELECT COUNT(*) FROM (SELECT target FROM unlocks WHERE user_id = 1 "
                 "GROUP BY target HAVING COUNT(*) > 1) AS d"):
            found.append("hint_unlocked_twice")
    else:
        raise KeyError(rule)
    return found


SCHEMA: dict[str, str] = {
    'oscar': '''CREATE TABLE auth_user (
    id SERIAL PRIMARY KEY,
    password VARCHAR(128) NOT NULL,
    last_login TIMESTAMPTZ,
    is_superuser BOOLEAN NOT NULL,
    username VARCHAR(150) NOT NULL,
    first_name VARCHAR(150) NOT NULL,
    last_name VARCHAR(150) NOT NULL,
    email VARCHAR(254) NOT NULL,
    is_staff BOOLEAN NOT NULL,
    is_active BOOLEAN NOT NULL,
    date_joined TIMESTAMPTZ NOT NULL,
    UNIQUE (username)
);

CREATE TABLE voucher_voucher (
    id SERIAL PRIMARY KEY,
    name VARCHAR(128) NOT NULL,
    code VARCHAR(128) NOT NULL,
    usage VARCHAR(128) NOT NULL,
    start_datetime TIMESTAMPTZ NOT NULL,
    end_datetime TIMESTAMPTZ NOT NULL,
    num_basket_additions INTEGER NOT NULL,
    num_orders INTEGER NOT NULL,
    total_discount NUMERIC(12, 2) NOT NULL,
    date_created TIMESTAMPTZ NOT NULL,
    voucher_set_id INTEGER,
    UNIQUE (code),
    UNIQUE (name),
    FOREIGN KEY (voucher_set_id) REFERENCES voucher_voucherset(id)
);

CREATE TABLE order_order (
    id SERIAL PRIMARY KEY,
    number VARCHAR(128) NOT NULL,
    currency VARCHAR(12) NOT NULL,
    total_incl_tax NUMERIC(12, 2) NOT NULL,
    total_excl_tax NUMERIC(12, 2) NOT NULL,
    shipping_incl_tax NUMERIC(12, 2) NOT NULL,
    shipping_excl_tax NUMERIC(12, 2) NOT NULL,
    shipping_method VARCHAR(128) NOT NULL,
    shipping_code VARCHAR(128) NOT NULL,
    status VARCHAR(100) NOT NULL,
    guest_email VARCHAR(254) NOT NULL,
    date_placed TIMESTAMPTZ NOT NULL,
    basket_id INTEGER,
    billing_address_id INTEGER,
    shipping_address_id INTEGER,
    site_id INTEGER,
    user_id INTEGER,
    shipping_tax_code VARCHAR(64),
    analytics_tracked BOOLEAN NOT NULL,
    UNIQUE (number),
    FOREIGN KEY (basket_id) REFERENCES basket_basket(id),
    FOREIGN KEY (billing_address_id) REFERENCES order_billingaddress(id),
    FOREIGN KEY (shipping_address_id) REFERENCES order_shippingaddress(id),
    FOREIGN KEY (site_id) REFERENCES django_site(id),
    FOREIGN KEY (user_id) REFERENCES auth_user(id)
);

CREATE TABLE voucher_voucherapplication (
    id SERIAL PRIMARY KEY,
    date_created TIMESTAMPTZ NOT NULL,
    order_id INTEGER NOT NULL,
    user_id INTEGER,
    voucher_id INTEGER NOT NULL,
    FOREIGN KEY (voucher_id) REFERENCES voucher_voucher(id),
    FOREIGN KEY (order_id) REFERENCES order_order(id),
    FOREIGN KEY (user_id) REFERENCES auth_user(id)
);''',
    'ctfd': '''CREATE TABLE users (
    id SERIAL PRIMARY KEY,
    oauth_id INTEGER,
    name VARCHAR(128),
    password VARCHAR(128),
    email VARCHAR(128),
    type VARCHAR(80),
    secret VARCHAR(128),
    website VARCHAR(128),
    affiliation VARCHAR(128),
    country VARCHAR(32),
    hidden BOOLEAN,
    banned BOOLEAN,
    verified BOOLEAN,
    team_id INTEGER,
    created TIMESTAMP,
    language VARCHAR(32),
    bracket_id INTEGER,
    change_password BOOLEAN,
    UNIQUE (email),
    UNIQUE (id, oauth_id),
    UNIQUE (oauth_id),
    FOREIGN KEY (bracket_id) REFERENCES brackets(id) ON DELETE SET NULL,
    FOREIGN KEY (team_id) REFERENCES teams(id)
);

CREATE TABLE challenges (
    id SERIAL PRIMARY KEY,
    name VARCHAR(80),
    description TEXT,
    max_attempts INTEGER,
    value INTEGER,
    category VARCHAR(80),
    type VARCHAR(80),
    state VARCHAR(80) NOT NULL,
    requirements JSON,
    connection_info TEXT,
    next_id INTEGER,
    attribution TEXT,
    logic VARCHAR(80) NOT NULL,
    initial INTEGER,
    minimum INTEGER,
    decay INTEGER,
    function VARCHAR(32),
    position INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (next_id) REFERENCES challenges(id) ON DELETE SET NULL
);

CREATE TABLE flags (
    id SERIAL PRIMARY KEY,
    challenge_id INTEGER,
    type VARCHAR(80),
    content TEXT,
    data TEXT,
    FOREIGN KEY (challenge_id) REFERENCES challenges(id) ON DELETE CASCADE
);

CREATE TABLE hints (
    id SERIAL PRIMARY KEY,
    type VARCHAR(80),
    challenge_id INTEGER,
    content TEXT,
    cost INTEGER,
    requirements JSON,
    title VARCHAR(80),
    FOREIGN KEY (challenge_id) REFERENCES challenges(id) ON DELETE CASCADE
);

CREATE TABLE submissions (
    id SERIAL PRIMARY KEY,
    challenge_id INTEGER,
    user_id INTEGER,
    team_id INTEGER,
    ip VARCHAR(46),
    provided TEXT,
    type VARCHAR(32),
    date TIMESTAMP,
    FOREIGN KEY (challenge_id) REFERENCES challenges(id) ON DELETE CASCADE,
    FOREIGN KEY (team_id) REFERENCES teams(id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE solves (
    id INTEGER PRIMARY KEY,
    challenge_id INTEGER,
    user_id INTEGER,
    team_id INTEGER,
    UNIQUE (challenge_id, team_id),
    UNIQUE (challenge_id, user_id),
    FOREIGN KEY (challenge_id) REFERENCES challenges(id) ON DELETE CASCADE,
    FOREIGN KEY (id) REFERENCES submissions(id) ON DELETE CASCADE,
    FOREIGN KEY (team_id) REFERENCES teams(id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE awards (
    id SERIAL PRIMARY KEY,
    user_id INTEGER,
    team_id INTEGER,
    name VARCHAR(80),
    description TEXT,
    date TIMESTAMP,
    value INTEGER,
    category VARCHAR(80),
    icon TEXT,
    requirements JSON,
    type VARCHAR(80) DEFAULT 'standard',
    FOREIGN KEY (team_id) REFERENCES teams(id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE unlocks (
    id SERIAL PRIMARY KEY,
    user_id INTEGER,
    team_id INTEGER,
    target INTEGER,
    date TIMESTAMP,
    type VARCHAR(32),
    FOREIGN KEY (team_id) REFERENCES teams(id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);''',
}


REFERENCES: dict[tuple[str, str], str] = {
    ("oscar_voucher", "correct"): '''
from django.contrib.auth.models import AnonymousUser
from django.db import transaction
from oscar.core.loading import get_model

Order = get_model("order", "Order")
Voucher = get_model("voucher", "Voucher")
VoucherApplication = get_model("voucher", "VoucherApplication")


def apply_voucher(order_number, code):
    with transaction.atomic():
        voucher = Voucher.objects.select_for_update().filter(code=code).first()
        if voucher is None:
            return {"ok": False, "error": "unknown_voucher"}
        if not voucher.is_active():
            return {"ok": False, "error": "inactive"}
        order = Order.objects.select_related("user").get(number=order_number)
        user = order.user if order.user is not None else AnonymousUser()
        available, _ = voucher.is_available_to_user(user=user)
        if not available:
            return {"ok": False, "error": "unavailable"}
        VoucherApplication.objects.create(voucher=voucher, order=order, user=order.user)
        voucher.num_orders += 1
        voucher.save(update_fields=["num_orders"])
    return {"ok": True}
''',
    ("oscar_voucher", "buggy"): '''
from django.contrib.auth.models import AnonymousUser
from oscar.core.loading import get_model

Order = get_model("order", "Order")
Voucher = get_model("voucher", "Voucher")


def apply_voucher(order_number, code):
    voucher = Voucher.objects.filter(code=code).first()
    if voucher is None:
        return {"ok": False, "error": "unknown_voucher"}
    if not voucher.is_active():
        return {"ok": False, "error": "inactive"}
    order = Order.objects.select_related("user").get(number=order_number)
    user = order.user if order.user is not None else AnonymousUser()
    available, _ = voucher.is_available_to_user(user=user)
    if not available:
        return {"ok": False, "error": "unavailable"}
    voucher.record_usage(order, user)
    return {"ok": True}
''',
    ("ctfd_attempts", "correct"): '''
from CTFd.models import Challenges, Fails, Flags, Solves, Users, db


def attempt(user_id, challenge_id, provided):
    # Lock the user's row first: one submission per user is processed at a time.
    db.session.query(Users.id).filter(Users.id == user_id).with_for_update().first()
    challenge = Challenges.query.filter_by(id=challenge_id).first()
    if Solves.query.filter_by(user_id=user_id, challenge_id=challenge_id).first():
        db.session.rollback()
        return {"ok": False, "error": "already_solved"}
    if challenge.max_attempts and challenge.max_attempts > 0:
        fails = Fails.query.filter_by(user_id=user_id, challenge_id=challenge_id).count()
        if fails >= challenge.max_attempts:
            db.session.rollback()
            return {"ok": False, "error": "max_attempts"}
    flags = Flags.query.filter_by(challenge_id=challenge_id, type="static").all()
    if any(flag.content == provided for flag in flags):
        db.session.add(Solves(user_id=user_id, challenge_id=challenge_id, provided=provided))
        status = "correct"
    else:
        db.session.add(Fails(user_id=user_id, challenge_id=challenge_id, provided=provided))
        status = "incorrect"
    db.session.commit()
    return {"ok": True, "status": status}
''',
    ("ctfd_attempts", "buggy"): '''
from CTFd.models import Challenges, Fails, Flags, Solves, db


def attempt(user_id, challenge_id, provided):
    challenge = Challenges.query.filter_by(id=challenge_id).first()
    if Solves.query.filter_by(user_id=user_id, challenge_id=challenge_id).first():
        return {"ok": False, "error": "already_solved"}
    if challenge.max_attempts and challenge.max_attempts > 0:
        fails = Fails.query.filter_by(user_id=user_id, challenge_id=challenge_id).count()
        if fails >= challenge.max_attempts:
            return {"ok": False, "error": "max_attempts"}
    flags = Flags.query.filter_by(challenge_id=challenge_id, type="static").all()
    if any(flag.content == provided for flag in flags):
        db.session.add(Solves(user_id=user_id, challenge_id=challenge_id, provided=provided))
        status = "correct"
    else:
        db.session.add(Fails(user_id=user_id, challenge_id=challenge_id, provided=provided))
        status = "incorrect"
    db.session.commit()
    return {"ok": True, "status": status}
''',
    ("ctfd_hint_unlock", "correct"): '''
from sqlalchemy import func

from CTFd.models import Awards, Challenges, HintUnlocks, Hints, Solves, Unlocks, Users, db


def unlock_hint(user_id, hint_id):
    # Lock the user's row first: one score-spending request per user at a time.
    db.session.query(Users.id).filter(Users.id == user_id).with_for_update().first()
    hint = Hints.query.filter_by(id=hint_id).first()
    if Unlocks.query.filter_by(user_id=user_id, target=hint_id, type="hints").first():
        db.session.rollback()
        return {"ok": False, "error": "already_unlocked"}
    solved = (db.session.query(func.coalesce(func.sum(Challenges.value), 0))
              .join(Solves, Solves.challenge_id == Challenges.id)
              .filter(Solves.user_id == user_id).scalar())
    awarded = (db.session.query(func.coalesce(func.sum(Awards.value), 0))
               .filter(Awards.user_id == user_id).scalar())
    if hint.cost > int(solved) + int(awarded):
        db.session.rollback()
        return {"ok": False, "error": "insufficient_score"}
    unlock = HintUnlocks(user_id=user_id, target=hint_id)
    db.session.add(unlock)
    db.session.add(Awards(user_id=user_id, name=hint.name, description=hint.description,
                          value=-hint.cost, category=hint.category))
    db.session.commit()
    return {"ok": True, "unlock_id": unlock.id}
''',
    ("ctfd_hint_unlock", "buggy"): '''
from CTFd.models import Awards, HintUnlocks, Hints, Unlocks, Users, db


def unlock_hint(user_id, hint_id):
    user = Users.query.filter_by(id=user_id).first()
    hint = Hints.query.filter_by(id=hint_id).first()
    if Unlocks.query.filter_by(user_id=user_id, target=hint_id, type="hints").first():
        return {"ok": False, "error": "already_unlocked"}
    if hint.cost > user.get_score(admin=True):
        return {"ok": False, "error": "insufficient_score"}
    unlock = HintUnlocks(user_id=user_id, target=hint_id)
    db.session.add(unlock)
    db.session.add(Awards(user_id=user_id, name=hint.name, description=hint.description,
                          value=-hint.cost, category=hint.category))
    db.session.commit()
    return {"ok": True, "unlock_id": unlock.id}
''',
}


RULE_TEXT = {
    "oscar_voucher_single": "a single-use voucher is used by at most one order",
    "oscar_voucher_customer": "a once-per-customer voucher is used at most once by each customer",
    "ctfd_attempts": (
        "a user records at most `max_attempts` failed submissions for a challenge"
    ),
    "ctfd_hint_unlock": (
        "unlocking hints never takes a user's score below zero, and a user unlocks a hint "
        "at most once"
    ),
}


def call_text(handler: str, kwargs: dict[str, Any]) -> str:
    arguments = ", ".join(f"{k}={v!r}" for k, v in kwargs.items())
    return f"{HANDLERS[handler]['function']}({arguments})"


def passed(check: dict[str, Any] | None) -> bool:
    if check is None or not check["functional"]["passed"]:
        return False
    return not any(c["violating_trials"] or c["exceptions"] or c["hung"]
                   for c in (check.get("concurrent") or {}).values())


def feedback_text(handler: str, check: dict[str, Any] | None) -> str | None:
    import c1_feedback

    function = HANDLERS[handler]["function"]
    if check is None:
        return (f"Your answer did not contain a ```python code block that defines `{function}`. "
                + c1_feedback.CLOSING)
    f = check["functional"]
    if not f["passed"]:
        if f.get("setup_error"):
            return f"Importing your module failed: {f['setup_error']}\n\n{c1_feedback.CLOSING}"
        if f.get("hung"):
            return (f"The test suite timed out while calling `{function}`.\n\n"
                    f"{c1_feedback.CLOSING}")
        spec = FUNCTIONAL[handler]
        lines = ["The application's tests called the function in this order on a fresh database:"]
        for k, (outcome, (kwargs, expected)) in enumerate(zip(f["outcomes"], spec["calls"]), 1):
            verdict = "ok" if f["calls_ok"][k - 1] else "FAILED"
            lines.append(f"{k}. `{call_text(handler, kwargs)}`: expected ok={expected}, "
                         f"{c1_feedback.outcome_text(outcome)} [{verdict}]")
        for (sql, expected), good in zip(spec["state"], f["state_ok"]):
            if not good:
                lines.append(f"After the calls, `{sql}` should return {expected} but did not.")
        return "\n".join(lines) + "\n\n" + c1_feedback.CLOSING
    if passed(check):
        return None
    lines: list[str] = []
    for rule, c in check["concurrent"].items():
        if not (c["violating_trials"] or c["exceptions"] or c["hung"]):
            continue
        lines.append(
            "The sequential tests pass. A concurrency test then had "
            f"{c['workers']} clients call `{function}` at the same time, each making its usual "
            "requests, on a fresh database each run.")
        if c["violating_trials"]:
            lines.append(f"In {c['violating_trials']} of {c['trials']} runs the database ended in "
                         f"a state that breaks the rule: {RULE_TEXT[rule]}.")
        if c["exceptions"]:
            count = sum(c["exceptions"].values())
            key, message = next(iter(c["exception_messages"].items()))
            lines.append(f"{count} requests raised an exception to the caller, for example "
                         f"{key.split(':')[0]}: {message}")
        if c["hung"]:
            lines.append("A run did not finish within the time limit.")
        break
    return "\n".join(lines) + "\n\n" + c1_feedback.CLOSING


ORACLE_TEST = '''
import c2_apps


def test_concurrency():
    for rule in c2_apps.HANDLERS[{handler!r}]["rules"]:
        fresh_database()
        for statement in c2_apps.seed(rule, "concurrent", "postgresql"):
            db_execute(statement)
        for table in c2_apps.TABLES[c2_apps.app_of(rule)]:
            db_execute(
                f"SELECT setval(pg_get_serial_sequence('{{table}}', 'id'), "
                f"COALESCE((SELECT MAX(id) FROM {{table}}), 0) + 1, false)"
            )
        run_concurrently([c2_apps.request_plan(rule, w)[0] for w in range(8)])
        found = c2_apps.invariant(rule, lambda sql: db_execute(sql)[0][0])
        assert not found, found
'''
