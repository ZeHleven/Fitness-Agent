import { apiRequest } from '../core/request'
import type { PersonalizedExerciseOption } from '../types/api'

export interface CustomExerciseInput {
  name: string
  description: string
  muscles: string[]
  equipment: string[]
  contraindications: string[]
}

export const exerciseApi = {
  custom: () => apiRequest<PersonalizedExerciseOption[]>('/exercises/custom'),
  createCustom: (data: CustomExerciseInput) => apiRequest<PersonalizedExerciseOption>('/exercises/custom', { method: 'POST', data })
}
