# Signal Radar — Operational Guide

> Signal Radar is the LinkedIn post monitoring and engagement system. It polls LinkedIn for posts (profile + company) and tracks engagement (reactions, comments, reposts).

---

## Operation Status Matrix

| Operation | Status | QueryId | Response Path | Variables | Notes |
|-----------|--------|---------|---------------|-----------|-------|
| `fetchProfilePosts` | **✅ LIVE** (2026-05-05) | `voyagerFeedDashProfileUpdates.4af00b28d60ed0f1488018948daad822` | `data.posts` | `{profileUrn, count, start}` | Wave FS-4 (2026-05-05): CDP connectivity restored after Chrome restart (stuck target registry). Smoke passes: 10 profile posts fetched. Parser handles GraphQL `*elements` + `included` resolution and legacy `data.posts`. |
| `fetchCompanyPosts` | **DEFERRED / KNOWN BROKEN** | (was populated but broken by LinkedIn changes) | — | — | Out of scope for profile-first change. Separate change needed. |
| `fetchPostReactions` | **✅ LIVE** (2026-05-05) | `voyagerSocialDashReactions.41ebf31a9f4c4a84e35a49d5abc9010b` | `data.data.socialDashReactionsByReactionType` | `{threadUrn, count, start}` | Wave FS-7: Validated live against activity URN. Returns 4 reactions. Uses `*elements` + `included` resolution. |
| `fetchPostComments` | **✅ LIVE** (2026-05-05) | `voyagerSocialDashComments.afec6d88d7810d45548797a8dac4fb87` | `data.data.socialDashCommentsBySocialDetail` | `{socialDetailUrn, count, start, numReplies, sortOrder}` | Wave FS-7: Validated live. Returns 0 comments (post has none). LinkedIn returns `null` container when empty; parser updated to handle null as empty. Uses `*elements`. |
| `fetchPostReposts` | **✅ LIVE** (2026-05-05) | `voyagerFeedDashReshareFeed.dc56f7e6b303133b71fdbb584ec2a2a5` | `data.data.feedDashReshareFeedByReshareFeed` | `{targetUrn, count, start}` | Wave FS-7: Validated live. Returns 0 reposts (post has none). LinkedIn returns `null` container when empty; parser updated. Uses `elements` (not `*elements`). |

---

## Capture Methodology

- Automated CDP-connectOverCDP via Playwright against existing `linkedin-chrome.service` session (no operator engagement)
- Wave SR-5 used this pattern for `fetchProfilePosts`
- No new Chrome processes spawned; process count unchanged (16 → 16)

---

## When LinkedIn Rotates queryIds (Runbook)

**Detection signal**: `RESPONSE_SHAPE_CHANGED` failure type from smoke test exit-1

**Remediation steps**:
1. Check if the container key exists but is `null` (zero engagement) vs completely missing (true schema drift)
   - Null container → update parser to treat null as empty result (see FS-7 fix)
   - Missing key → true schema drift; proceed to capture
2. Re-run capture (CDP-connectOverCDP through `linkedin-chrome.service`)
3. Update `linkedin/operations/registry.py` `query_id` for the affected op
4. Re-run smoke test

Wave SR-5 used this exact pattern for `fetchProfilePosts`.
Wave FS-7 discovered that `RESPONSE_SHAPE_CHANGED` can be a false alarm when LinkedIn returns `null` for empty engagement containers. Always verify raw response structure before assuming schema drift.

---

## Smoke CLI Flags

The smoke test command supports:
- `--target-company <id>`: Company ID for fetchCompanyPosts (default: 1337)
- `--fallback-post-urn <urn>`: Known post URN for engagement ops when target company has no posts
- `--include-profile-posts <vanity>`: Run fetchProfilePosts smoke against a profile vanity (e.g. `joshuajvalentin`). Added in Wave SR-8.
- `--json`: Output JSON instead of human-readable text

---

## Daemon Prerequisites

