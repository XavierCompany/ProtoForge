# LLM Instruction Files

This folder contains **IDE-specific instruction files** that teach AI coding
assistants about the ProtoForge codebase. Each file targets a different tool
but carries the same core information.

## How LLM Instruction Files Work

Different AI coding tools look for project-specific instructions in different
places:

| Tool | File it reads | Location required |
|---|---|---|
| **GitHub Copilot** | `.github/copilot-instructions.md` | Must be at `.github/` |
| **Cursor IDE** | `.cursorrules` | Must be at repo root |
| **Claude Code** | `CLAUDE.md` | Must be at repo root |
| **Windsurf / Codeium** | `.windsurfrules` | Must be at repo root |

## File Layout

```
llm-instructions/
  README.md              ← You are here
  cursorrules.md         ← Full Cursor instructions (source of truth)
  claude.md              ← Full Claude Code instructions (source of truth)
  windsurfrules.md       ← Full Windsurf instructions (source of truth)
```

The **repo-root files** (`.cursorrules`, `CLAUDE.md`, `.windsurfrules`) are
thin pointers that include a one-line project description and redirect the
LLM to read `.github/copilot-instructions.md` + the full documentation set.

## Canonical Source

**`.github/copilot-instructions.md`** is the canonical LLM instruction file.
All other variants derive from it. If they diverge, `copilot-instructions.md`
wins.

## How to Update

1. Edit `.github/copilot-instructions.md` (the canonical source)
2. Update the variants in this folder if any IDE-specific content changed
3. The root pointer files (`.cursorrules`, `CLAUDE.md`, `.windsurfrules`)
   rarely need changes — they just point to the docs

## How to Add a New Tool

1. Find out which filename + location the tool looks for
2. Create a thin pointer file at that location (see existing root files)
3. Create a full-content variant in this folder
4. Add it to the table above
