from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
import hashlib
import math
import os
import re
import secrets
from threading import BoundedSemaphore
from typing import Generator
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pwdlib import PasswordHash
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, case, create_engine, delete, event, func, inspect, or_, select, text, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker

BASE_DIR = Path(__file__).resolve().parent
DATABASE_PATH = Path(os.environ.get("FOODSAVER_DB_PATH", str(BASE_DIR / "foodsaver.db"))).expanduser().resolve()
DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
DATABASE_URL = f"sqlite:///{DATABASE_PATH.as_posix()}"
CITY_NAMES = (
    "Алматы",
    "Астана",
    "Шымкент",
    "Караганда",
    "Актобе",
    "Тараз",
    "Павлодар",
    "Усть-Каменогорск",
    "Семей",
    "Атырау",
)
DEAL_CATEGORIES = ("bakery", "coffee", "ready_food", "desserts", "shops")
# Approximate city-level demo pins only; sources and caveats are documented in CITY_COORDINATES.md.
CITY_DEMO_CENTRES: dict[str, tuple[float, float]] = {
    "Алматы": (43.2363924, 76.9457275),
    "Астана": (51.1282804, 71.4304708),
    "Шымкент": (42.3146962, 69.5883282),
    "Караганда": (49.80187, 73.10211),
    "Актобе": (50.2836938, 57.2298129),
    "Тараз": (42.89799, 71.37334),
    "Павлодар": (52.2857573, 76.9455035),
    "Усть-Каменогорск": (49.9484438, 82.6274791),
    "Семей": (50.4067378, 80.2502824),
    "Атырау": (47.1067183, 51.9138976),
}
COMMISSION_RATE = 0.20
SESSION_IDLE_SECONDS = 30 * 60
SESSION_MAX_SECONDS = 8 * 60 * 60
QALDYQ_ENV = os.environ.get("QALDYQ_ENV", "development").strip().lower()
COOKIE_SECURE = os.environ.get("QALDYQ_COOKIE_SECURE", "true" if QALDYQ_ENV == "production" else "false").strip().lower() in {"1", "true", "yes", "on"}
if QALDYQ_ENV == "production" and not COOKIE_SECURE:
    raise RuntimeError("QALDYQ_COOKIE_SECURE must be enabled in production")
SESSION_COOKIE_NAME = "__Host-qaldyq_session" if COOKIE_SECURE else "qaldyq_session"
PASSWORD_HASH = PasswordHash.recommended()
PASSWORD_HASH_SEMAPHORE = BoundedSemaphore(4)
DUMMY_PASSWORD_HASH = PASSWORD_HASH.hash("qaldyq-invalid-account-dummy-password")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False, "timeout": 20},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@event.listens_for(engine, "connect")
def enable_sqlite_foreign_keys(connection, _record) -> None:
    cursor = connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


class Base(DeclarativeBase):
    pass


