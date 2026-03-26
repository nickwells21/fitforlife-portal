# FitForLife Portal — Notification & Engagement System  
## Implementation plan for Claude Code (handoff document)

**Do not treat this file as executable code.** It is the ordered plan to implement everything defined in:

`CURSOR_AGENT_SPEC.md` (same folder — single source of truth for behavior, copy, UI, and constraints).

---

## 0. Preconditions

1. Read **entire** `CURSOR_AGENT_SPEC.md` before writing code.
2. Stack: Flask monolith in `app.py`, Jinja2, Bootstrap 5, vanilla JS, existing `static/css/style.css`.
3. Obey **§14 Build Constraints** (no React, no new modules split from `app.py`, CSRF on POSTs, client-only route guards, FFL dark tokens only, no toasts, privacy rules for leaderboard).

---

## 1. Database — migrate / alter in order

| Step | What |
|------|------|
| 1.1 | Add tables: `daily_logs`, `exercise_volume_totals`, `workout_volume_records` (schemas in spec §3). |
| 1.2 | Ensure dormant tables exist in DB: `personal_records`, `badge_definitions`, `user_badges`, `streaks`, `rep_milestones` (create if missing). |
| 1.3 | Alter `exercise_logs`: `duration_seconds`, `time_seconds`, `distance`, `distance_unit`. |
| 1.4 | Alter `exercises`: `is_timed`, `is_hold` (defaults false). |
| 1.5 | Optional index on `notifications` `(user_id, is_read, created_at)` per spec §14. |
| 1.6 | Backfill `ExerciseVolumeTotal` from historical `ExerciseLog` (one-time job in `app.py` or migration script — spec §16). |
| 1.7 | Expand `BADGE_SEED` toward 70+ badges (spec §6 new rows). |
| 1.8 | Flag sample exercises with `is_hold` / `is_timed` where appropriate (spec §17). |

---

## 2. Core backend functions (all in `app.py`)

Implement in dependency order:

| Function | Purpose |
|----------|---------|
| `create_notification(...)` | Single write path for every notification (spec §4). |
| `calculate_set_volume(sets, reps_str, weight)` | Spec §16 — comma-separated reps. |
| `format_duration` / `format_time` | Spec §17 / §5. |
| `get_notif_emoji(notif_type)` | Spec §10 `NOTIF_EMOJI_MAP`. |
| `process_workout_log(user_id, workout_id, exercise_data_list, log_date)` | Spec §4 — full checklist §4 `process_workout_log` (items 1–14). |
| `process_daily_log(user_id, log_type, value=...)` | Spec §4 / §8. |
| `update_streak(user_id, streak_type)` | Spec §7 (freeze rules, milestones). |
| `check_badges(user_id, context)` | Spec §6 — return newly awarded for chaining. |
| `check_comeback_nudge(user_id)` | Spec §4 — schedule or login hook (decide one). |
| `sort_achievements(...)` | Spec §9. |
| Helpers | `get_best_active_streak`, `get_today_log_status`, totals for dashboard (spec §12.7). |

---

## 3. `process_workout_log` — internal checklist (must not skip)

For each completed workout submission, run **in a single transaction** after logs are saved:

1. Rep-max PR checks (1/3/5/8/10 RM) per set — spec §5A.  
2. Session volume per exercise vs `ExerciseVolumeTotal.best_session_volume` — §5C.  
3. Update `ExerciseVolumeTotal` (incremental) — §5B.  
4. Cumulative volume milestones per exercise (1k … 1M) — §5B.  
5. Total workout volume vs `WorkoutVolumeRecord` — §5D.  
6. Longest hold (`is_hold`, `duration_seconds`) — §5E.  
7. Fastest time (`is_timed`, `time_seconds`, lower better) — §5F.  
8. Max unbroken reps (bodyweight-style) — §5G.  
9. Exercise count milestones (1st, 10, 25, 50, 100, 150, 200) — §18 exercise milestones.  
10. Total rep milestones per exercise (100, 500, 1k, 5k, 10k) — §18.  
11. `check_badges` with `action: 'workout_logged'` (and PR counts if applicable).  
12. `update_streak` for `workout` (and any others spec requires).  
13. Time-of-day: `early_bird` / `night_owl` — spec §3 notif types.  
14. `week_started` / `weekly_target_hit` if rules defined (spec templates §18).  
15. **`workout_completed`** notification with **formatted total workout volume** — spec §18.  

**Product flow (explicit):** On successful POST from complete-workout flow, **redirect client to the notifications dashboard** (dedicated route or `/achievements` per spec — align with spec §9–10 “View All” / full feed). New rows must appear immediately (same request transaction).

---

## 4. Notification type coverage — verify each can be emitted

Use **exact** `notif_type` strings from spec §3 (expanded list). Before considering the build done, trace at least one code path per type:

