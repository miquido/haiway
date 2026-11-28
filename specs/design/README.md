# Technical design specifications

## Rules

- **Location and names.** One file per specification in this directory, named
  `TDS-NNN-<short-name>.md`: `NNN` is three digits, the next free number here, never reused;
  `<short-name>` is lower-case words joined by `-`.
- **Identity.** The first line is `# TDS-NNN — <Title>`. The document's ID is `TDS-NNN`; its
  requirements are cited from outside it as `TDS-NNN FR-1`, its open questions as `TDS-NNN OQ-1`.
  Specifications of other repositories are cited with the repository name first, for example
  `draive TDS-004 HM-1`.
- **Status.** A `Status:` line under the title with one value: `draft`, `proposed`, `accepted`,
  `implemented`, `superseded by <ID>` or `withdrawn`. A specification that replaces another says
  `Supersedes <ID>`, and the old one's status changes in the same change.
- **Grounding.** A `Grounded at:` line names the commit the cited `path:line` references were read
  at. Citations are a snapshot and go stale when the code moves.
- **Structure.** Header, Purpose, Normative language, Glossary, Decisions, Context and scope,
  Components and responsibilities, Interfaces, Data model, Behavior requirements, Security and
  failure modes, Compatibility and migration, Coverage, Open questions, Unverified, Traceability,
  References. A section that cannot apply says so in one line.
- **Requirements.** `FR-n` (functional) and `NFR-n` (non-functional), each its own `###` heading
  `FR-n — <Title>` with the labelled blocks `**Statement:**`, `**Rationale:**`, `**Acceptance:**`,
  `**Source:**`, then `**Depends on:**` when it has dependencies. IDs are never renumbered; a
  removed requirement stays as a heading marked withdrawn.
- **Open questions.** `OQ-n`, each with header, question, why it matters, options with their
  consequences and a recommendation only when cited facts favor one. An answered question stays in
  place, marked resolved with a link to its `D-n` decision.
- **Links.** Relative links only, to files and heading anchors that exist (GitHub anchor rules).
- **Formatting.** Markdown wrapped at 100 columns, checked with
  `uv run --frozen mdformat --wrap 100 --check specs` and
  `uv run --frozen pymarkdown --config .pymarkdown.json scan specs`.

## Index

| Document                                     | Title                 | Status   | Summary                                                                                                                                                                                                                    |
| -------------------------------------------- | --------------------- | -------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [TDS-001](TDS-001-managed-tasks.md)          | Managed tasks         | accepted | One runtime for registered tasks spawned out of their call context: fresh context per attempt, retries, leases, a swappable task store, task listing, opt-in failing of unsupported tasks; removes `ctx.spawn_background`. |
| [TDS-002](TDS-002-postgres-managed-tasks.md) | PostgreSQL task store | accepted | PostgreSQL task store: stored tasks and attempts, leases, shared work queue, listing and rejection statements, own migrations and schema check.                                                                            |