class CafeAccount(Base):
    __tablename__ = "cafe_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_name: Mapped[str] = mapped_column(String(120), nullable=False)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True, nullable=False)
    city: Mapped[str] = mapped_column(String(32), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    deals: Mapped[list[Deal]] = relationship(back_populates="owner")
    sessions: Mapped[list[CafeSession]] = relationship(back_populates="cafe", cascade="all, delete-orphan")


class CafeSession(Base):
    __tablename__ = "cafe_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cafe_id: Mapped[int] = mapped_column(ForeignKey("cafe_accounts.id", ondelete="CASCADE"), index=True, nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    csrf_token: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    cafe: Mapped[CafeAccount] = relationship(back_populates="sessions")


class RateLimitBucket(Base):
    __tablename__ = "rate_limit_buckets"

    key_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    window_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class Deal(Base):
    __tablename__ = "deals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("cafe_accounts.id", ondelete="SET NULL"), index=True, nullable=True)
    owner: Mapped[CafeAccount | None] = relationship(back_populates="deals")
    cafe_name: Mapped[str] = mapped_column(String(120), nullable=False)
    title: Mapped[str] = mapped_column(String(140), nullable=False)
    description: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    category: Mapped[str] = mapped_column(String(20), nullable=False, default="ready_food", index=True)
    photo_url: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    old_price: Mapped[float] = mapped_column(Float, nullable=False)
    new_price: Mapped[float] = mapped_column(Float, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    listed_quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    mystery_box: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    flash_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    pickup_time: Mapped[str] = mapped_column(String(100), nullable=False)
    pickup_start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    pickup_end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True, nullable=True)
    city: Mapped[str] = mapped_column(String(32), nullable=False)
    address: Mapped[str] = mapped_column(String(250), default="", nullable=False)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    weight_kg: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    orders: Mapped[list[Order]] = relationship(back_populates="deal")


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    deal_id: Mapped[int] = mapped_column(ForeignKey("deals.id"), nullable=False, index=True)
    customer_name: Mapped[str] = mapped_column(String(100), nullable=False)
    phone: Mapped[str] = mapped_column(String(32), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    unit_price: Mapped[float] = mapped_column(Float, nullable=False)
    weight_kg: Mapped[float] = mapped_column(Float, nullable=False)
    flash_sale_applied: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    delivery_method: Mapped[str] = mapped_column(String(16), default="pickup", nullable=False)
    delivery_address: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    deal: Mapped[Deal] = relationship(back_populates="orders")


class DealCreate(BaseModel):
    cafe_name: str = Field(min_length=2, max_length=120)
    title: str = Field(min_length=2, max_length=140)
    description: str = Field(default="", max_length=500)
    category: str = "ready_food"
    photo_url: str = Field(default="", max_length=500)
    old_price: float = Field(gt=0, le=1_000_000)
    new_price: float = Field(gt=0, le=1_000_000)
    quantity: int = Field(ge=1, le=5000)
    pickup_time: str = Field(min_length=3, max_length=100)
    city: str
    weight_kg: float = Field(gt=0, le=50)
    address: str = Field(default="", max_length=250)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    mystery_box: bool = False
    flash_enabled: bool = True
    pickup_start_at: datetime | None = None
    pickup_end_at: datetime | None = None

    @field_validator("cafe_name", "title", "description", "pickup_time")
    @classmethod
    def trim_text(cls, value: str, info) -> str:
        value = value.strip()
        if info.field_name in {"cafe_name", "title", "pickup_time"} and not value:
            raise ValueError("Поле не может быть пустым")
        return value

    @field_validator("city")
    @classmethod
    def supported_city(cls, value: str) -> str:
        value = value.strip()
        if value not in CITY_NAMES:
            raise ValueError("Выберите один из 10 городов QaldyQ")
        return value

    @field_validator("category")
    @classmethod
    def supported_category(cls, value: str) -> str:
        value = value.strip()
        if value not in DEAL_CATEGORIES:
            raise ValueError("Выберите категорию предложения")
        return value

    @field_validator("photo_url")
    @classmethod
    def safe_photo_url(cls, value: str) -> str:
        value = value.strip()
        if not value:
            return ""
        if value.startswith("/static/assets/"):
            if ".." in value or "\\" in value:
                raise ValueError("Укажите безопасный путь к фото QaldyQ")
            return value
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("Для фото используйте HTTPS-ссылку или каталог /static/assets/")
        return value

    @field_validator("address")
    @classmethod
    def trim_address(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def validate_coordinates(self):
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("Укажите широту и долготу вместе")
        if self.latitude is not None and not self.address:
            raise ValueError("Укажите адрес заведения для точки на карте")
        if (self.pickup_start_at is None) != (self.pickup_end_at is None):
            raise ValueError("Укажите начало и конец окна самовывоза вместе")
        if self.pickup_start_at is not None and to_utc_naive(self.pickup_start_at) >= to_utc_naive(self.pickup_end_at):
            raise ValueError("Конец самовывоза должен быть позже начала")
        return self

    @field_validator("new_price")
    @classmethod
    def discount_required(cls, value: float, info):
        old_price = info.data.get("old_price")
        if old_price is not None and value >= old_price:
            raise ValueError("Цена со скидкой должна быть ниже обычной")
        return value


class CafeRegister(BaseModel):
    business_name: str = Field(min_length=2, max_length=120)
    email: str = Field(min_length=6, max_length=254)
    password: str = Field(min_length=12, max_length=128)
    city: str

    @field_validator("business_name")
    @classmethod
    def trim_business_name(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 2:
            raise ValueError("Укажите название кафе минимум из 2 символов")
        return value

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        value = value.strip().lower()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
            raise ValueError("Введите корректный email")
        return value

    @field_validator("city")
    @classmethod
    def supported_city(cls, value: str) -> str:
        value = value.strip()
        if value not in CITY_NAMES:
            raise ValueError("Выберите один из 10 городов QaldyQ")
        return value


class CafeLogin(BaseModel):
    email: str = Field(min_length=6, max_length=254)
    password: str = Field(min_length=1, max_length=128)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.strip().lower()


class OrderCreate(BaseModel):
    deal_id: int = Field(gt=0)
    customer_name: str = Field(min_length=2, max_length=100)
    phone: str = Field(min_length=7, max_length=32)
    delivery_method: str = "pickup"
    delivery_address: str | None = Field(default=None, max_length=500)

    @field_validator("customer_name")
    @classmethod
    def trim_name(cls, value: str) -> str:
        return value.strip()

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, value: str) -> str:
        value = value.strip()
        digits = re.sub(r"\D", "", value)
        if not 10 <= len(digits) <= 15:
            raise ValueError("Укажите корректный номер телефона")
        return value


    @field_validator("delivery_method")
    @classmethod
    def supported_delivery_method(cls, value: str) -> str:
        value = value.strip().lower()
        if value not in {"pickup", "courier"}:
            raise ValueError("Выберите самовывоз или курьера")
        return value

    @field_validator("delivery_address")
    @classmethod
    def trim_delivery_address(cls, value: str | None) -> str | None:
        return value.strip() if value and value.strip() else None

    @model_validator(mode="after")
    def require_courier_address(self):
        if self.delivery_method == "courier" and not self.delivery_address:
            raise ValueError("Для запроса курьера укажите адрес доставки")
        if self.delivery_method == "pickup":
            self.delivery_address = None
        return self


class BatchOrderLine(BaseModel):
    deal_id: int = Field(gt=0)
    quantity: int = Field(ge=1, le=100)


class BatchOrderCreate(BaseModel):
    customer_name: str = Field(min_length=2, max_length=100)
    phone: str = Field(min_length=7, max_length=32)
    items: list[BatchOrderLine] = Field(min_length=1, max_length=20)
    delivery_method: str = "pickup"
    delivery_address: str | None = Field(default=None, max_length=500)

    @field_validator("customer_name")
    @classmethod
    def trim_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Укажите имя")
        return value

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, value: str) -> str:
        value = value.strip()
        digits = re.sub(r"\D", "", value)
        if not 10 <= len(digits) <= 15:
            raise ValueError("Укажите корректный номер телефона")
        return value


    @field_validator("delivery_method")
    @classmethod
    def supported_delivery_method(cls, value: str) -> str:
        value = value.strip().lower()
        if value not in {"pickup", "courier"}:
            raise ValueError("Выберите самовывоз или курьера")
        return value

    @field_validator("delivery_address")
    @classmethod
    def trim_delivery_address(cls, value: str | None) -> str | None:
        return value.strip() if value and value.strip() else None

    @model_validator(mode="after")
    def require_courier_address(self):
        if self.delivery_method == "courier" and not self.delivery_address:
            raise ValueError("Для запроса курьера укажите адрес доставки")
        if self.delivery_method == "pickup":
            self.delivery_address = None
        return self


class DealOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    cafe_name: str
    title: str
    description: str
    category: str
    photo_url: str
    old_price: float
    new_price: float
    quantity: int
    pickup_time: str
    city: str
    address: str
    latitude: float | None
    longitude: float | None
    mystery_box: bool
    flash_enabled: bool
    pickup_start_at: datetime | None
    pickup_end_at: datetime | None
    effective_price: float
    discount_percent: int
    flash_sale_active: bool
    flash_discount_percent: int
    minutes_to_pickup_end: int | None
    weight_kg: float


class OrderOut(BaseModel):
    id: int
    deal_id: int
    title: str
    cafe_name: str
    customer_name: str
    phone: str
    quantity: int
    unit_price: float
    weight_kg: float
    flash_sale_applied: bool
    delivery_method: str
    delivery_address: str | None
    created_at: datetime


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


KAZAKHSTAN_TZ = timezone(timedelta(hours=5))


def utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def to_utc_naive(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def pickup_window_datetimes(pickup_text: str) -> tuple[datetime | None, datetime | None]:
    times = re.findall(r"(?<!\d)([01]?\d|2[0-3]):([0-5]\d)", pickup_text)
    if len(times) < 2:
        return None, None
    local_date = datetime.now(KAZAKHSTAN_TZ).date()
    values = [datetime.combine(local_date, datetime.min.time().replace(hour=int(hour), minute=int(minute)), KAZAKHSTAN_TZ).astimezone(timezone.utc).replace(tzinfo=None) for hour, minute in times[:2]]
    return values[0], values[1]


def flash_price_details(deal: Deal, now: datetime | None = None) -> dict:
    current = now or utc_now_naive()
    end_at = to_utc_naive(deal.pickup_end_at)
    minutes_left = max(0, math.ceil((end_at - current).total_seconds() / 60)) if end_at else None
    flash_active = bool(deal.flash_enabled and end_at and timedelta(0) < end_at - current <= timedelta(minutes=45))
    effective_price = min(float(deal.new_price), round(float(deal.old_price) * 0.30, 2)) if flash_active else float(deal.new_price)
    discount_percent = max(0, round((1 - effective_price / float(deal.old_price)) * 100)) if deal.old_price else 0
    return {
        "effective_price": effective_price,
        "discount_percent": discount_percent,
        "flash_sale_active": flash_active,
        "flash_discount_percent": discount_percent if flash_active else 0,
        "minutes_to_pickup_end": minutes_left,
    }


def seed_demo_deals(db: Session) -> None:
    """Idempotently add city-hub demo offers without deleting user data."""
    partners = [
        ("Алматы", "Мадлен"),
        ("Алматы", "Qara Coffee / JumpinGoat"),
        ("Алматы", "Zebra Coffee"),
        ("Алматы", "Paul"),
        ("Астана", "Coffee Boom"),
        ("Астана", "Тәп-Тәтті"),
        ("Астана", "Global Coffee"),
        ("Астана", "Rafe Coffee & Food"),
        ("Шымкент", "Мадлен"),
        ("Шымкент", "CoffeeOut"),
        ("Шымкент", "My Cafe"),
        ("Караганда", "Мастер Кофе"),
        ("Караганда", "Global Coffee"),
        ("Караганда", "Бублик"),
        ("Актобе", "Zebra Coffee"),
        ("Актобе", "Coffee Like"),
        ("Тараз", "Тәп-Тәтті"),
        ("Тараз", "Кондитерский дом Тараз"),
        ("Павлодар", "Coffee House"),
        ("Павлодар", "Пекарня Павлодар"),
        ("Усть-Каменогорск", "Global Coffee"),
        ("Усть-Каменогорск", "Кофейня Усть-Каменогорск"),
        ("Семей", "Пекарня Семей"),
        ("Семей", "Кофейня Семей"),
        ("Атырау", "Coffee+"),
        ("Атырау", "Кофейня Атырау"),
    ]
    titles = ("Набор выпечки дня", "Вечерний кофейный бокс", "Десертный набор", "Набор к чаю", "Ассорти витрины")
    demo_categories = {"Набор выпечки дня": "bakery", "Вечерний кофейный бокс": "coffee", "Десертный набор": "desserts", "Набор к чаю": "ready_food", "Ассорти витрины": "shops"}
    pickup_windows = ("Сегодня, 18:00–19:00", "Сегодня, 18:30–19:30", "Сегодня, 19:00–20:00", "Сегодня, 19:30–20:30", "Сегодня, 20:00–21:00")
    demo_description = (
        "ДЕМО для хакатона. Партнёрство не подтверждено; "
        "точка в центре города — ориентир, не адрес заведения."
    )

    existing = {
        (deal.city, deal.cafe_name, deal.title)
        for deal in db.scalars(select(Deal).where(Deal.cafe_name.like("ДЕМО · %"))).all()
    }
    new_deals = []
    for index, (city, partner_name) in enumerate(partners):
        cafe_name = f"ДЕМО · {partner_name}"
        title = titles[index % len(titles)]
        key = (city, cafe_name, title)
        if key in existing:
            continue
        old_price = 3200 + (index % 5) * 400
        quantity = 1 if index % 3 == 0 else 2
        pickup_time = pickup_windows[index % len(pickup_windows)]
        pickup_start_at, pickup_end_at = pickup_window_datetimes(pickup_time)
        latitude, longitude = CITY_DEMO_CENTRES[city]
        new_deals.append(
            Deal(
                cafe_name=cafe_name,
                title=title,
                description=demo_description,
                category=demo_categories[title],
                old_price=old_price,
                new_price=old_price // 2,
                quantity=quantity,
                listed_quantity=quantity,
                mystery_box=False,
                flash_enabled=True,
                pickup_time=pickup_time,
                pickup_start_at=pickup_start_at,
                pickup_end_at=pickup_end_at,
                city=city,
                address=f"ДЕМО · центр города {city} (ориентир, не адрес заведения)",
                latitude=latitude,
                longitude=longitude,
                weight_kg=round(0.35 + (index % 5) * 0.1, 2),
            )
        )
        existing.add(key)
    if new_deals:
        db.add_all(new_deals)
        db.commit()
    now = utc_now_naive()
    demo_deals = db.scalars(select(Deal).where(Deal.cafe_name.like("ДЕМО · %"))).all()
    changed = False
    for deal in demo_deals:
        expected_category = demo_categories.get(deal.title, "ready_food")
        if deal.category != expected_category:
            deal.category = expected_category
            changed = True
        if not deal.listed_quantity:
            deal.listed_quantity = max(0, deal.quantity)
            changed = True
        if deal.pickup_end_at is None or to_utc_naive(deal.pickup_end_at) <= now:
            start_at, end_at = pickup_window_datetimes(deal.pickup_time)
            if start_at and end_at:
                deal.pickup_start_at, deal.pickup_end_at = start_at, end_at
                changed = True
    if changed:
        db.commit()


def deal_payload(deal: Deal, now: datetime | None = None) -> dict:
    return {
        "id": deal.id,
        "cafe_name": deal.cafe_name,
        "title": deal.title,
        "description": deal.description,
        "category": deal.category,
        "photo_url": deal.photo_url or "",
        "old_price": deal.old_price,
        "new_price": deal.new_price,
        "quantity": deal.quantity,
        "pickup_time": deal.pickup_time,
        "city": deal.city,
        "address": deal.address or "",
        "latitude": deal.latitude,
        "longitude": deal.longitude,
        "mystery_box": deal.mystery_box,
        "flash_enabled": deal.flash_enabled,
        "pickup_start_at": to_utc_naive(deal.pickup_start_at),
        "pickup_end_at": to_utc_naive(deal.pickup_end_at),
        **flash_price_details(deal, now),
        "weight_kg": deal.weight_kg,
    }


def order_payload(order: Order) -> dict:
    return {
        "id": order.id,
        "deal_id": order.deal_id,
        "title": order.deal.title,
        "cafe_name": order.deal.cafe_name,
        "customer_name": order.customer_name,
        "phone": order.phone,
        "quantity": order.quantity,
        "unit_price": order.unit_price,
        "weight_kg": order.weight_kg,
        "flash_sale_applied": order.flash_sale_applied,
        "delivery_method": order.delivery_method,
        "delivery_address": order.delivery_address,
        "created_at": order.created_at,
    }


def migrate_schema() -> None:
    """Add location, ownership, mystery and flash fields without deleting legacy data."""
    deal_columns = {column["name"] for column in inspect(engine).get_columns("deals")}
    order_columns = {column["name"] for column in inspect(engine).get_columns("orders")}
    with engine.begin() as connection:
        if "address" not in deal_columns:
            connection.execute(text("ALTER TABLE deals ADD COLUMN address VARCHAR(250) NOT NULL DEFAULT ''"))
        if "latitude" not in deal_columns:
            connection.execute(text("ALTER TABLE deals ADD COLUMN latitude FLOAT"))
        if "longitude" not in deal_columns:
            connection.execute(text("ALTER TABLE deals ADD COLUMN longitude FLOAT"))
        if "owner_id" not in deal_columns:
            connection.execute(text("ALTER TABLE deals ADD COLUMN owner_id INTEGER REFERENCES cafe_accounts(id) ON DELETE SET NULL"))
        if "listed_quantity" not in deal_columns:
            connection.execute(text("ALTER TABLE deals ADD COLUMN listed_quantity INTEGER NOT NULL DEFAULT 0"))
        if "mystery_box" not in deal_columns:
            connection.execute(text("ALTER TABLE deals ADD COLUMN mystery_box BOOLEAN NOT NULL DEFAULT 0"))
        if "flash_enabled" not in deal_columns:
            connection.execute(text("ALTER TABLE deals ADD COLUMN flash_enabled BOOLEAN NOT NULL DEFAULT 1"))
        if "pickup_start_at" not in deal_columns:
            connection.execute(text("ALTER TABLE deals ADD COLUMN pickup_start_at DATETIME"))
        if "pickup_end_at" not in deal_columns:
            connection.execute(text("ALTER TABLE deals ADD COLUMN pickup_end_at DATETIME"))
        if "category" not in deal_columns:
            connection.execute(text("ALTER TABLE deals ADD COLUMN category VARCHAR(20) NOT NULL DEFAULT 'ready_food'"))
        if "photo_url" not in deal_columns:
            connection.execute(text("ALTER TABLE deals ADD COLUMN photo_url VARCHAR(500) NOT NULL DEFAULT ''"))
        if "flash_sale_applied" not in order_columns:
            connection.execute(text("ALTER TABLE orders ADD COLUMN flash_sale_applied BOOLEAN NOT NULL DEFAULT 0"))
        if "delivery_method" not in order_columns:
            connection.execute(text("ALTER TABLE orders ADD COLUMN delivery_method VARCHAR(16) NOT NULL DEFAULT 'pickup'"))
        if "delivery_address" not in order_columns:
            connection.execute(text("ALTER TABLE orders ADD COLUMN delivery_address VARCHAR(500)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_deals_owner_id ON deals(owner_id)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_deals_pickup_end_at ON deals(pickup_end_at)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_deals_category ON deals(category)"))


def hash_password(password: str) -> str:
    with PASSWORD_HASH_SEMAPHORE:
        return PASSWORD_HASH.hash(password)


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        with PASSWORD_HASH_SEMAPHORE:
            return PASSWORD_HASH.verify(password, stored_hash)
    except Exception:
        return False


def remote_address(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def enforce_rate_limit(db: Session, key: str, limit: int, window_seconds: int) -> None:
    now = utc_now_naive()
    cutoff = now - timedelta(seconds=window_seconds)
    key_hash = hashlib.sha256(key.encode("utf-8")).hexdigest()
    should_reset = RateLimitBucket.window_started_at <= cutoff
    statement = sqlite_insert(RateLimitBucket).values(key_hash=key_hash, window_started_at=now, attempts=1)
    statement = statement.on_conflict_do_update(
        index_elements=[RateLimitBucket.key_hash],
        set_={
            "window_started_at": case((should_reset, now), else_=RateLimitBucket.window_started_at),
            "attempts": case((should_reset, 1), else_=RateLimitBucket.attempts + 1),
        },
    )
    db.execute(statement)
    db.execute(delete(RateLimitBucket).where(RateLimitBucket.window_started_at < now - timedelta(days=2)))
    db.commit()
    db.expire_all()
    bucket = db.get(RateLimitBucket, key_hash)
    if bucket and bucket.attempts > limit:
        retry_after = max(1, int((bucket.window_started_at + timedelta(seconds=window_seconds) - now).total_seconds()))
        raise HTTPException(status_code=429, detail="Слишком много попыток. Попробуйте позже.", headers={"Retry-After": str(retry_after)})


def account_payload(cafe: CafeAccount) -> dict:
    return {"id": cafe.id, "business_name": cafe.business_name, "email": cafe.email, "city": cafe.city}


def set_session_cookie(response: Response, raw_token: str) -> None:
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=raw_token,
        max_age=SESSION_MAX_SECONDS,
        path="/",
        secure=COOKIE_SECURE,
        httponly=True,
        samesite="strict",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/", secure=COOKIE_SECURE, httponly=True, samesite="strict")


def create_session(cafe: CafeAccount, request: Request, response: Response, db: Session) -> dict:
    old_token = request.cookies.get(SESSION_COOKIE_NAME)
    if old_token:
        old_hash = hashlib.sha256(old_token.encode("utf-8")).hexdigest()
        old_session = db.scalar(select(CafeSession).where(CafeSession.token_hash == old_hash))
        if old_session:
            old_session.revoked = True
    now = utc_now_naive()
    db.execute(delete(CafeSession).where(CafeSession.expires_at < now - timedelta(days=1)))
    raw_token = secrets.token_urlsafe(32)
    csrf_token = secrets.token_urlsafe(32)
    session = CafeSession(
        cafe_id=cafe.id,
        token_hash=hashlib.sha256(raw_token.encode("utf-8")).hexdigest(),
        csrf_token=csrf_token,
        created_at=now,
        last_seen_at=now,
        expires_at=now + timedelta(seconds=SESSION_MAX_SECONDS),
        revoked=False,
    )
    db.add(session)
    db.commit()
    set_session_cookie(response, raw_token)
    return {"authenticated": True, "account": account_payload(cafe), "csrf_token": csrf_token, "expires_at": session.expires_at.isoformat() + "Z"}


def require_current_cafe(request: Request, response: Response, db: Session = Depends(get_db)) -> CafeAccount:
    raw_token = request.cookies.get(SESSION_COOKIE_NAME)
    if not raw_token:
        raise HTTPException(status_code=401, detail="Войдите в аккаунт кафе")
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    session = db.scalar(select(CafeSession).where(CafeSession.token_hash == token_hash, CafeSession.revoked.is_(False)))
    now = utc_now_naive()
    if not session:
        clear_session_cookie(response)
        raise HTTPException(status_code=401, detail="Сессия завершена. Войдите снова.")
    if session.expires_at <= now or session.last_seen_at <= now - timedelta(seconds=SESSION_IDLE_SECONDS):
        session.revoked = True
        db.commit()
        clear_session_cookie(response)
        raise HTTPException(status_code=401, detail="Сессия истекла. Войдите снова.")
    supplied_csrf = request.headers.get("x-csrf-token", "")
    if not supplied_csrf or not secrets.compare_digest(supplied_csrf, session.csrf_token):
        raise HTTPException(status_code=403, detail="Не пройдена проверка CSRF. Обновите страницу и повторите.")
    cafe = db.get(CafeAccount, session.cafe_id)
    if cafe is None:
        session.revoked = True
        db.commit()
        clear_session_cookie(response)
        raise HTTPException(status_code=401, detail="Аккаунт кафе недоступен")
    session.last_seen_at = now
    db.commit()
    request.state.cafe_session_id = session.id
    return cafe


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(bind=engine)
    migrate_schema()
    with SessionLocal() as db:
        seed_demo_deals(db)
    yield


app = FastAPI(
    title="QaldyQ API",
    description="Маркетплейс наборов еды со скидкой от кафе, пекарен и магазинов Казахстана.",
    version="1.0.0",
    lifespan=lifespan,
    docs_url=None if QALDYQ_ENV == "production" else "/docs",
    redoc_url=None if QALDYQ_ENV == "production" else "/redoc",
    openapi_url=None if QALDYQ_ENV == "production" else "/openapi.json",
)


@app.middleware("http")
async def protect_browser_writes_and_add_headers(request: Request, call_next):
    response = None
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        origin = request.headers.get("origin")
        host = request.headers.get("host", "").lower()
        if origin:
            parsed_origin = urlsplit(origin)
            if parsed_origin.scheme.lower() != request.url.scheme.lower() or parsed_origin.netloc.lower() != host:
                response = JSONResponse(status_code=403, content={"detail": "Запросы с другого источника запрещены"})
        elif request.cookies.get(SESSION_COOKIE_NAME):
            response = JSONResponse(status_code=403, content={"detail": "Для изменения данных требуется заголовок Origin"})
    if response is None:
        response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "geolocation=(self)")
    if request.url.path.startswith(("/api/auth", "/api/cafe", "/api/orders")):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


@app.post("/api/auth/register", status_code=201)
def register_cafe(payload: CafeRegister, request: Request, response: Response, db: Session = Depends(get_db)):
    enforce_rate_limit(db, f"register:{remote_address(request)}", limit=5, window_seconds=3600)
    if db.scalar(select(CafeAccount).where(CafeAccount.email == payload.email)):
        raise HTTPException(status_code=409, detail="Аккаунт с таким email уже существует")
    cafe = CafeAccount(
        business_name=payload.business_name,
        email=payload.email,
        city=payload.city,
        password_hash=hash_password(payload.password),
    )
    db.add(cafe)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=409, detail="Не удалось создать аккаунт. Проверьте email и повторите.")
    db.refresh(cafe)
    return create_session(cafe, request, response, db)


@app.post("/api/auth/login")
def login_cafe(payload: CafeLogin, request: Request, response: Response, db: Session = Depends(get_db)):
    client = remote_address(request)
    enforce_rate_limit(db, f"login-ip:{client}", limit=120, window_seconds=900)
    enforce_rate_limit(db, f"login-account:{client}:{payload.email}", limit=8, window_seconds=900)
    cafe = db.scalar(select(CafeAccount).where(CafeAccount.email == payload.email))
    stored_hash = cafe.password_hash if cafe else DUMMY_PASSWORD_HASH
    if not verify_password(payload.password, stored_hash) or cafe is None:
        raise HTTPException(status_code=401, detail="Неверный email или пароль")
    return create_session(cafe, request, response, db)


@app.get("/api/auth/me")
def current_cafe_session(request: Request, response: Response, db: Session = Depends(get_db)):
    raw_token = request.cookies.get(SESSION_COOKIE_NAME)
    if not raw_token:
        return {"authenticated": False}
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    session = db.scalar(select(CafeSession).where(CafeSession.token_hash == token_hash, CafeSession.revoked.is_(False)))
    now = utc_now_naive()
    if not session or session.expires_at <= now or session.last_seen_at <= now - timedelta(seconds=SESSION_IDLE_SECONDS):
        if session:
            session.revoked = True
            db.commit()
        clear_session_cookie(response)
        return {"authenticated": False}
    cafe = db.get(CafeAccount, session.cafe_id)
    if cafe is None:
        session.revoked = True
        db.commit()
        clear_session_cookie(response)
        return {"authenticated": False}
    session.last_seen_at = now
    db.commit()
    return {"authenticated": True, "account": account_payload(cafe), "csrf_token": session.csrf_token, "expires_at": session.expires_at.isoformat() + "Z"}


@app.post("/api/auth/logout")
def logout_cafe(request: Request, response: Response, db: Session = Depends(get_db), cafe: CafeAccount = Depends(require_current_cafe)):
    session = db.get(CafeSession, getattr(request.state, "cafe_session_id", None))
    if session:
        session.revoked = True
        db.commit()
    clear_session_cookie(response)
    return {"authenticated": False}


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "service": "QaldyQ"}


@app.get("/api/deals", response_model=list[DealOut])
def list_deals(
    city: str | None = None,
    search: str | None = None,
    category: str | None = None,
    db: Session = Depends(get_db),
):
    now = utc_now_naive()
    statement = select(Deal).where(Deal.quantity > 0, or_(Deal.pickup_end_at.is_(None), Deal.pickup_end_at > now))
    if search and len(search) > 120:
        raise HTTPException(status_code=422, detail="Поисковый запрос не должен превышать 120 символов")
    if city and city not in CITY_NAMES:
        raise HTTPException(status_code=422, detail="Выберите один из 10 городов QaldyQ")
    if city:
        statement = statement.where(Deal.city == city)
    if category and category not in DEAL_CATEGORIES:
        raise HTTPException(status_code=422, detail="Выберите категорию предложения")
    if category:
        statement = statement.where(Deal.category == category)
    if search and search.strip():
        term = f"%{search.strip()}%"
        statement = statement.where((Deal.title.ilike(term)) | (Deal.cafe_name.ilike(term)) | (Deal.description.ilike(term)))
    deals = db.scalars(statement.order_by(Deal.created_at.desc(), Deal.id.desc())).all()
    return [deal_payload(deal, now) for deal in deals]


@app.post("/api/deals", response_model=DealOut, status_code=201)
def create_deal(
    payload: DealCreate,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    cafe: CafeAccount = Depends(require_current_cafe),
):
    enforce_rate_limit(db, f"deal:{cafe.id}", limit=30, window_seconds=3600)
    if payload.new_price >= payload.old_price:
        raise HTTPException(status_code=422, detail="Цена со скидкой должна быть ниже обычной")
    pickup_start_at = to_utc_naive(payload.pickup_start_at)
    pickup_end_at = to_utc_naive(payload.pickup_end_at)
    if pickup_start_at is None or pickup_end_at is None:
        pickup_start_at, pickup_end_at = pickup_window_datetimes(payload.pickup_time)
    now = utc_now_naive()
    if pickup_start_at is None or pickup_end_at is None:
        raise HTTPException(status_code=422, detail="Укажите корректное время начала и окончания самовывоза")
    if pickup_start_at <= now or pickup_end_at <= now:
        raise HTTPException(status_code=422, detail="Окно самовывоза должно быть в будущем")
    values = payload.model_dump(exclude={"pickup_start_at", "pickup_end_at"})
    if payload.mystery_box:
        values["title"] = "Вечерний бокс-сюрприз от шефа"
        if not values["description"]:
            values["description"] = "Состав — сюрприз. Уточните аллергены и ограничения у кафе до бронирования."
    values.update(
        owner_id=cafe.id,
        cafe_name=cafe.business_name,
        listed_quantity=payload.quantity,
        pickup_start_at=pickup_start_at,
        pickup_end_at=pickup_end_at,
    )
    deal = Deal(**values)
    db.add(deal)
    db.commit()
    db.refresh(deal)
    return deal_payload(deal)


@app.post("/api/orders", status_code=201)
def create_order(payload: OrderCreate, request: Request, db: Session = Depends(get_db)):
    enforce_rate_limit(db, f"order:{remote_address(request)}", limit=20, window_seconds=600)
    now = utc_now_naive()
    existing = db.get(Deal, payload.deal_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Предложение не найдено")
    if existing.pickup_end_at and to_utc_naive(existing.pickup_end_at) <= now:
        raise HTTPException(status_code=410, detail="Окно самовывоза закончилось")
    # A conditional UPDATE is atomic in SQLite: two buyers cannot reserve the final unit.
    result = db.execute(
        update(Deal)
        .where(
            Deal.id == payload.deal_id,
            Deal.quantity > 0,
            or_(Deal.pickup_end_at.is_(None), Deal.pickup_end_at > now),
        )
        .values(quantity=Deal.quantity - 1)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        db.rollback()
        db.expire_all()
        exists = db.get(Deal, payload.deal_id)
        if exists is None:
            raise HTTPException(status_code=404, detail="Предложение не найдено")
        if exists.pickup_end_at and to_utc_naive(exists.pickup_end_at) <= now:
            raise HTTPException(status_code=410, detail="Окно самовывоза закончилось")
        raise HTTPException(status_code=409, detail="Этот набор уже забрали. Выберите другое предложение.")

    deal = db.get(Deal, payload.deal_id)
    db.refresh(deal)
    price_details = flash_price_details(deal, now)
    order = Order(
        deal_id=deal.id,
        customer_name=payload.customer_name,
        phone=payload.phone,
        quantity=1,
        unit_price=price_details["effective_price"],
        weight_kg=deal.weight_kg,
        flash_sale_applied=price_details["flash_sale_active"],
        delivery_method=payload.delivery_method,
        delivery_address=payload.delivery_address,
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    return {
        "id": order.id,
        "deal_id": deal.id,
        "title": deal.title,
        "cafe_name": deal.cafe_name,
        "customer_name": order.customer_name,
        "unit_price": order.unit_price,
        "discount_percent": price_details["discount_percent"],
        "quantity": order.quantity,
        "flash_sale_applied": order.flash_sale_applied,
        "pickup_time": deal.pickup_time,
        "city": deal.city,
        "delivery_method": order.delivery_method,
    }


@app.post("/api/orders/batch", status_code=201)
def create_batch_order(payload: BatchOrderCreate, request: Request, db: Session = Depends(get_db)):
    enforce_rate_limit(db, f"order-batch:{remote_address(request)}", limit=12, window_seconds=600)
    quantities: dict[int, int] = {}
    for item in payload.items:
        quantities[item.deal_id] = quantities.get(item.deal_id, 0) + item.quantity
        if quantities[item.deal_id] > 100:
            raise HTTPException(status_code=422, detail="В одной позиции можно забронировать не более 100 наборов")
    if len(quantities) > 20:
        raise HTTPException(status_code=422, detail="В корзине может быть не более 20 разных предложений")

    now = utc_now_naive()
    deals = {deal.id: deal for deal in db.scalars(select(Deal).where(Deal.id.in_(quantities))).all()}
    if len(deals) != len(quantities):
        raise HTTPException(status_code=404, detail="Одно из предложений больше не найдено")

    created: list[tuple[Deal, Order, dict]] = []
    try:
        for deal_id, quantity in quantities.items():
            deal = deals[deal_id]
            if deal.pickup_end_at and to_utc_naive(deal.pickup_end_at) <= now:
                raise HTTPException(status_code=410, detail=f"Окно самовывоза «{deal.title}» закончилось")
            result = db.execute(
                update(Deal)
                .where(
                    Deal.id == deal_id,
                    Deal.quantity >= quantity,
                    or_(Deal.pickup_end_at.is_(None), Deal.pickup_end_at > now),
                )
                .values(quantity=Deal.quantity - quantity)
                .execution_options(synchronize_session=False)
            )
            if result.rowcount != 1:
                raise HTTPException(status_code=409, detail=f"Недостаточно наборов «{deal.title}». Корзина не оформлена.")
            db.refresh(deal)
            price_details = flash_price_details(deal, now)
            order = Order(
                deal_id=deal.id,
                customer_name=payload.customer_name,
                phone=payload.phone,
                quantity=quantity,
                unit_price=price_details["effective_price"],
                weight_kg=deal.weight_kg,
                flash_sale_applied=price_details["flash_sale_active"],
                delivery_method=payload.delivery_method,
                delivery_address=payload.delivery_address,
            )
            db.add(order)
            created.append((deal, order, price_details))
        db.flush()
        db.commit()
    except HTTPException:
        db.rollback()
        raise

    orders = []
    for deal, order, price_details in created:
        db.refresh(order)
        orders.append({
            "id": order.id,
            "deal_id": deal.id,
            "title": deal.title,
            "cafe_name": deal.cafe_name,
            "quantity": order.quantity,
            "unit_price": order.unit_price,
            "discount_percent": price_details["discount_percent"],
            "flash_sale_applied": order.flash_sale_applied,
            "pickup_time": deal.pickup_time,
            "city": deal.city,
            "delivery_method": order.delivery_method,
        })
    total_kzt = round(sum(order["unit_price"] * order["quantity"] for order in orders), 2)
    return {"orders": orders, "item_count": sum(order["quantity"] for order in orders), "total_kzt": total_kzt}


@app.get("/api/cafe/dashboard")
def cafe_dashboard(db: Session = Depends(get_db), cafe: CafeAccount = Depends(require_current_cafe)):
    deals = db.scalars(select(Deal).where(Deal.owner_id == cafe.id).order_by(Deal.created_at.desc(), Deal.id.desc())).all()
    orders = db.scalars(select(Order).join(Deal, Order.deal_id == Deal.id).where(Deal.owner_id == cafe.id).order_by(Order.created_at.desc(), Order.id.desc())).all()
    return {
        "deals": [deal_payload(deal) for deal in deals],
        "orders": [order_payload(order) for order in orders],
    }


@app.get("/api/cafe/forecast")
def cafe_forecast(db: Session = Depends(get_db), cafe: CafeAccount = Depends(require_current_cafe)):
    now = utc_now_naive()
    history = db.scalars(
        select(Deal)
        .where(
            Deal.owner_id == cafe.id,
            Deal.pickup_end_at.is_not(None),
            Deal.pickup_end_at <= now,
            Deal.created_at >= now - timedelta(days=90),
        )
        .order_by(Deal.pickup_end_at.desc())
        .limit(30)
    ).all()
    local_hour = datetime.now(KAZAKHSTAN_TZ).hour
    time_factor = 1.15 if local_hour < 16 else 1.0 if local_hour < 19 else 0.85
    if len(history) >= 3:
        average_leftover = sum(max(0, deal.quantity) for deal in history) / len(history)
        suggested = max(0, min(5000, math.ceil(average_leftover * time_factor)))
        basis = f"Среднее по {len(history)} завершённым объявлениям за 90 дней, с поправкой на время дня."
        confidence = min(0.9, round(0.35 + len(history) * 0.04, 2))
        method = "completed_listing_history"
    else:
        suggested = 5 if local_hour < 16 else 3 if local_hour < 20 else 1
        basis = "Мало завершённых объявлений для истории; показан консервативный ориентир по времени дня."
        confidence = 0.2
        method = "time_of_day_cold_start"
    return {
        "suggested_quantity": suggested,
        "completed_listings": len(history),
        "confidence": confidence,
        "method": method,
        "time_factor": time_factor,
        "basis": basis,
        "disclaimer": "MVP-эвристика, не обученная AI-модель. Проверьте фактические остатки перед публикацией.",
    }


@app.get("/api/orders", response_model=list[OrderOut])
def list_orders(db: Session = Depends(get_db), cafe: CafeAccount = Depends(require_current_cafe)):
    orders = db.scalars(select(Order).join(Deal, Order.deal_id == Deal.id).where(Deal.owner_id == cafe.id).order_by(Order.created_at.desc(), Order.id.desc())).all()
    return [order_payload(order) for order in orders]


@app.get("/stats", include_in_schema=False)
@app.get("/api/stats")
def get_stats(db: Session = Depends(get_db)):
    totals = db.execute(
        select(
            func.count(Order.id),
            func.coalesce(func.sum(Order.unit_price * Order.quantity), 0.0),
            func.coalesce(func.sum(Order.weight_kg * Order.quantity), 0.0),
        )
    ).one()
    order_count = int(totals[0] or 0)
    turnover = round(float(totals[1] or 0.0), 2)
    saved_kg = round(float(totals[2] or 0.0), 3)
    return {
        "total_orders": order_count,
        "turnover_kzt": turnover,
        "commission_rate": COMMISSION_RATE,
        "commission_kzt": round(turnover * COMMISSION_RATE, 2),
        "food_saved_kg": saved_kg,
    }


@app.get("/")
def home():
    return FileResponse(BASE_DIR / "static" / "index.html")


app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
