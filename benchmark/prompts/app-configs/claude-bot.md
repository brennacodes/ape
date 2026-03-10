# Claude Bot

## Unsurfaced Bugs

| # | Bug | Location | Impact |
|---|-----|----------|--------|
| 1 | **Hardcoded CLI path** — `spawn('/Users/brenna/.local/bin/claude')` makes the bot non-functional on any other machine | `run-claude.js:25`, `sessions.js:8` | App won't start elsewhere |
| 2 | **Unhandled async rejections** — `close` handler is `async` but `message.reply()` calls aren't guarded; a single Discord API failure crashes the handler | `run-claude.js:44-87` | Silent bot crash, lost session state |
| 3 | **Race condition in permission collector** — `collect` and `end` events can both call `done()`, potentially resolving a permission decision twice | `dm.js:61-68` | Duplicate/conflicting permission responses |

## Untested Code (zero test files exist)

| # | Code Path | Location | Risk |
|---|-----------|----------|------|
| 1 | **JSON parsing + session state storage** — malformed Claude CLI output silently stores invalid session IDs | `run-claude.js:51-68` | Corrupted session state, cascading failures |
| 2 | **File diff logic in todo-watcher** — uses `task.content` as identity key; deleted tasks are never detected; debounce creates race windows | `todo-watcher.js:35-59` | Silent task loss, cache corruption |
| 3 | **Bridge server request handling** — no body size limit (unbounded memory), no payload schema validation, case-sensitive header auth on a case-insensitive protocol | `bridge-server.js:38-65` | DoS via large payloads, auth bypass risk |

## Poor Architectural Choices

| # | Issue | Locations | Risk |
|---|-------|-----------|------|
| 1 | **Hardcoded absolute paths** instead of using `PATH` resolution or config | `run-claude.js:25`, `setup.sh:14` | Zero portability, deployment impossible |
| 2 | **Global mutable state without contracts** — `activeSessions`, `dmChannel`, `activeCollectors`, and `cache` are all module-level mutable state with no synchronization or lifecycle management | `index.js:8`, `dm.js:4-5`, `todo-watcher.js:7` | Concurrency bugs, untestable, implicit ordering |
| 3 | **Fragmented permission flow** — permission logic spans 3 files (hook script, bridge server, DM module) with misaligned timeouts (660s vs configurable vs Discord collector) and no shared domain model | `bridge-server.js`, `dm.js`, `hooks/permission-request.js` | Hard to extend, timeout confusion, violates Open/Closed |

## Improvements & Feature Expansion

| # | Opportunity | Impact |
|---|-------------|--------|
| 1 | **Session persistence** — store sessions to disk so they survive bot restarts; recover from `~/.claude/sessions/` on startup | Eliminates data loss on crash/restart |
| 2 | **Structured error handling** — distinguish transient vs permanent failures, add retry logic, surface stderr details to users instead of generic messages | Prevents silent failures, improves debuggability |
| 3 | **Argument parsing & command extensibility** — replace single-string args with structured parsing to support flags, per-task config overrides, and task queuing | Unlocks advanced workflows without code changes |
