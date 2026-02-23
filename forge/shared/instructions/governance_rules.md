# Governance Rules — Always-On Enforcement

These rules are **always active** and enforced at every stage of the
orchestration pipeline.  They cannot be overridden by individual agents.

---

## 1. Context Window Governance

| Threshold | Action |
|-----------|--------|
| 0 – 120 000 tokens | ✅ Normal operation — no intervention |
| 120 001 – 127 999 tokens | ⚠️ **Warning** — HITL triggered. Human decides whether to decompose the task and spawn a new sub-agent to keep the window healthy. |
| ≥ 128 000 tokens | 🛑 **Hard cap** — execution halted. Task MUST be decomposed before proceeding. |

### Rules

1. The **total context window** for any single orchestration run (all
   agents combined) MUST stay below **128 000 tokens**.
2. When the cumulative token count crosses **120 000 tokens**, the
   Governance Guardian triggers a **Human-in-the-Loop** review:
   - The system presents a breakdown of token usage per agent.
   - The system suggests decomposing the current task and creating a
     new sub-agent to handle the remaining work in a fresh context.
   - The human may **accept** the suggestion (task is split, new
     sub-agent spawned) or **reject** it (execution continues at the
     operator's risk).
3. If a single **agent's input payload** exceeds its per-agent budget,
   the existing truncation strategies (priority / sliding_window /
   summarize) apply automatically.

---

## 2. Skill Cap Governance

| Condition | Action |
|-----------|--------|
| ≤ 4 skills per agent | ✅ Normal — no intervention |
| > 4 skills requested | ⚠️ **HITL triggered** — human reviews a suggestion to create a custom sub-agent that carries the overflow skills |

### Rules

1. Each agent is limited to **4 skills maximum**.
2. When a forge manifest (`agent.yaml`) declares more than 4 skills,
   the Governance Guardian flags the violation at **load time**.
3. A HITL review is created with:
   - The agent's current skill list.
   - A recommended split: keep the 4 highest-priority skills on the
     parent agent, move the rest to a new custom sub-agent.
   - The human may **accept** the split, **customise** which skills
     stay vs. move, or **override** (acknowledge the violation and
     proceed with > 4 skills).

---

## 3. Architectural Principle Enforcement

The following separation of concerns MUST be maintained:

| Component | Responsibility |
|-----------|---------------|
| **Agent** | Handles a complete **task** — receives a goal, coordinates its skills, returns a result. |
| **Skill** | Provides a single, reusable **capability** — a tool the agent invokes (e.g. "search code", "parse logs"). |
| **Sub-agent** | Handles **isolated, context-heavy work** — runs in its own context window so the parent agent doesn't overflow. |

### Rules

1. An agent MUST NOT embed business logic that belongs in a skill.
   If an agent's `execute()` method contains tool-like logic (parsing,
   API calls, data transforms), it should be extracted into a skill.
2. Context-heavy tasks (large document processing, multi-file analysis,
   heavy data crunching) MUST be delegated to a **sub-agent** rather
   than processed inline by the parent agent.
3. Sub-agents inherit the same governance rules — they are also
   subject to the 128 K context cap and the 4-skill limit.

---

## Enforcement Points

| Stage | Check |
|-------|-------|
| **Manifest load** (`ForgeLoader`) | Skill count ≤ 4 per agent |
| **Pre-dispatch** (`OrchestratorEngine._dispatch`) | Cumulative token count < 120 K warning / 128 K hard cap |
| **Post-dispatch** | Token usage recorded; budget report updated |
| **Fan-out** (`OrchestratorEngine._fan_out`) | Each parallel agent checked before execution |

---

*These rules are loaded by the ForgeLoader as a shared instruction and
injected into every agent's system prompt context automatically.*
