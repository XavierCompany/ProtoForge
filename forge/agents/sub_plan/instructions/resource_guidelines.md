# Resource Planning Guidelines

## Minimum-Viable-Resource Principle

When planning prerequisite resources, follow these guidelines:

1. **Free tiers first** — Always prefer free or dev-tier SKUs (e.g. Azure Free Tier, Cosmos DB Free Tier, App Service F1).
2. **Emulators over cloud** — If a local emulator exists (Cosmos DB Emulator, Azurite, LocalStack), prefer it for development and demos.
3. **Shared over dedicated** — Reuse existing resource groups and subscriptions rather than creating new ones.
4. **Defaults over custom** — Use default configurations; only customise when the demo requires it.
5. **One region** — Deploy everything in a single region to avoid cross-region complexity and latency.

## What Counts as a "Resource"

- Cloud services (compute, storage, databases, queues)
- Connectors (workspace connectors, API integrations, webhooks)
- Authentication artefacts (app registrations, service principals, API keys)
- Data prerequisites (seed data, schemas, sample files)
- Network configuration (endpoints, DNS, firewall rules)

## What Does NOT Count

- Code changes (that's the task agents' job)
- Testing (that's downstream)
- Documentation (that's the knowledge base agent)

## Output Expectations

The sub-plan should be specific enough that a human reviewer can:
- Understand *exactly* what will be created
- Estimate cost and time
- Accept, modify, or reject each resource individually
