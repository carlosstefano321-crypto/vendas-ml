from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from . import config


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


engine = create_engine(config.DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class Product(Base):
    __tablename__ = "products"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)  # id do anúncio no ML
    title: Mapped[str] = mapped_column(Text)
    price: Mapped[float] = mapped_column(Float)
    original_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    permalink: Mapped[str] = mapped_column(Text)
    thumbnail: Mapped[str | None] = mapped_column(Text, nullable=True)
    free_shipping: Mapped[bool] = mapped_column(Boolean, default=False)
    term: Mapped[str] = mapped_column(String(200), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class PricePoint(Base):
    __tablename__ = "price_points"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"), index=True)
    price: Mapped[float] = mapped_column(Float)
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class Coupon(Base):
    __tablename__ = "coupons"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(80))
    percent: Mapped[float] = mapped_column(Float, default=0)        # ex.: 20 = 20%
    fixed_amount: Mapped[float] = mapped_column(Float, default=0)   # ex.: 50 = R$ 50 (se > 0, vale no lugar do %)
    max_discount: Mapped[float] = mapped_column(Float, default=0)   # teto do desconto (0 = sem teto)
    min_purchase: Mapped[float] = mapped_column(Float, default=0)   # compra mínima
    valid_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    terms: Mapped[str] = mapped_column(Text, default="")            # termos de busca aceitos (vazio = todos)
    campaign_url: Mapped[str] = mapped_column(Text, default="")     # link da campanha, para você conferir
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    source: Mapped[str] = mapped_column(String(20), default="manual")
    verification_status: Mapped[str] = mapped_column(String(30), default="not_verified")


class SearchTerm(Base):
    __tablename__ = "search_terms"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    term: Mapped[str] = mapped_column(String(200), unique=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")


class Offer(Base):
    __tablename__ = "offers"
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"), primary_key=True)
    coupon_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    price: Mapped[float] = mapped_column(Float)
    final_price: Mapped[float] = mapped_column(Float)
    reference_price: Mapped[float] = mapped_column(Float)
    discount_pct: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    posted: Mapped[bool] = mapped_column(Boolean, default=False)


class ReviewQueue(Base):
    __tablename__ = "review_queue"
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"), primary_key=True)
    message: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error: Mapped[str] = mapped_column(Text, default="")


def init_db() -> None:
    Base.metadata.create_all(engine)
    # Migração aditiva, preservando todos os cupons existentes.
    columns = {column["name"] for column in inspect(engine).get_columns("coupons")}
    with engine.begin() as conn:
        if "source" not in columns:
            conn.execute(text("ALTER TABLE coupons ADD COLUMN source VARCHAR(20) NOT NULL DEFAULT 'manual'"))
        if "verification_status" not in columns:
            conn.execute(text("ALTER TABLE coupons ADD COLUMN verification_status VARCHAR(30) NOT NULL DEFAULT 'not_verified'"))
