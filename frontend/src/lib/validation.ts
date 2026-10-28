import type { RegisterInput } from './types'

export type FieldErrors = Partial<Record<keyof RegisterInput | 'identifier', string>>

export function validateRegistration(input: RegisterInput): FieldErrors {
  const errors: FieldErrors = {}
  if (!/^[A-Za-z0-9_]{3,32}$/.test(input.username.trim()))
    errors.username = '请输入 3–32 位字母、数字或下划线。'
  if (input.email.trim().length > 254 || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(input.email.trim()))
    errors.email = '请输入有效的邮箱地址。'
  if (
    input.password.length < 8 ||
    input.password.length > 128 ||
    !/[A-Za-z]/.test(input.password) ||
    !/[0-9]/.test(input.password)
  )
    errors.password = '密码需为 8–128 位，并同时包含英文字母和数字。'
  if (input.confirm_password !== input.password) errors.confirm_password = '两次输入的密码不一致。'
  return errors
}
