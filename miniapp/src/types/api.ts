import type { PlanAdjustmentProposalReference } from './plan-adjustment-proposal'


export interface TokenResponse {
  access_token: string
  refresh_token: string
  token_type: string
}

export interface WeChatLoginResponse extends TokenResponse {
  is_new_user: boolean
  onboarding_completed: boolean
}

export interface UserProfile {
  user_id: string
  age?: number | null
  gender?: string | null
  height_cm?: number | null
  weight_kg?: number | null
  bmi?: number | null
  bmi_category?: string | null
  experience_level?: string | null
  primary_goal?: string | null
  training_days_per_week?: number | null
  session_duration_min?: number | null
  training_location?: string | null
  diet_restriction?: string | null
  injuries: string[]
  chronic_conditions: string[]
  onboarding_completed: boolean
}

export interface ProfileUpdate {
  age?: number
  gender?: string
  height_cm?: number
  weight_kg?: number
  experience_level?: string
  primary_goal?: string
  training_days_per_week?: number
  session_duration_min?: number
  training_location?: string
  diet_restriction?: string
  injuries?: string[]
  chronic_conditions?: string[]
  onboarding_completed?: boolean
}

export interface ProfileUpdateResult extends UserProfile {
  active_plan_safety_status?: 'compatible' | 'needs_review' | null
  active_plan_safety_reasons?: string[]
}

export interface WeightLog {
  id: string
  weight_kg: number
  recorded_at: string
}

export interface PlannedExercise {
  id: string
  plan_id: string
  exercise_id: string
  exercise_name?: string | null
  day_of_week: number
  sets: number
  reps: string
  rest_seconds: number
  recommended_weight_kg?: number | null
  order_index: number
}

export interface WorkoutPlan {
  id: string
  name: string
  goal?: string | null
  duration_weeks: number
  days_per_week: number
  is_active: boolean
  ai_generated: boolean
  notes?: string | null
  safety_status: 'compatible' | 'needs_review'
  safety_reasons: string[]
  manual_proposals_enabled: boolean
  exercises: PlannedExercise[]
}

export interface Food {
  id: string
  name_zh: string
  name_en?: string | null
  category: string
  calories_per_100g: number
  protein_g: number
  carbs_g: number
  fat_g: number
  fiber_g?: number | null
  common_portion_g?: number | null
  diet_tags?: string[] | null
}

export interface MealItemInput {
  food_id?: string | null
  food_name: string
  amount_g: number
  calories: number
  protein_g: number
  carbs_g: number
  fat_g: number
}

export interface MealItem extends MealItemInput {
  id: string
  meal_id: string
}

export interface MealLog {
  id: string
  logged_at: string
  meal_type: '早餐' | '午餐' | '晚餐' | '加餐'
  created_at: string
  items: MealItem[]
}

export interface DailyNutritionSummary {
  date: string
  total_calories: number
  total_protein_g: number
  total_carbs_g: number
  total_fat_g: number
  meals: MealLog[]
}

export interface PersonalizedExerciseOption {
  exercise_id: string
  exercise_name: string
  category: string
  difficulty: string
  equipment: string[]
}

export interface PersonalizedPlanExercise {
  exercise_id: string
  exercise_name: string
  category: string
  day_of_week: number
  sets: number
  reps: string
  rest_seconds: number
  order_index: number
}

export interface PersonalizedPlanPreview {
  name: string
  goal: string
  duration_weeks: number
  days_per_week: number
  session_duration_min: number
  rationale: string[]
  safety_notes: string[]
  exercises: PersonalizedPlanExercise[]
  exercise_options: PersonalizedExerciseOption[]
  generation_strategy: string
}

export interface PersonalizedPlanPreviewRequest {
  goal?: string
  duration_weeks?: number
  days_per_week?: number
  session_duration_min?: number
}

export interface WorkoutSetRecord {
  set_number?: number
  reps: number
  weight_kg?: number | null
  is_personal_record?: boolean
}

export interface SessionExercise {
  id: string
  session_id: string
  exercise_id: string
  exercise_name?: string | null
  order_index: number
  target_sets?: number | null
  target_reps?: string | null
  target_weight_kg?: number | null
  rest_seconds?: number | null
  sets_data: WorkoutSetRecord[]
  previous_sets_data: WorkoutSetRecord[]
  personal_best_weight_kg?: number | null
  personal_best_reps?: number | null
}

export interface WorkoutFeedback {
  difficulty_feedback?: 'too_easy' | 'just_right' | 'too_hard' | null
  perceived_exertion?: number | null
  energy_level?: number | null
  pain_level: number
  pain_areas: string[]
  feedback_notes?: string | null
}

export interface WorkoutCompleteInput extends WorkoutFeedback {
  duration_min?: number
  notes?: string
}

export interface WorkoutAdjustment {
  exercise_id: string
  exercise_name: string
  action: string
  before: Record<string, unknown>
  after: Record<string, unknown>
  reason: string
  safety_priority: boolean
}

export interface WorkoutSession {
  id: string
  plan_id?: string | null
  plan_name?: string | null
  day_of_week?: number | null
  status: 'in_progress' | 'completed' | 'abandoned' | string
  trained_at: string
  duration_min?: number | null
  notes?: string | null
  started_at: string
  completed_at?: string | null
  total_sets: number
  total_reps: number
  total_volume_kg: number
  exercises: SessionExercise[]
  feedback?: WorkoutFeedback | null
  adjustments: WorkoutAdjustment[]
}

export interface WeeklyWorkoutProgress {
  week_start: string
  sessions: number
  sets: number
  reps: number
  volume_kg: number
}

export interface WorkoutProgress {
  weeks: number
  total_sessions: number
  total_sets: number
  total_reps: number
  total_volume_kg: number
  weekly: WeeklyWorkoutProgress[]
}

export interface AgentCard {
  type: string
  data: Record<string, unknown>
}

export interface AgentArtifactReference {
  id: string
  artifact_type: 'daily_meal_plan_v1'
  status: 'active' | 'superseded' | 'proposed' | 'consumed' | 'expired'
  version: number
  expires_at: string
  payload_fingerprint: string
}

export interface AgentChatResponse {
  reply: string
  conversation_id: string
  run_id: string
  cards: AgentCard[]
  proposal?: PlanAdjustmentProposalReference | null
  artifact?: AgentArtifactReference | null
}

export type AgentRunState = 'queued' | 'running' | 'completed' | 'failed'

export interface AgentRunSubmission {
  run_id: string
  conversation_id: string
  status: AgentRunState
  poll_after_ms: number
}

export interface AgentRunStatus {
  id: string
  status: AgentRunState
  reply?: string | null
  cards: AgentCard[]
  proposal?: PlanAdjustmentProposalReference | null
  artifact?: AgentArtifactReference | null
  error_code?: string | null
  error_message?: string | null
  poll_after_ms?: number | null
  queued_at: string
  processing_started_at?: string | null
  attempt_count: number
}

export interface AgentMessage {
  id: string
  run_id?: string | null
  role: 'user' | 'assistant' | 'tool' | 'system'
  content: string
  content_data: Record<string, unknown>
  created_at: string
}
