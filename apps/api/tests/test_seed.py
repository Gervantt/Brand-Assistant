from sqlalchemy import func, select

from brand_api.config import Settings
from brand_api.db import create_engine, create_sessionmaker
from brand_api.seed import run_seed
from brand_shared.db.models import Client, User


async def test_seed_is_idempotent_and_restores_demo_accounts(
    settings: Settings, seeded: None
) -> None:
    engine = create_engine(settings.database_url)
    sessionmaker = create_sessionmaker(engine)
    async with sessionmaker() as session:
        viewer = (await session.scalars(select(User).where(User.email == "viewer@demo.com"))).one()
        viewer.role = "admin"  # simulate tampering through the public demo
        viewer.clients = []
        await session.commit()

    async with sessionmaker() as session:
        await run_seed(session, settings)
    async with sessionmaker() as session:
        await run_seed(session, settings)

    async with sessionmaker() as session:
        demo_users = await session.scalar(
            select(func.count()).select_from(User).where(User.is_demo.is_(True))
        )
        demo_clients = await session.scalar(
            select(func.count())
            .select_from(Client)
            .where(Client.slug.in_(["bean-there", "peakform"]))
        )
        viewer = (await session.scalars(select(User).where(User.email == "viewer@demo.com"))).one()
    await engine.dispose()

    assert demo_users == 4
    assert demo_clients == 2
    assert viewer.role == "viewer"
    assert [c.slug for c in viewer.clients] == ["bean-there"]
