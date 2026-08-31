import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.food import Food


async def query_nutrition_database(
    db: AsyncSession,
    *,
    category: str | None = None,
    diet_tag: str | None = None,
    min_protein_g: float | None = None,
    query: str | None = None,
    limit: int = 10,
) -> list[Food]:
    stmt = select(Food).where(Food.is_active.is_(True))
    if category:
        stmt = stmt.where(Food.category == category)
    if diet_tag:
        stmt = stmt.where(
            sa.cast(Food.diet_tags, sa.String).contains(f'"{diet_tag}"')
        )
    if min_protein_g is not None:
        stmt = stmt.where(Food.protein_g >= min_protein_g)
    if query:
        normalized = query.strip()
        if normalized:
            stmt = stmt.where(
                Food.name_zh.ilike(f"%{normalized}%")
                | Food.name_en.ilike(f"%{normalized}%")
            )
    stmt = stmt.order_by(Food.is_common_in_china.desc(), Food.name_zh).limit(limit)
    result = await db.execute(stmt)
    return list(result.scalars().all())
