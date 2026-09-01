import unicodedata

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import func, select

from app.models.food import Food, FoodAlias


def normalize_food_reference(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).strip().lower()
    return " ".join(normalized.split())


async def resolve_food_reference(
    db: AsyncSession,
    *,
    food_id: str | None = None,
    reference: str | None = None,
) -> list[Food]:
    """Resolve only exact canonical names or server-managed exact aliases."""
    if food_id:
        result = await db.execute(
            select(Food)
            .where(Food.id == food_id, Food.is_active.is_(True))
            .limit(2)
        )
        return list(result.scalars().all())

    normalized = normalize_food_reference(reference or "")
    if not normalized:
        return []
    for name_column in (Food.name_zh, Food.name_en):
        canonical = list((await db.execute(
            select(Food)
            .where(
                Food.is_active.is_(True),
                func.lower(func.trim(name_column)) == normalized,
            )
            .order_by(Food.id)
            .limit(2)
        )).scalars().all())
        if canonical:
            return canonical
    aliases = list((await db.execute(
        select(Food)
        .join(FoodAlias, FoodAlias.food_id == Food.id)
        .where(
            Food.is_active.is_(True),
            FoodAlias.normalized_alias == normalized,
        )
        .order_by(Food.id)
        .limit(2)
    )).scalars().all())
    return aliases


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
        normalized = normalize_food_reference(query)
        if normalized:
            stmt = stmt.where(
                Food.name_zh.ilike(f"%{normalized}%")
                | Food.name_en.ilike(f"%{normalized}%")
                | sa.exists(
                    select(FoodAlias.id).where(
                        FoodAlias.food_id == Food.id,
                        FoodAlias.normalized_alias.ilike(f"%{normalized}%"),
                    )
                )
            )
    stmt = stmt.order_by(Food.is_common_in_china.desc(), Food.name_zh).limit(limit)
    result = await db.execute(stmt)
    return list(result.scalars().all())
