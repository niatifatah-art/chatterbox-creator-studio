# ADR 0002 — Data versioning before storage migration

Status: Accepted for the Voice Studio completion pass.

## Context

The working product already stores small creator state in local JSON/files and uses temporary-file replacement for several writes. The new Speech Core introduces additional voice-profile/artifact/job concepts. It would be possible to migrate all state to SQLite immediately, but doing that at the same time as the synthesis/Core migration would combine two independent high-risk changes.

The project also needs to remain portable and easy to revisit after long gaps.

## Decision

For the current completion phases:

1. **Keep existing JSON/file stores behind store classes.** Do not let new UI code write ad-hoc files directly.
2. **Version every persisted schema.** A reader either understands the version, migrates it, or refuses safely.
3. **Backup before destructive migration.** Migration writes/validates a new representation before the old data is removed or replaced.
4. **Make migrations idempotent.** Re-running startup after interruption must not duplicate/delete creator assets.
5. **Keep media as ordinary files.** WAV/reference/preview/export assets are not database blobs.
6. **Keep portable export separate from internal state.** Export remains inspectable JSON + media so a future internal database does not lock creator data to one implementation.
7. **Re-evaluate SQLite only when durable jobs/history/concurrent access provide a measured benefit.** If adopted, SQLite stores metadata/index/job state while media stays on disk.

## Why not SQLite immediately?

SQLite is a strong candidate for later durable job/history state, and its WAL/backup facilities are useful for a desktop application. The current problem, however, is not database throughput or concurrent writers; it is architectural duplication between the working legacy stores and the new Speech Core contracts. Adding a database now would not remove that duplication and would make parity/migration harder to isolate.

## Consequences

Positive:

- synthesis migration can be tested independently from a storage-engine migration;
- current users keep the working on-disk layout during the highest-risk transition;
- backups/migrations become explicit before schema complexity grows;
- adopting SQLite later remains possible behind the same store interfaces.

Trade-offs:

- JSON/file state remains less convenient for future querying/concurrent durable jobs;
- some temporary duplication exists until the Voice Profile migration finishes;
- we must add migration discipline now rather than relying on a database migration framework.

## Trigger to revisit

Revisit this ADR when at least one of the following becomes true:

- jobs must survive application restarts and be queried/updated concurrently;
- segment/take history creates meaningful cross-file consistency problems;
- schema migration/testing becomes materially harder than a transactional database migration;
- multiple local clients need concurrent read/write access to the same state.
