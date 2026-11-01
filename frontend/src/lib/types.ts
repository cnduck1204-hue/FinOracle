export interface User {
  id: string
  username: string
  email: string
  role: 'user' | 'admin'
  status: 'active' | 'disabled'
  last_login_at: string | null
  created_at: string
  updated_at: string
}

export interface TokenPair {
  access_token: string
  refresh_token: string
  token_type: 'bearer'
  expires_in: number
  user: User
}

export type ProfileInput = Pick<User, 'username' | 'email'>

export interface UserListQuery {
  page?: number
  page_size?: number
  q?: string
  role?: User['role']
  status?: User['status']
}

export interface UserPage {
  items: User[]
  total: number
  page: number
  page_size: number
}

export interface RegisterInput {
  username: string
  email: string
  password: string
  confirm_password: string
}

export interface SessionSnapshot {
  user: User | null
  expiresAt: number | null
  hasSession: boolean
  notice: string | null
}
