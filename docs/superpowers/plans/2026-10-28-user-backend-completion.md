# User business and backend foundation completion

**Goal:** Complete backend APIs, tests and handoff documentation. Frontend work,
RAG, document ingestion, password recovery/change and bulk logout are excluded.
User confirmed backend-only delivery on 2026-10-28.

**Approach:** Extend the current FastAPI modular monolith. Keep user/admin roles,
existing error responses, JWT contract and v2 Redis session keys. Do not introduce
a permission framework or new services. Preserve all existing workspace changes.

## Contracts

- `PATCH /api/v1/users/me`: authenticated; optional username/email, at least one
  required; explicit null and extra fields rejected. Apply registration
  normalization and uniqueness rules. Return UserResponse, 409 for conflicts.
- `GET /api/v1/admin/users`: current database admin role required; page=1,
  page_size=20 (max 100), optional q (username/email), role, status; return
  `{items: UserResponse[], total, page, page_size}` in stable created_at/id order.
- `GET /api/v1/admin/users/{user_id}`: UserResponse or 404.
- `PATCH /api/v1/admin/users/{user_id}/status`: `{status: active|disabled}`;
  regular users only; administrators cannot be disabled through this API.
  Repeat setting the same status is idempotent. Return UserResponse.
- Disable increments a database `auth_version` stored in newly issued sessions.
  Both current-user and refresh checks compare it. Missing legacy session version
  means 0, preserving existing active accounts. Re-enable cannot revive old logins.
- Optional ADMIN_USERNAME/ADMIN_EMAIL/ADMIN_PASSWORD initialize the first admin
  at startup after migrations. All unset skips initialization; partial config
  fails validation. Idempotent under concurrent boot and never promotes an
  existing regular account or resets an existing admin password. Any existing
  administrator skips creation and keeps current identity, password and status;
  original initialization settings remain harmless after profile edits. No default
  credentials are shipped. User confirmed this rule on 2026-10-28.

## Work and acceptance

- [x] Profile schema/service/API plus regression and real-database checks.
- [x] Administrator schema/repository/service/routes and current-role dependency.
- [x] Auth-version migration and access/refresh/login integration; tests cover
  disabled→enabled old-token rejection and fresh-login success.
- [x] Optional administrator configuration, bootstrap, Compose forwarding and
  fresh/repeated-start verification.
- [x] Coverage >=80% for app in CI, backend Postman collection, reproducible P95
  authentication/user-management probe, and frontend-team API handoff document.
- [x] Update current README and PRD scope without restoring bulk logout.
- [x] Backend checks, existing frontend regressions, real MySQL/Redis integration,
  fresh-install/migration checks, local rebuilt service health and independent review.

## Ownership

Profile work owns `schemas/profile.py`, `services/profile.py`, existing users
endpoint and UserRepository extensions, plus new profile tests. Quality/handoff
work owns pyproject/uv.lock, backend CI, documentation, Postman and benchmark.
Primary implementer owns admin, bootstrap, auth version, configuration/migration
and integration assembly. Shared interfaces above are fixed for parallel work.

## Review focus

Check privilege escalation and protected admins; malformed/extra/null updates;
duplicate identifiers under races; concurrent initialization and status changes;
disabled/re-enabled credentials; safe storage errors; legacy active sessions;
cleanup of only explicitly generated test data. Tests precede implementation.

## Verification and confirmed initialization rule

Backend: 500 tests passed, app line coverage 98.94%; Ruff lint and formatting
passed. All 12 real integration modules passed, including profile concurrency and
admin disable/re-enable. Four existing frontend browser checks passed after
starting the previously stopped web service. Isolated fresh-install checks
verified migration, administrator creation, concurrent/repeated initialization,
password preservation, restart and cleanup. Local serial performance report is
`docs/verification/2026-10-28-backend-performance.json` (20 measured samples per
endpoint, 3 warmups, P95 6.817–98.332ms, all below the 500ms goal). These figures
are a local baseline, not a production or concurrent-load guarantee.

Independent review found no new actionable issues. The user confirmed skipping
creation when an administrator already exists. Updated regression tests cover
both username and email edits; the fresh-install scenario additionally edits the
administrator via the public profile API, restarts with original initialization
settings and checks the new identity, same password and same account ID.

Final confirmed-rule verification passed: all 500 backend tests, 98.94% coverage,
Ruff checks, updated container build and isolated first-boot/profile-edit/restart
scenario. Temporary containers and test volumes were removed. All planned backend
deliverables are complete; workspace changes are saved locally and not committed.
