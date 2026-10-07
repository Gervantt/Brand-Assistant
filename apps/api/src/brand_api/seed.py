"""Idempotent seed: demo clients and one demo account per role.

Runs on every container start. Demo accounts are re-synced to their canonical role, clients and
DEMO_PASSWORD each time, so a public demo can't be permanently broken by edits made through it.
"""

import asyncio
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from brand_api.auth.security import hash_password, verify_password
from brand_api.config import Settings, get_settings
from brand_api.db import create_engine, create_sessionmaker
from brand_api.logging_setup import configure_logging, get_logger
from brand_shared.db.models import Client, User
from brand_shared.permissions import Role
from brand_shared.schemas.clients import ClientProfile

log = get_logger("brand_api.seed")


@dataclass(frozen=True)
class DemoClient:
    slug: str
    name: str
    profile: ClientProfile


@dataclass(frozen=True)
class DemoUser:
    email: str
    full_name: str
    role: Role
    client_slugs: tuple[str, ...]
    description: str


DEMO_CLIENTS: tuple[DemoClient, ...] = (
    DemoClient(
        slug="bean-there",
        name="Bean There",
        profile=ClientProfile(
            industry="Кофейни, specialty coffee",
            description="Сеть из трёх specialty-кофеен в Алматы с собственной обжаркой.",
            audience="Горожане 22–35 лет: студенты, фрилансеры, офисные сотрудники рядом.",
            tone_of_voice="Тёплый, дружеский, с лёгким юмором; на «ты», без канцелярита.",
            platforms=["Instagram", "Telegram", "TikTok"],
            content_pillars=["Кофе и обжарка", "Бариста и команда", "Сезонное меню", "Сообщество"],
            banned_topics=["Политика", "Сравнение с конкурентами по имени", "Алкоголь"],
            hashtags=["#BeanThere", "#BeanThereAlmaty", "#кофезнаток"],
        ),
    ),
    DemoClient(
        slug="peakform",
        name="PeakForm",
        profile=ClientProfile(
            industry="Спортивный ритейл: бег, трейл, функциональный тренинг",
            description="Магазин экипировки с офлайн-точкой и онлайн-доставкой по Казахстану.",
            audience="Активные люди 25–45 лет, от новичков до любителей полумарафонов.",
            tone_of_voice="Энергичный, мотивирующий, экспертный: конкретика вместо пафоса.",
            platforms=["Instagram", "YouTube", "Telegram"],
            content_pillars=[
                "Экспертиза по снаряжению",
                "Тренировки",
                "Сообщество бегунов",
                "Акции",
            ],
            banned_topics=["Медицинские обещания", "Body shaming", "Допинг"],
            hashtags=["#PeakForm", "#бегаемвместе", "#PeakFormRun"],
        ),
    ),
)

DEMO_USERS: tuple[DemoUser, ...] = (
    DemoUser(
        "viewer@demo.com",
        "Вика Viewer",
        Role.VIEWER,
        ("bean-there",),
        "Только вопросы по брендбуку Bean There",
    ),
    DemoUser(
        "copywriter@demo.com",
        "Костя Copywriter",
        Role.COPYWRITER,
        ("bean-there",),
        "Генерация планов, постов и брифов для Bean There",
    ),
    DemoUser(
        "manager@demo.com",
        "Маша Manager",
        Role.MANAGER,
        ("bean-there", "peakform"),
        "Всё, что у копирайтера, плюс публикация планов; оба клиента",
    ),
    DemoUser(
        "admin@demo.com",
        "Аня Admin",
        Role.ADMIN,
        (),
        "Полный доступ: клиенты, пользователи, метрики",
    ),
)


async def seed_clients(session: AsyncSession) -> dict[str, Client]:
    existing = {c.slug: c for c in (await session.scalars(select(Client))).all()}
    for demo in DEMO_CLIENTS:
        profile = demo.profile.model_dump()
        client = existing.get(demo.slug)
        if client is None:
            client = Client(slug=demo.slug, name=demo.name, profile=profile)
            session.add(client)
            existing[demo.slug] = client
        else:
            client.name, client.profile = demo.name, profile
    await session.flush()
    return existing


async def seed_demo_users(
    session: AsyncSession, clients: dict[str, Client], password: str, bcrypt_rounds: int
) -> None:
    for demo in DEMO_USERS:
        user = (await session.scalars(select(User).where(User.email == demo.email))).first()
        if user is None:
            user = User(email=demo.email, password_hash=hash_password(password, bcrypt_rounds))
            session.add(user)
        elif not verify_password(password, user.password_hash):
            user.password_hash = hash_password(password, bcrypt_rounds)
        user.full_name = demo.full_name
        user.role = demo.role.value
        user.is_active = True
        user.is_demo = True
        user.clients = [clients[slug] for slug in demo.client_slugs]
    await session.flush()


async def run_seed(session: AsyncSession, settings: Settings) -> None:
    clients = await seed_clients(session)
    if settings.demo_mode:
        if settings.demo_password is None:
            log.warning("seed_demo_users_skipped", reason="DEMO_PASSWORD is not set")
        else:
            await seed_demo_users(
                session,
                clients,
                settings.demo_password.get_secret_value(),
                settings.bcrypt_rounds,
            )
    await session.commit()
    log.info("seed_complete", clients=len(clients), demo_users=settings.demo_mode)


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)
    engine = create_engine(settings.database_url)
    try:
        async with create_sessionmaker(engine)() as session:
            await run_seed(session, settings)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
