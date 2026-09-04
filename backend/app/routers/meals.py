from datetime import date, timedelta
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import delete, select
from app.database import get_db
from app.deps import get_current_user
from app.models.user import User
from app.models.meal import MealLog, MealItem
from app.models.food import Food
from app.schemas.meal import (
    MealLogCreate, MealLogDetail, MealLogUpdate, MealItemResponse, DailySummary,
    NutritionAdviceResponse,
)
from app.services.nutrition_queries import (
    build_daily_nutrition_summary,
    list_nutrition_history,
)

router = APIRouter(prefix="/meals", tags=["meals"])


async def _hydrate_items(
    db: AsyncSession,
    *,
    items,
) -> list[dict]:
    food_ids = {item.food_id for item in items if item.food_id}
    foods = list((await db.execute(
        select(Food).where(
            Food.id.in_(food_ids),
            Food.is_active.is_(True),
        )
    )).scalars().all()) if food_ids else []
    foods_by_id = {item.id: item for item in foods}
    if len(foods_by_id) != len(food_ids):
        raise HTTPException(status_code=400, detail="饮食记录包含不存在或已停用的食品")

    hydrated = []
    for item_data in items:
        values = item_data.model_dump()
        if item_data.food_id:
            food = foods_by_id[item_data.food_id]
            factor = item_data.amount_g / 100
            values.update({
                "food_name": food.name_zh,
                "calories": round(food.calories_per_100g * factor, 1),
                "protein_g": round(food.protein_g * factor, 1),
                "carbs_g": round(food.carbs_g * factor, 1),
                "fat_g": round(food.fat_g * factor, 1),
            })
        hydrated.append(values)
    return hydrated


def _meal_detail(meal: MealLog, items: list[MealItem]) -> MealLogDetail:
    result = MealLogDetail.model_validate(meal)
    result.items = [MealItemResponse.model_validate(item) for item in items]
    return result


@router.post("", response_model=MealLogDetail, status_code=201)
async def log_meal(
    body: MealLogCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    meal = MealLog(
        user_id=current_user.id,
        logged_at=body.logged_at,
        meal_type=body.meal_type,
    )
    db.add(meal)
    await db.flush()

    hydrated = await _hydrate_items(db, items=body.items)
    items = []
    for values in hydrated:
        item = MealItem(
            meal_id=meal.id,
            **values,
        )
        db.add(item)
        items.append(item)

    await db.commit()

    return _meal_detail(meal, items)


@router.put("/{meal_id}", response_model=MealLogDetail)
async def update_meal(
    meal_id: str,
    body: MealLogUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    meal = await db.scalar(
        select(MealLog).where(
            MealLog.id == meal_id,
            MealLog.user_id == current_user.id,
        ).with_for_update()
    )
    if not meal:
        raise HTTPException(status_code=404, detail="饮食记录不存在")
    earliest = date.today() - timedelta(days=29)
    if meal.logged_at < earliest or body.logged_at < earliest:
        raise HTTPException(status_code=409, detail="仅支持修改近 30 天的饮食记录")

    hydrated = await _hydrate_items(db, items=body.items)
    await db.execute(delete(MealItem).where(MealItem.meal_id == meal.id))
    meal.logged_at = body.logged_at
    meal.meal_type = body.meal_type
    items = [MealItem(meal_id=meal.id, **values) for values in hydrated]
    db.add_all(items)
    await db.commit()
    return _meal_detail(meal, items)


@router.get("/today", response_model=DailySummary)
async def today_summary(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    today = date.today()
    return await build_daily_nutrition_summary(
        db, user_id=current_user.id, target_date=today
    )


@router.get("/history", response_model=list[DailySummary])
async def meal_history(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await list_nutrition_history(db, user_id=current_user.id, days=30)


@router.delete("/{meal_id}", status_code=204)
async def delete_meal(
    meal_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    meal = await db.scalar(
        select(MealLog).where(
            MealLog.id == meal_id,
            MealLog.user_id == current_user.id,
        )
    )
    if not meal:
        raise HTTPException(status_code=404, detail="饮食记录不存在")
    await db.execute(delete(MealItem).where(MealItem.meal_id == meal.id))
    await db.delete(meal)
    await db.commit()


@router.get("/advice", response_model=NutritionAdviceResponse)
async def nutrition_advice(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from app.services.nutritionist import get_daily_nutrition_advice
    advice = await get_daily_nutrition_advice(db, user_id=current_user.id)
    return NutritionAdviceResponse(advice=advice)
