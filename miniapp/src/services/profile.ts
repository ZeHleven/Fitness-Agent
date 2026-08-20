import { apiRequest } from '../core/request'
import type { ProfileUpdate, UserProfile } from '../types/api'

export const profileApi = {
  get: () => apiRequest<UserProfile>('/profile'),
  update: (profile: ProfileUpdate) => apiRequest<UserProfile>('/profile', {
    method: 'PUT',
    data: profile
  })
}
