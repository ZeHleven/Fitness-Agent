import { apiRequest } from '../core/request'
import type {
  DailyNutritionSummary,
  Food,
  MealItemInput,
  MealLog
} from '../types/api'

export const nutritionApi = {
  foods: (query = '', limit = 20) => apiRequest<Food[]>('/foods', {
    query: { q: query || undefined, limit }
  }),
  today: () => apiRequest<DailyNutritionSummary>('/meals/today'),
  history: () => apiRequest<DailyNutritionSummary[]>('/meals/history'),
  logMeal: (data: {
    logged_at: string
    meal_type: MealLog['meal_type']
    items: MealItemInput[]
  }) => apiRequest<MealLog>('/meals', {
    method: 'POST',
    data
  }),
  deleteMeal: (mealId: string) => apiRequest<void>(`/meals/${mealId}`, {
    method: 'DELETE'
  })
}
