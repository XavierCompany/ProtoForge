# Work IQ Usage Guide

## Prerequisites

- `workiq` CLI installed globally: `npm install -g @microsoft/workiq`
- EULA accepted: `workiq accept-eula`
- Valid Microsoft 365 / Entra ID sign-in

## How It Works

1. The orchestrator routes questions about people, calendar, email, etc.
   to the Work IQ Agent.
2. The agent calls `workiq ask -q "<question>"` as a subprocess.
3. The response is split into logical sections.
4. **Human-in-the-loop**: the user selects which sections to inject
   into the orchestrator pipeline.
5. Selected sections become enrichment context for downstream agents.

## Routing Keywords

Questions containing these terms are routed to Work IQ:

- People: manager, report, org chart, team member, contact
- Calendar: meeting, calendar, schedule, free, busy
- Email: email, inbox, thread, message
- Documents: document, file, sharepoint, onedrive
- Teams: teams, channel, chat
- General: workiq, work iq, m365, microsoft 365

## Privacy

- All Work IQ data goes through human selection before use
- No data is cached beyond the current session
- Respects Microsoft 365 access controls (user can only see their own data)
