# Commit & Issue Documentation Conventions

## Conventional Commits Format

Every generated commit message must follow:

```
type(scope): short description

[optional body]

[optional footer(s)]
```

### Types
- `feat` — New user-facing feature
- `fix` — Bug fix
- `perf` — Performance improvement (no functional change)
- `refactor` — Code restructuring (no functional change)
- `docs` — Documentation only
- `test` — Adding or updating tests
- `ci` — CI/CD configuration changes
- `chore` — Dependency updates, tooling, maintenance
- `style` — Formatting, linting (no logic change)

### Scope
Use the module or area affected: `router`, `engine`, `agents`, `forge`, `mcp`, `server`, `tests`.

### Breaking Changes
Append `!` after type/scope: `feat(api)!: remove deprecated /v1 endpoints`
Add `BREAKING CHANGE: <description>` in the footer.

## Issue Documentation Standards

### Issue Title
- Follow pattern: `[Type] Concise description`
- Examples: `[Feature] Add GitHub commit documentation agent`, `[Fix] Router scoring tiebreak returns wrong agent`

### Issue Body
1. **Summary** — One paragraph describing the change (2-4 sentences).
2. **Motivation** — Why this change was needed.
3. **Changes Made** — Bulleted list of specific modifications with file paths.
4. **Testing** — How the change was verified.
5. **Related** — Links to related issues, PRs, or commits.

### Labels
Auto-apply based on commit type:
- `feat` → `enhancement`
- `fix` → `bug`
- `perf` → `performance`
- `refactor` → `refactor`
- `docs` → `documentation`
- `test` → `testing`
- `ci` → `ci/cd`
- `chore` → `maintenance`

## Changelog Generation

Group entries by release and category:

```markdown
## [v0.2.0] - 2026-02-23

### Features
- Add GitHub Tracker Agent for commit documentation (#42)

### Fixes
- Fix router scoring when WorkIQ hints produce ties (#38)

### Improvements
- Expand ruff lint rules from 9 to 20 categories (#40)
```

## Cross-Referencing Rules

1. Every commit message should reference an issue: `fix(router): correct tiebreak logic (#38)`
2. Every issue should link to the commit that resolves it
3. PRs should list all issues they close: `Closes #38, #39`
4. Changelog entries should link to both the PR and issue