| Type | Primary trigger |
|------|-----------------|
| `session_completed` | Existing trainer session complete (keep; add streak/badge hooks §12.2). |
| `streak_milestone` | `update_streak` at milestone days §7. |
| `pr_rep_max` | Rep-max PR §5A. |
| `pr_volume_exercise` | Best session volume §5C (emoji map name). |
| `pr_volume_workout` | Best total workout volume §5D. |
| `pr_volume_milestone` | Cumulative exercise volume crosses threshold §5B. |
| `pr_longest_hold` | Hold PR §5E. |
| `pr_fastest_time` | Timed PR §5F. |
| `pr_max_reps` | §5G. |
| `badge_earned` | `check_badges`. |
| `streak_started` / `streak_broken` | Streak lifecycle §7. |
| `comeback_nudge` | Inactivity §4. |
| `workout_completed` | End of `process_workout_log` §18. |
| `exercise_first` | First time exercise §18. |
| `exercise_count_milestone` | Nth session on movement §18. |
| `exercise_rep_milestone` | Cumulative reps §18. |
| `weekly_target_hit` | Weekly frequency §18. |
| `consistency_badge` | Multi-week consistency badges if used §6. |
| `daily_log_*` | Daily log routes §8. |
| `early_bird` / `night_owl` | Time of workout §3. |
| `week_started` | First workout of week §18. |
| `month_summary` | Month-end job or cron (define explicitly). |

**Fun / copy:** Use spec §18 templates; optional short variant lines are OK if tone stays positive and on-brand.

---

## 5. Integration hooks (spec §12)

| Hook | Action |
|------|--------|
| Workout log POST | Call `process_workout_log`; redirect to notifications dashboard. |
| Session status POST | Keep notifications; add `update_streak`, `check_badges`. |
| Complete week POST | Streak + badge per affected client. |
| Progress log POST | Weigh-in → `process_daily_log`. |
| Exercise-only log POST | PR path via `process_workout_log` or shared helper. |
| New daily-log POSTs | `process_daily_log` each. |
| Dashboard GET | Extra context §12.7. |
| `base.html` | Nav: Achievements + optional unread indicator. |

---

## 6. Routes & templates to add or change

**New routes:** §18 Summary — `/achievements`, `/leaderboard`, `/daily-log` + four POST endpoints, `POST /notifications/mark-all-read`, `GET /api/notifications/recent`, `GET /api/daily-log/today`.

**New templates:** `achievements.html`, `daily_log.html` (spec §9–§8).

**Modified:** `dashboard_client.html` (widget + notification panel + View All), `workout_log.html` (hold/timed inputs §17), `base.html` (nav).

**CSS:** Append to `style.css` only — §11 tokens, §10 notif colors, achievement cards, daily log, leaderboard (spec §14–§15).

---

## 7. API / UX — notifications

- Keep `POST /notifications/dismiss/<id>` and `GET /api/notifications/unread-count`.  
- Add mark-all-read and recent JSON (spec §10).  
- Full feed on achievements page; dashboard shows recent + link.  
- No floating toasts (spec §14).

---

## 8. Quality analysis gate (pre-merge checklist)

Run through as **QA agent** before shipping:

- [ ] Volume: unit test or manual case — single rep string, comma reps, multi-set same reps — matches §16.  
- [ ] One heavy set creates multiple RM notifications when applicable §5A.  
- [ ] `ExerciseVolumeTotal` updates once per exercise per workout; milestones fire only on **cross** (old &lt; M &lt;= new).  
- [ ] Timed vs hold: higher-is-better vs lower-is-better queries correct.  
- [ ] Every `notif_type` in §3 has a documented trigger (table in §4 above).  
- [ ] CSRF on all new POSTs; `@login_required`; client-only where required.  
- [ ] Leaderboard: no weight, strength, or body comparisons §15.  
- [ ] Complete workout → redirect lands on feed with new items visible.  
- [ ] PostgreSQL + SQLite both work (URI fix for postgres already in app).  

---

## 9. Suggested Claude Code task order

1. Migrations / models / backfill.  
2. `create_notification` + volume helpers + `process_workout_log` skeleton → fill all branches.  
3. Wire workout POST + redirect.  
4. Streaks + badges + daily log routes.  
5. Achievements + notification feed UI + CSS.  
6. Leaderboard.  
7. QA pass §8.  

---

## 10. Agent family (roles for Claude Code subtasks)

Use these as separate prompts or sub-agents; each must re-open `CURSOR_AGENT_SPEC.md` for details.

| Role | Responsibility |
|------|----------------|
| **Orchestrator** | Ordering §9; ensures no duplicate `notif_type` gaps. |
| **Schema & migration** | §1 only; backfill volume totals. |
| **Workout engine** | `process_workout_log` §3–§4 + §16–§17. |
| **PR & volume math** | §5, §16; edge cases for reps strings. |
| **Streaks & badges** | §6–§7; seed data. |
| **Daily logging** | §8 routes + `process_daily_log`. |
| **UI / templates / CSS** | §9–§11, §10 feed; workout form hold/timed. |
| **Quality analysis** | §8 checklist; privacy and CSRF audit. |

---

**End of plan.** Implementation belongs in Claude Code against `CURSOR_AGENT_SPEC.md` + this ordering document.
