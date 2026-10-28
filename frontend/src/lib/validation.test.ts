import { expect, it } from 'vitest'
import { validateRegistration } from './validation'

it('matches registration rules and preserves meaningful password whitespace', () => {
  expect(
    validateRegistration({
      username: ' alice_01 ',
      email: 'alice@example.com',
      password: ' Password123 ',
      confirm_password: ' Password123 ',
    }),
  ).toEqual({})
  expect(
    validateRegistration({
      username: 'bad-name',
      email: 'bad',
      password: 'abcdefgh',
      confirm_password: 'different',
    }),
  ).toMatchObject({
    username: expect.any(String),
    email: expect.any(String),
    password: expect.any(String),
    confirm_password: expect.any(String),
  })
})

it('does not accept overlong passwords or Unicode-only letters as registration strength', () => {
  const base = {
    username: 'alice',
    email: 'alice@example.com',
    password: '密码密码密码123',
    confirm_password: '密码密码密码123',
  }
  expect(validateRegistration(base).password).toBeTruthy()
  expect(validateRegistration({ ...base, password: 'A1'.repeat(65) }).password).toBeTruthy()
})
