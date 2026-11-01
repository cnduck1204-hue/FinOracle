# Remove logout-all implementation plan

> Execute inline using superpowers:executing-plans. Track the checks below.

**Goal:** Remove logout on all devices and retain a single “退出登录” action.

**Architecture:** Remove the UI, client option, API endpoint, service branch and
Redis bulk revocation/index machinery. Keep the v2 session keys, refresh rotation,
replay protection and individual revocation compatible with existing logins.

**Tech stack:** React, TypeScript, FastAPI, Redis Lua, Vitest, pytest, Playwright.

**Spec:** User request: “退出所有设备这个功能干脆删了吧。”

**Constraints:** Preserve unrelated local edits; update current documentation only;
do not reset database data or active sessions.

## Implementation and verification

- [x] Add a regression asserting `/auth/logout-all` returns 404 and is absent from
  OpenAPI; verify it fails before implementation.
- [x] Cover ordinary client logout, cleared credentials, retained failure handling
  and a single visible “退出登录” button.
- [x] Remove the feature through frontend, API, services and dedicated Redis code;
  adapt related tests and cleanup code without changing session key names.
- [x] Update README descriptions and remove obsolete feature-specific styles.
- [x] Run backend lint/unit tests and frontend build/unit/browser tests.
- [x] Run real Redis and HTTP lifecycle checks: revoked tokens fail, refreshing
  cannot revive them, and other logins remain valid.
- [x] Rebuild local services and check health, UI and the removed API route.

## Review focus

Keep token validation, Redis failure responses, refresh replay protection and
single-session isolation covered by the existing suites. Ensure no removed action
remains reachable and generated test users/sessions are cleaned up.

## Results

The endpoint regression failed with 204 before removal and passes with 404 after
removal. Backend: 373 pytest tests, Ruff lint and formatting checks passed.
Frontend: 18 unit tests, production build, formatting checks and 4 Playwright
tests passed. All 10 real integration modules passed. Local services were rebuilt;
the API proxy reports healthy MySQL/Redis and the removed route returns 404.
Independent read-only review found no actionable issues. No commit was created;
unrelated local edits and existing v2 session records were preserved.
