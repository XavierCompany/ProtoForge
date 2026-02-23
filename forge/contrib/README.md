# Contributing to the ProtoForge Forge Ecosystem

This directory holds **dynamic contributions** — custom agents, prompts, skills,
and workflows added by teams or individuals beyond the built-in set.

## Directory Layout
```
contrib/
  agents/         # Custom agent packages (same structure as forge/agents/<name>/)
  prompts/        # One-off prompt files (.md)
  skills/         # Custom skill definitions (.yaml)
  workflows/      # Custom workflow definitions (.yaml)
  audit_log.yaml  # Automatic audit trail of all changes
```

## Adding a Custom Agent
1. Create a folder: `contrib/agents/<your_agent>/`
2. Add `agent.yaml` (required) with id, name, type, description, skills, prompts
3. Add `prompts/system.md` (required) — the agent's system prompt
4. Add `skills/<skill>.yaml` and `instructions/<doc>.md` as needed
5. The loader auto-discovers your agent on next startup

## Adding a Custom Skill
1. Create `contrib/skills/<skill_name>.yaml`
2. Follow the schema: name, description, agent_type, version, parameters, examples
3. Reference it from an existing or custom agent's `agent.yaml`

## Adding a Custom Prompt
1. Create `contrib/prompts/<prompt_name>.md`
2. Reference it from an agent's `agent.yaml` prompts list

## Adding a Custom Workflow
1. Create `contrib/workflows/<workflow_name>.yaml`
2. Follow the schema: name, description, steps with agent_type, prompt_template, depends_on

## Validation
All contributions are validated on load:
- `agent.yaml` must have: id, name, type, description
- Skills must have: name, description, parameters
- Workflows must have: name, steps (each with agent_type)
- IDs must be unique across built-in + contrib

## Audit Trail
Every create/update/delete is logged in `audit_log.yaml` with:
- timestamp, action, path, author, description
