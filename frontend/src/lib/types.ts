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
