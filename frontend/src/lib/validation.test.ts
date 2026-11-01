import { expect, it } from 'vitest'
import { validateProfile, validateRegistration } from './validation'

it('validates editable profile fields without requiring a password', () => {
  expect(validateProfile({ username: ' alice_01 ', email: 'alice@example.com' })).toEqual({})
  for (const username of ['', 'ab', 'bad-name', '用户', 'a'.repeat(33)]) {
    expect(validateProfile({ username, email: 'alice@example.com' }).username).toBeTruthy()
  }
  for (const email of ['', 'bad', 'a@b', 'a'.repeat(243) + '@example.com']) {
    expect(validateProfile({ username: 'alice', email }).email).toBeTruthy()
  }
})

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
