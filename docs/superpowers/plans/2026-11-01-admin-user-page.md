# Administrator user management page

> Implementation uses the writing-plans, executing-plans, test-driven-development
> and verification-before-completion skills. Scope was selected by the user.

**Goal:** Connect the existing administrator APIs to a compact user management
page with search, filters, pagination, details and enable/disable operations.

**Architecture:** Extend the current React/TypeScript application and AuthClient;
reuse its protected requests, token rotation, error notices and 80% page scale.
Add `/admin/users`, an administrator navigation entry and a role guard. Use a
table with local horizontal scrolling on narrow screens, a detail section and
a native confirmation dialog. Existing backend contracts remain authoritative.

**Constraints:** Preserve current diagram changes. No new dependencies, user
creation/deletion, role editing, password resets, backend migrations or remote
push. Administrator accounts have no status actions. Use disposable test accounts
and clean up only their exact generated names. Do not change existing credentials.

**Files:** `frontend/src/lib/{types,api}.ts`, API tests, `src/App.tsx`,
`src/components/AppLayout.tsx`, `src/pages/{AuthPage,AdminUsersPage}.tsx`,
`src/styles.css`, administrator browser tests and their disposable-account helper;
update frontend README and backend handoff status.

## Task 1: Administrator client contracts

- [x] Add failing tests for list query encoding, malformed pagination, detail and
  status responses that never replace the administrator's own profile, and 403
  permission errors that preserve login.
- [x] Implement `listUsers(query)`, `getUser(id)`, `setUserStatus(id,status)` and
  UserPage/UserListQuery types through existing protected requests.
- [x] Verify targeted tests, then the frontend unit suite and strict build.

## Task 2: Administrator page

- [x] Add a real-backend browser test that fails on the missing administrator page.
- [x] Implement guarded navigation, search by username/email, role/status filters,
  page sizes, stable pagination, detail loading, retry and empty states.
- [x] Confirm status changes with their login impact, disable duplicate submission,
  refresh filtered totals/results and the open detail after success.
- [x] Verify ordinary-user denial, protected admin rows, desktop/mobile pagination,
  cancelled changes, disabled/re-enabled old-token rejection and fresh login.
- [x] Verify delayed list/detail responses cannot overwrite a newer selection.

## Task 3: Delivery

- [x] Run the frontend unit suite, build, changed-file formatting and browser suite.
- [x] Review loading/error paths, status mutations during navigation, response data
  separation and cleanup of disposable administrator accounts.
- [x] Rebuild the local web container and run administrator browser checks against
  the deployed page. Update documentation; preserve local Git/remote state.

## Decisions

The user approved integrating the existing administrator functions. Implementation
stays in the shared workspace to preserve the running local setup and pending
diagram edits. This plan records the concrete UI choices; no extra capabilities
or permission model are introduced.

## Review and verification

31 frontend unit tests and the strict production build pass. All 14 browser
scenarios pass against the rebuilt local web container, including seven
administrator and seven existing authentication scenarios. Added cases cover
late list/detail responses, list retry and failed status updates without duplicate
writes. Only temporary `e2e_` accounts are created and cleaned up.

A read-only review found modal dismissal lost keyboard focus. A browser test
reproduced this failure before the fix. Explicit dialog close now restores the
trigger on cancellation, or focuses the stable list heading after a successful
change; both focus assertions pass. The reviewer found no other actionable issue.
Changed-file Prettier and the disposable-account helper's Ruff checks pass.
The web service is healthy at http://127.0.0.1:8080. No Git commit or push was made.
