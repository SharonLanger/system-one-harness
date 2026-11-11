# Local session state

Session state remembers which method a flow has already enriched. A shared
compaction marker allows later requests to enrich again. It is separate from
[diagnostic logs](log-viewer/DATA_MODEL.md), which are searched in the log viewer.

## Location and initialization

SOH persists ordered event metadata using Python's standard-library `sqlite3` in
`<SOH root>/.soh/session-state.sqlite3`. The root is the installed package/checkout
root, independent of the target TypeScript project, hook cwd and shell cwd. Each
installation has one database shared by sessions. Reusing a session ID in that
database addresses the same session. The store initializes lazily; a read-only
installation produces the existing storage-error behavior without fallback.

Existing JSONL state is neither read, imported, rewritten nor deleted. The new
store begins empty. No dual writes, retention, pruning, scheduled vacuum, schema
migration framework, payload version or automatic database size cap is included.
Node remains required for TypeScript source analysis, while session persistence
runs entirely in Python. A keyword-only absolute `database_path` override exists
for embedding and isolated tests, without a new CLI configuration setting.

## Stored events

Rows retain session, originating hook, optional producing flow, event type,
ordered event ID, UTC millisecond timestamp and event-specific JSON. A flow reads
its own events plus shared PostCompact boundaries in event order. Each valid
manual/automatic PostCompact handler invocation writes one shared empty-payload
row. Repeated invocations can write repeated boundaries. Current enrichment
payloads contain selected method metadata, never whole requests, prompts,
transcripts, source bodies or model answers. JSON payloads must be objects and
fit in 65,536 UTF-8 bytes; larger payloads fail without truncation.

## Transactions and failure behavior

Read, deterministic decision and optional append execute in one `BEGIN IMMEDIATE`
transaction. Connections close after each operation. Writer waits and SQL work
respect the hook deadline, with rollback journaling and `synchronous=FULL`.
Ordinary enrichment storage errors preserve prepared context; timeouts propagate
to the existing gateway. The row is recorded before gateway emission, so it does
not prove delivery to Claude. A killed process may leave committed state without
output. An uncertain commit is never automatically replayed.

## Permissions and sensitive data

The database is sensitive local runtime data. New state directories use owner-only
permissions and new databases owner read/write permissions on supported POSIX
systems, without changing the process umask. These permissions and ignore rules
are not encryption. A symlinked database or directory is rejected, as is an
incompatible existing schema; the store does not recreate or relocate it.
Embedding callers using OS path aliases should supply a canonical absolute parent
path and a regular database filename.

Never commit, merge, or upload `.soh/`, database files, or SQLite journal/WAL/shm
sidecars. Repository ignore rules and hygiene checks cover these paths and ordinary
SQLite headers, including force-added blobs. See [contributing](../CONTRIBUTING.md)
for staged/history checks and preventive Git hooks. Only test XML reports should
be CI artifacts.

Header checks cover ordinary unencrypted SQLite databases, not every encrypted,
compressed, or renamed standalone sidecar. If real state is pushed, stop merging
and follow [the private security process](../SECURITY.md); do not post its contents
in issues, test reports, or review comments.
