// SPDX-License-Identifier: AGPL-3.0-or-later
/**
 * Convenience re-exports of user-management hooks and types from auth module.
 *
 * Components that only deal with user CRUD can import from '@/api/users'
 * instead of pulling from the broader auth module.
 */
export type {
  User,
  UserListParams,
  UserListResponse,
  UserStatsResponse,
  CreateUserRequest,
  UpdateUserRequest,
} from './auth';

export {
  useUsers,
  useUser,
  useUserStats,
  useCreateUser,
  useUpdateUser,
  useToggleUser,
  useDeleteUser,
} from './auth';
