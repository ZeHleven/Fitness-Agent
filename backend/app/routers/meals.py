from datetime import date
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import delete, select
from app.database import get_db
from app.deps import get_current_user
from app.models.user import User
from app.models.meal import MealLog, MealItem
from app.models.food import Food
from app.schemas.meal import (
    MealLogCreate, MealLogDetail, MealItemResponse, DailySummary,
    NutritionAdviceResponse,
)
from app.services.nutrition_queries import (
    build_daily_nutrition_summary,
    list_nutrition_history,
)

router = APIRouter(prefix="/meals", tags=["meals"])


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

    food_ids = {item.food_id for item in body.items if item.food_id}
    foods = list((await db.execute(
        select(Food).where(
            Food.id.in_(food_ids),
            Food.is_active.is_(True),
        )
    )).scalars().all()) if food_ids else []
    foods_by_id = {item.id: item for item in foods}
    if len(foods_by_id) != len(food_ids):
        raise HTTPException(status_code=400, detail="饮食记录包含不存在或已停用的食品")

    items = []
    for item_data in body.items:
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
        item = MealItem(
            meal_id=meal.id,
            **values,
        )
        db.add(item)
        items.append(item)

    await db.commit()

    result = MealLogDetail.model_validate(meal)
    result.items = [MealItemResponse.model_validate(i) for i in items]
    return result


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