> ⚠️ **WARNING — DISPLAY/Xvfb required**

`openoutreach-rundaemon.service` has never successfully polled due to DISPLAY/Xvfb mismatch. This is a separately tracked issue and was NOT touched by the Signal Radar profile-first change.

**Do NOT start or restart the daemon without explicit operator approval.** The daemon requires a live Xvfb virtual framebuffer to be available at the DISPLAY configured in its environment. Without it, the daemon will fail silently or produce no meaningful output.

---

## Known Issues / Pre-existing Bugs

- `FailureType.NETWORK_ERROR` — FIXED in Wave SR-8. Enum value added to `linkedin/operations/health.py` with TDD regression test.
- **Engagement ops "schema drift"** — **RESOLVED in Wave FS-7 (2026-05-05).** Root cause was NOT schema drift. LinkedIn returns `null` for engagement containers when a post has zero engagement (comments/reposts). Parsers treated `null` as `RESPONSE_SHAPE_CHANGED` instead of "empty result." Fix: updated `CommentsParser`, `ReactionsParser`, and `RepostsParser` to distinguish "key present with null value" (empty) from "key missing" (shape changed). All three ops now return `failure=None` with empty data arrays for zero-engagement posts.
- **Chrome CDP target registry corruption** — DISCOVERED in Wave FS-4 (2026-05-05). Chrome 145 can enter a state where a page target shows `attached: true` with no actual client connected, causing Playwright `connect_over_cdp` to hang. Fix: kill Chrome process (user-data-dir preserved); process auto-restarts. Runbook: `runbooks/signal-radar-chrome-cdp.md`.

---

## Wave History (Audit Trail)

| Wave | Date | Summary |
|------|------|---------|
| SR-3 | 2026-05-04 | P3 parser TDD complete. 20/20 tests. Reviewer flagged 4 follow-ups. |
| SR-4 | 2026-05-04 | SR-3 review fixes + P4 executor wiring complete. 71/71 tests. P4_BLOCKED on registry. |
| SR-5 | 2026-05-04 | Registry populated via automated CDP capture (queryId `voyagerFeedDashProfileUpdates.4af00b28d60ed0f1488018948daad822`). P5 smoke DEFERRED — LinkedIn session died after capture. |
| SR-6 | 2026-05-04 | Docs + OpenSpec sync + commit. P5 + remaining P6/P7 deferred to next session with live LinkedIn session. |
| SR-7 | 2026-05-04 | Chrome recon + runbook + helper script. CDP 9222 verified as only authenticated endpoint. |
| SR-8 | 2026-05-04 | Patched FailureType.NETWORK_ERROR enum + added --include-profile-posts smoke flag. Smoke FAILED (`Page.evaluate: TypeError: Failed to fetch`). Engagement ops not run. P5_STILL_BLOCKED. |
| **FS-4** | **2026-05-05** | **CDP/Playwright connectivity debug + smoke validation.** Root cause: stuck CDP target registry (`attached: true` ghost target). Fix: Chrome process kill + auto-restart (user-data-dir preserved). `fetchProfilePosts: PASS` (10 items). Engagement ops all return `RESPONSE_SHAPE_CHANGED` — LinkedIn schema drift on reactions/comments/reposts. |
| **FS-7** | **2026-05-05** | **Finish engagement validation, regression, and OpenSpec true-up.** File integrity check: no destructive stubs found. Test regression: 87/87 PASS. Live validation: all three engagement ops PASS against profile-derived URN. Root cause of FS-4 "schema drift": LinkedIn returns `null` engagement containers for zero-engagement posts; parsers treated `null` as shape changed. Fix: added null guards to `CommentsParser`, `ReactionsParser`, `RepostsParser` + fallback `elements` vs `*elements`. Smoke test: engagement ops `[PASS]`. Docs and OpenSpec updated. |

---

*Last updated: 2026-05-05 (Wave FS-7)*