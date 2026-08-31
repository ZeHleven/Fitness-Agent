from datetime import date, datetime
from typing import Literal, Optional
from pydantic import BaseModel, Field, model_validator


class MealItemCreate(BaseModel):
    food_id: Optional[str] = None
    food_name: str = Field(min_length=1, max_length=100)
    amount_g: float = Field(gt=0, le=10000)
    calories: float = Field(ge=0, le=50000)
    protein_g: float = Field(default=0.0, ge=0, le=5000)
    carbs_g: float = Field(default=0.0, ge=0, le=5000)
    fat_g: float = Field(default=0.0, ge=0, le=5000)


class MealItemResponse(BaseModel):
    id: str
    meal_id: str
    food_id: Optional[str] = None
    food_name: str
    amount_g: float
    calories: float
    protein_g: float
    carbs_g: float
    fat_g: float

    model_config = {"from_attributes": True}


class MealLogCreate(BaseModel):
    logged_at: date
    meal_type: Literal["早餐", "午餐", "晚餐", "加餐"] = "早餐"
    items: list[MealItemCreate] = Field(min_length=1, max_length=30)

    @model_validator(mode="after")
    def reject_future_date(self):
        if self.logged_at > date.today():
            raise ValueError("不能记录未来的饮食")
        return self


class MealLogResponse(BaseModel):
    id: str
    user_id: str
    logged_at: date
    meal_type: str
    created_at: datetime

    model_config = {"from_attributes": True}


class MealLogDetail(MealLogResponse):
    items: list[MealItemResponse] = []


class DailySummary(BaseModel):
    date: date
    total_calories: float
    total_protein_g: float
    total_carbs_g: float
    total_fat_g: float
    meals: list[MealLogDetail]


class NutritionAdviceResponse(BaseModel):
    advice: str
