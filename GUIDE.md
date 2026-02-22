# ProtoForge — Developer Guide

A comprehensive guide covering architecture rationale, extending agent capabilities, and leveraging GitHub Copilot CLI for AI-powered code review workflows.

---

## Table of Contents

1. [Why This Architecture?](#why-this-architecture)
2. [Plan-First Design: The Reasoning](#plan-first-design-the-reasoning)
3. [Expanding Plan Agent Capabilities](#expanding-plan-agent-capabilities)
4. [Expanding Sub-Agent Capabilities](#expanding-sub-agent-capabilities)
5. [Adding a Brand-New Agent](#adding-a-brand-new-agent)
6. [Adding New Skills & Workflows](#adding-new-skills--workflows)
7. [Extending the Codebase with GitHub Copilot CLI](#extending-the-codebase-with-github-copilot-cli)
8. [Multi-Model Code Review Workflow](#multi-model-code-review-workflow-copilot-cli--claude-opus-46--codex-53)
9. [Architecture Decision Records](#architecture-decision-records)

---

## Why This Architecture?

### The Problem with Flat Multi-Agent Systems

Most multi-agent systems use a flat architecture where a router directly dispatches to a single specialist agent:

```
User → Router → Agent → Response    ← flat, no coordination
```

This breaks down when:
- A request requires **multiple agents** (e.g., "fix the crash in the auth module" needs log analysis + code research + remediation)
- There's **no strategic plan** — each agent works in isolation without shared context
- **Sequencing matters** — you need to analyze logs *before* writing a fix
- **Quality degrades** — no coordinator verifies the overall approach

### Why Plan-First Solves This

ProtoForge uses a **Plan-First** architecture — every request goes through the Plan Agent before any sub-agent executes:

```
User → Orchestrator → Plan Agent (ALWAYS first)
                         ↓
                    Sub-Agents (parallel fan-out)
                         ↓
                    Aggregated Response
```

**Benefits:**

| Benefit | How Plan-First Delivers It |
|---------|---------------------------|
| **Strategic consistency** | Plan Agent produces a step-by-step strategy before execution |
| **Multi-agent coordination** | Plan Agent identifies which sub-agents to invoke and why |
| **Shared context** | Plan output is stored in working memory — sub-agents can reference it |
| **Quality gate** | Plan serves as a top-level review before work begins |
| **Parallel execution** | Once planned, sub-agents run concurrently for speed |
| **Observability** | Every response shows the plan + individual agent outputs |

### Why Semantic Kernel?

We chose **Semantic Kernel (Python)** over AutoGen, LangGraph, and CrewAI because:

- **Plugin architecture** — agents, skills, and connectors are composable plugins
- **Native multi-LLM** — switch between Azure OpenAI, OpenAI, Anthropic, Google without code changes
- **Enterprise-grade** — built by Microsoft, production-tested, strong Azure integration
- **Minimal abstraction tax** — thin wrapper over LLM calls, not a heavy framework
- **MCP-compatible** — skills map naturally to MCP tools for cross-tool interop

---

## Plan-First Design: The Reasoning

### How `engine.py` Works

The orchestrator pipeline in `src/orchestrator/engine.py` follows this flow:

```python
async def process(self, user_message: str) -> str:
    # 1. Route intent — keyword patterns identify target agent types
    routing = self._router.route_by_keywords(user_message)

    # 2. Low confidence? Try LLM routing
    if routing.confidence < 0.5:
        llm_routing = await self._route_with_llm(user_message)

    # 3. ALWAYS run Plan Agent first
    plan_result = await self._dispatch(AgentType.PLAN, user_message, routing)

    # 4. Store plan in working memory for sub-agents
    self._context.set_memory("plan_output", plan_result.content)
    self._context.set_memory("plan_artifacts", plan_result.artifacts)

    # 5. Resolve which sub-agents to invoke (excludes PLAN)
    sub_agents = self._resolve_sub_agents(routing)

    # 6. Fan out sub-agents in parallel
    sub_results = await self._fan_out(sub_agents, user_message, routing)

    # 7. Aggregate Plan + sub-agent results
    return self._aggregate(plan_result, sub_results)
```

### Why Plan Agent Is Always First

The Plan Agent (`src/agents/plan_agent.py`) acts as a **strategic coordinator**:

1. **Analyzes scope** — understands what the user is really asking
2. **Decomposes** — breaks complex requests into ordered steps
3. **Routes** — recommends which sub-agents should execute (and why)
4. **Provides context** — downstream sub-agents can read the plan from working memory
5. **Sets success criteria** — defines what "done" looks like

This means even a simple request like *"check the logs"* gets a plan that identifies:
- What to look for in the logs
- Whether other agents might help (e.g., code_research for stack traces)
- What success looks like

---

## Expanding Plan Agent Capabilities

### 1. Wire Up Real LLM Calls

The Plan Agent currently uses a structured placeholder. To wire it to a real LLM:

```python
# src/agents/plan_agent.py — inside execute()

from semantic_kernel import Kernel
from semantic_kernel.connectors.ai.open_ai import AzureChatCompletion

async def execute(self, message, context, params=None):
    kernel = Kernel()
    kernel.add_service(AzureChatCompletion(
        deployment_name="gpt-5.3-codex",  # or claude-opus-4.6
        endpoint=settings.llm.azure_endpoint,
    ))

    messages = self._build_messages(message, context)

    # Use Semantic Kernel to get the LLM response
    result = await kernel.invoke_prompt(
        prompt=messages[-1]["content"],
        system_message=self._system_prompt,
    )

    # Parse the plan from the LLM output
    plan_text = str(result)
    recommended = self._identify_sub_agents(message, params)

    return AgentResult(
        agent_id=self.agent_id,
        content=plan_text,
        confidence=0.9,
        artifacts={"recommended_sub_agents": recommended},
    )
```

### 2. Add Plan Memory & Learning

Make the Plan Agent learn from past plans:

```python
# In plan_agent.py — add to execute()

# Check if similar plans exist in memory
past_plans = context.get_memory("past_plans") or []
similar = [p for p in past_plans if self._similarity(p["query"], message) > 0.8]

if similar:
    # Reference prior successful plans
    plan_prompt += f"\n\nPrevious similar plan:\n{similar[0]['plan']}"

# After generating the plan, store it
past_plans.append({"query": message, "plan": plan_response})
context.set_memory("past_plans", past_plans[-20:])  # keep last 20
```

### 3. Add Plan Validation & Self-Critique

Add a self-critique step so the Plan Agent reviews its own output:

```python
# After generating the initial plan
critique_prompt = (
    f"Review this plan for completeness and risks:\n{plan_response}\n\n"
    f"Is anything missing? Are the steps in the right order? "
    f"Are the right sub-agents selected?"
)
critique = await kernel.invoke_prompt(prompt=critique_prompt)

# Refine the plan based on critique
if "missing" in str(critique).lower() or "risk" in str(critique).lower():
    plan_response = await self._refine_plan(plan_response, str(critique))
```

### 4. Add Confidence Scoring from LLM

Instead of a fixed confidence, let the LLM self-assess:

```python
confidence_prompt = (
    f"Rate your confidence in this plan from 0.0 to 1.0:\n{plan_response}\n"
    f"Return ONLY a number."
)
confidence = float(await kernel.invoke_prompt(prompt=confidence_prompt))
```

---

## Expanding Sub-Agent Capabilities

### Pattern for All Sub-Agents

Every sub-agent inherits from `BaseAgent` (`src/agents/base.py`). To expand any sub-agent:

```python
class MyAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            agent_id="my_agent",
            description="What this agent does",
            system_prompt="You are an expert in...",
        )

    async def execute(self, message, context, params=None):
        # 1. Read plan context (from Plan Agent's working memory)
        plan = context.get_memory("plan_output")

        # 2. Build messages with plan context
        messages = self._build_messages(message, context)
        if plan:
            messages.insert(1, {
                "role": "system",
                "content": f"The Plan Agent provided this context:\n{plan}"
            })

        # 3. Call LLM, process, return AgentResult
        ...
```

### Example: Expanding Log Analysis Agent

Add structured log parsing and pattern detection:

```python
# src/agents/log_analysis_agent.py

import re
from collections import Counter

class LogAnalysisAgent(BaseAgent):
    """Enhanced log analysis with pattern detection."""

    # Add new capabilities
    ERROR_PATTERNS = {
        "null_pointer": r"NullPointerException|NoneType.*attribute",
        "timeout": r"TimeoutError|deadline exceeded|ETIMEDOUT",
        "auth_failure": r"401|403|Unauthorized|Forbidden|AuthenticationError",
        "oom": r"OutOfMemoryError|OOM|Cannot allocate memory",
        "connection": r"ConnectionRefused|ECONNREFUSED|Connection reset",
    }

    async def execute(self, message, context, params=None):
        # Classify the error type from log content
        detected_patterns = {}
        for name, pattern in self.ERROR_PATTERNS.items():
            if re.search(pattern, message, re.IGNORECASE):
                detected_patterns[name] = True

        # Include pattern detection in artifacts
        return AgentResult(
            agent_id=self.agent_id,
            content=f"Log analysis found: {list(detected_patterns.keys())}",
            confidence=0.9 if detected_patterns else 0.5,
            artifacts={
                "detected_patterns": detected_patterns,
                "severity": self._assess_severity(detected_patterns),
            },
        )

    def _assess_severity(self, patterns: dict) -> str:
        if "oom" in patterns or "null_pointer" in patterns:
            return "critical"
        if "auth_failure" in patterns:
            return "high"
        if "timeout" in patterns or "connection" in patterns:
            return "medium"
        return "low"
```

### Example: Expanding Code Research Agent

Add AST-based code analysis:

```python
# src/agents/code_research_agent.py

import ast

class CodeResearchAgent(BaseAgent):
    """Enhanced code research with AST analysis."""

    async def execute(self, message, context, params=None):
        # If code is provided, do structural analysis
        code_block = params.get("code") if params else None

        if code_block:
            analysis = self._analyze_code(code_block)
            return AgentResult(
                agent_id=self.agent_id,
                content=f"Code structure:\n{analysis}",
                confidence=0.9,
                artifacts={"ast_analysis": analysis},
            )

        # Otherwise, do keyword-based research
        ...

    def _analyze_code(self, code: str) -> dict:
        """Parse and analyze Python code structure."""
        try:
            tree = ast.parse(code)
            return {
                "functions": [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)],
                "classes": [n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)],
                "imports": [
                    n.names[0].name for n in ast.walk(tree) if isinstance(n, ast.Import)
                ],
                "complexity": sum(
                    1 for n in ast.walk(tree)
                    if isinstance(n, (ast.If, ast.For, ast.While, ast.Try))
                ),
            }
        except SyntaxError:
            return {"error": "Could not parse code"}
```

### Example: Adding Tool Use to Sub-Agents

Give agents the ability to call external tools:

```python
# Add to any agent
from semantic_kernel.functions import kernel_function

class RemediationAgent(BaseAgent):
    """Remediation agent with tool use."""

    @kernel_function(name="apply_patch", description="Apply a code patch")
    async def apply_patch(self, file_path: str, patch: str) -> str:
        """Tool that the LLM can invoke to apply a patch."""
        # Validate the patch
        # Apply it
        return f"Patch applied to {file_path}"

    @kernel_function(name="run_tests", description="Run the test suite")
    async def run_tests(self, test_path: str = "tests/") -> str:
        """Tool that the LLM can invoke to run tests."""
        import subprocess
        result = subprocess.run(["pytest", test_path, "-v"], capture_output=True, text=True)
        return result.stdout
```

---

## Adding a Brand-New Agent

To add a new agent (e.g., a **Performance Profiler** agent):

### Step 1: Create the Agent Class

```python
# src/agents/performance_agent.py
"""Performance Profiler Agent — identifies bottlenecks and optimization opportunities."""

from src.agents.base import BaseAgent
from src.orchestrator.context import AgentResult, ConversationContext

PERF_SYSTEM_PROMPT = """You are a Performance Profiler Agent.
You analyze code and systems for performance bottlenecks, memory leaks,
slow queries, and optimization opportunities.

Focus on: CPU profiling, memory profiling, I/O bottlenecks, database
query optimization, caching strategies, and concurrency issues."""


class PerformanceAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            agent_id="performance_agent",
            description="Performance profiling and optimization",
            system_prompt=PERF_SYSTEM_PROMPT,
        )

    async def execute(self, message, context, params=None):
        messages = self._build_messages(message, context)

        # Read plan context for coordinated execution
        plan = context.get_memory("plan_output")

        return AgentResult(
            agent_id=self.agent_id,
            content="Performance analysis: ...",
            confidence=0.85,
            artifacts={"metrics": {}, "recommendations": []},
        )
```

### Step 2: Register in the Router

```python
# src/orchestrator/router.py — add to AgentType enum
class AgentType(str, Enum):
    # ...existing agents...
    PERFORMANCE = "performance"

# Add keyword patterns
KEYWORD_ROUTES[AgentType.PERFORMANCE] = [
    r"\bperformance\b", r"\bprofile\b", r"\bbottleneck\b",
    r"\bslow\b", r"\blatency\b", r"\bmemory\s*leak\b",
    r"\boptimiz", r"\bcpu\b", r"\bthroughput\b",
]
```

### Step 3: Register in Bootstrap

```python
# src/main.py — add to bootstrap()
from src.agents.performance_agent import PerformanceAgent

agent_map[AgentType.PERFORMANCE] = (
    PerformanceAgent(),
    "Performance Agent",
    "Performance profiling and optimization"
)
```

### Step 4: Add a Skill Definition

```yaml
# skills/performance.yaml
name: profile_performance
description: "Analyze code or system for performance bottlenecks"
agent_type: performance
parameters:
  - name: target
    type: string
    description: "Code, endpoint, or system to profile"
    required: true
  - name: profile_type
    type: string
    description: "Type of profiling: cpu, memory, io, all"
    required: false
    default: "all"
```

### Step 5: Add Tests

```python
# tests/test_performance_agent.py
import pytest
from src.agents.performance_agent import PerformanceAgent
from src.orchestrator.context import ConversationContext

class TestPerformanceAgent:
    @pytest.mark.asyncio
    async def test_executes_successfully(self):
        agent = PerformanceAgent()
        ctx = ConversationContext()
        result = await agent.execute("Profile the API endpoint", ctx)
        assert result.agent_id == "performance_agent"
        assert result.confidence > 0
```

---

## Adding New Skills & Workflows

### Adding a Skill

Skills are YAML files in `skills/` that map to MCP tools:

```yaml
# skills/my_skill.yaml
name: analyze_dependencies
description: "Analyze project dependencies for outdated or vulnerable packages"
agent_type: security_sentinel
parameters:
  - name: manifest_path
    type: string
    description: "Path to package manifest (requirements.txt, package.json, etc.)"
    required: true
  - name: check_vulnerabilities
    type: boolean
    description: "Whether to check for known CVEs"
    required: false
    default: true
```

Skills are auto-loaded at startup and exposed as MCP tools.

### Adding a Workflow

Workflows compose multiple agents into a pipeline:

```yaml
# workflows/full_audit.yaml
name: full_audit
description: "Complete codebase audit: security + performance + code quality"
steps:
  - name: security_scan
    agent_type: security_sentinel
    prompt_template: "Scan for vulnerabilities: {target}"

  - name: code_review
    agent_type: code_research
    prompt_template: "Review code quality: {target}"
    # No depends_on — runs in parallel with security_scan

  - name: remediation_plan
    agent_type: remediation
    depends_on: [security_scan, code_review]
    prompt_template: "Generate fixes for: {security_scan.output} and {code_review.output}"
```

---

## Extending the Codebase with GitHub Copilot CLI

GitHub Copilot CLI (`gh copilot`) lets you use AI directly from your terminal to explain code, suggest changes, and run AI-powered commands — without opening an editor.

### Setup

```powershell
# Install GitHub Copilot CLI extension
gh extension install github/gh-copilot

# Verify installation
gh copilot --version
```

### Common Commands for ProtoForge Development

#### Explain Code
```powershell
# Understand how the orchestrator works
gh copilot explain "Read src/orchestrator/engine.py and explain the Plan-first dispatch pattern"

# Understand the routing logic
gh copilot explain "How does intent routing work in src/orchestrator/router.py?"
```

#### Suggest Shell Commands
```powershell
# Let Copilot suggest how to run tests
gh copilot suggest "Run ProtoForge tests with coverage report"

# Let Copilot suggest how to profile the code
gh copilot suggest "Profile the ProtoForge orchestrator for memory usage"
```

#### AI-Powered Git Operations
```powershell
# Generate meaningful commit messages
gh copilot suggest "Write a git commit message for changes in src/agents/"

# Find commits that changed the router
gh copilot suggest "Find all git commits that modified the intent router"
```

---

## Multi-Model Code Review Workflow: Copilot CLI + Claude Opus 4.6 + Codex 5.3

One of the most powerful patterns for code quality is **getting critical feedback from multiple AI models concurrently**. Each model has different strengths:

| Model | Strength | Best For |
|-------|----------|----------|
| **Claude Opus 4.6** | Deep reasoning, nuanced analysis | Architecture review, logic correctness, edge cases |
| **Codex 5.3** | Code-native understanding | Implementation quality, performance, idioms |
| **GPT-4o** | Balanced general intelligence | Overall review, documentation, communication |

### Example: Reviewing the Orchestrator Engine

Open **two separate terminal windows** and fire both models at the same code:

#### Terminal 1 — Claude Opus 4.6 (Architecture & Logic Review)

```powershell
# Using GitHub Copilot CLI with Claude Opus 4.6
# Set your Anthropic API key
$env:ANTHROPIC_API_KEY = "your-key-here"

# Review the orchestrator engine for architectural issues
gh copilot explain @anthropic/claude-opus-4.6 "
Review src/orchestrator/engine.py critically:
1. Is the Plan-first dispatch pattern correctly implemented?
2. Are there race conditions in _fan_out() parallel execution?
3. Could _resolve_sub_agents() miss edge cases?
4. Is error handling comprehensive enough?
5. What are the scaling bottlenecks?
Give specific line-level feedback.
"
```

Or use **curl** to hit the Anthropic API directly for a deep review:

```powershell
# Terminal 1 — Claude Opus 4.6 deep review
$code = Get-Content src/orchestrator/engine.py -Raw
$body = @{
    model = "claude-opus-4.6"
    max_tokens = 4096
    messages = @(@{
        role = "user"
        content = @"
You are an expert code reviewer. Review this Python orchestrator engine critically.
Focus on: architecture correctness, error handling, race conditions, edge cases,
and scalability concerns. Be specific — reference function names and logic flows.

```python
$code
```

Provide:
1. Critical issues (must fix)
2. Warnings (should fix)
3. Suggestions (nice to have)
4. Architecture assessment (1-10 score with reasoning)
"@
    })
} | ConvertTo-Json -Depth 5

Invoke-RestMethod -Uri "https://api.anthropic.com/v1/messages" `
    -Method Post `
    -Headers @{
        "x-api-key" = $env:ANTHROPIC_API_KEY
        "anthropic-version" = "2023-06-01"
        "content-type" = "application/json"
    } `
    -Body $body | ForEach-Object { $_.content[0].text }
```

#### Terminal 2 — Codex 5.3 (Implementation & Performance Review)

```powershell
# Terminal 2 — Codex 5.3 implementation review
$env:OPENAI_API_KEY = "your-key-here"

$code = Get-Content src/orchestrator/engine.py -Raw
$body = @{
    model = "codex-5.3"
    messages = @(
        @{ role = "system"; content = "You are an expert Python code reviewer focused on implementation quality, performance, and idiomatic patterns." },
        @{ role = "user"; content = @"
Review this orchestrator engine for implementation quality:

```python
$code
```

Focus on:
1. Python idioms — is the code Pythonic?
2. Async patterns — is asyncio used correctly?
3. Performance — any unnecessary allocations, O(n^2) patterns, or blocking calls?
4. Type safety — are type hints correct and complete?
5. Error handling — what happens when things fail?
6. Testability — is the code easy to unit test?

Rate each area 1-10 and provide specific improvements.
"@ }
    )
    max_tokens = 4096
} | ConvertTo-Json -Depth 5

Invoke-RestMethod -Uri "https://api.openai.com/v1/chat/completions" `
    -Method Post `
    -Headers @{
        "Authorization" = "Bearer $($env:OPENAI_API_KEY)"
        "Content-Type" = "application/json"
    } `
    -Body $body | ForEach-Object { $_.choices[0].message.content }
```

### Compare & Merge Feedback

After both terminals complete, compare the outputs:

```powershell
# Save outputs from both terminals to files
# (Run after each terminal completes)

# Terminal 1 output → opus_review.md
# Terminal 2 output → codex_review.md

# Then use Copilot to synthesize
gh copilot explain "
Compare these two code reviews and create a unified action plan:
OPUS REVIEW: $(Get-Content opus_review.md -Raw)
CODEX REVIEW: $(Get-Content codex_review.md -Raw)
Prioritize items both models flagged as critical.
"
```

### Automating Multi-Model Review with a Script

Create a reusable review script:

```powershell
# review.ps1 — Multi-model code review script
param(
    [Parameter(Mandatory)]
    [string]$FilePath,

    [string]$Focus = "architecture, performance, correctness"
)

if (-not (Test-Path $FilePath)) {
    Write-Error "File not found: $FilePath"
    exit 1
}

$code = Get-Content $FilePath -Raw
$fileName = Split-Path $FilePath -Leaf

Write-Host "=== Multi-Model Code Review: $fileName ===" -ForegroundColor Cyan
Write-Host ""

# --- Claude Opus 4.6 Review ---
Write-Host "[1/2] Requesting Claude Opus 4.6 review..." -ForegroundColor Yellow

$opusBody = @{
    model = "claude-opus-4.6"
    max_tokens = 4096
    messages = @(@{
        role = "user"
        content = "Review this code critically. Focus on: $Focus`n`n``````python`n$code`n```````nProvide: Critical issues, Warnings, Suggestions, Score (1-10)."
    })
} | ConvertTo-Json -Depth 5

$opusReview = Invoke-RestMethod -Uri "https://api.anthropic.com/v1/messages" `
    -Method Post `
    -Headers @{
        "x-api-key" = $env:ANTHROPIC_API_KEY
        "anthropic-version" = "2023-06-01"
        "content-type" = "application/json"
    } `
    -Body $opusBody | ForEach-Object { $_.content[0].text }

Write-Host "--- Claude Opus 4.6 ---" -ForegroundColor Magenta
Write-Host $opusReview
Write-Host ""

# --- Codex 5.3 Review ---
Write-Host "[2/2] Requesting Codex 5.3 review..." -ForegroundColor Yellow

$codexBody = @{
    model = "codex-5.3"
    messages = @(
        @{ role = "system"; content = "Expert Python code reviewer. Focus on implementation quality." },
        @{ role = "user"; content = "Review critically. Focus: $Focus`n`n``````python`n$code`n```````nProvide: Critical issues, Warnings, Suggestions, Score (1-10)." }
    )
    max_tokens = 4096
} | ConvertTo-Json -Depth 5

$codexReview = Invoke-RestMethod -Uri "https://api.openai.com/v1/chat/completions" `
    -Method Post `
    -Headers @{
        "Authorization" = "Bearer $($env:OPENAI_API_KEY)"
        "Content-Type" = "application/json"
    } `
    -Body $codexBody | ForEach-Object { $_.choices[0].message.content }

Write-Host "--- Codex 5.3 ---" -ForegroundColor Green
Write-Host $codexReview
Write-Host ""

# --- Save reports ---
$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$opusReview | Out-File "reviews/${fileName}_opus_${timestamp}.md"
$codexReview | Out-File "reviews/${fileName}_codex_${timestamp}.md"

Write-Host "=== Reviews saved to reviews/ ===" -ForegroundColor Cyan
```

Usage:

```powershell
# Review a specific file with both models
.\review.ps1 -FilePath src/orchestrator/engine.py

# Review with a specific focus
.\review.ps1 -FilePath src/agents/plan_agent.py -Focus "async patterns, error handling"

# Review the router
.\review.ps1 -FilePath src/orchestrator/router.py -Focus "regex correctness, edge cases"
```

### Quick One-Liner Reviews

For fast feedback on small code sections directly in your terminal:

```powershell
# Quick Opus review of a single function
Get-Content src/orchestrator/engine.py |
    Select-String -Pattern "async def process" -Context 0,40 |
    ForEach-Object { $_.Context.PostContext -join "`n" } |
    gh copilot explain "Review this async method for correctness"

# Quick check: are there any obvious bugs?
gh copilot explain "Are there any bugs in $(Get-Content src/orchestrator/router.py -Raw | Select-Object -First 50)?"

# Explain what a complex regex does
gh copilot explain "What does this regex match: \bfix\s.*\b(?:error|exception|bug|issue|problem)\b"
```

---

## Architecture Decision Records

### ADR-001: Plan-First Over Flat Dispatch

**Status:** Accepted  
**Context:** Need to coordinate multiple agents for complex requests  
**Decision:** Always run Plan Agent first, then fan out  
**Consequences:** Slightly higher latency (+1 LLM call), much better result quality  

### ADR-002: Claude Opus 4.6 as Default LLM

**Status:** Accepted  
**Context:** Need a default model that balances quality and reasoning depth  
**Decision:** Anthropic Claude Opus 4.6 as the default provider  
**Consequences:** Requires Anthropic API key by default, best-in-class reasoning for plan coordination  

### ADR-003: Keyword + LLM Two-Tier Routing

**Status:** Accepted  
**Context:** Pure LLM routing is slow and expensive; pure keyword routing misses nuance  
**Decision:** Fast keyword routing first, LLM fallback when confidence < 0.5  
**Consequences:** Sub-millisecond routing for clear intents, graceful degradation  

### ADR-004: MCP for Skills Distribution

**Status:** Accepted  
**Context:** Agent skills need to be accessible from VS Code Copilot, Claude Desktop, etc.  
**Decision:** Expose all skills as MCP tools via JSON-RPC  
**Consequences:** Any MCP-compatible client can use ProtoForge agents  

### ADR-005: YAML-Defined Skills and Workflows

**Status:** Accepted  
**Context:** Non-engineers need to define and modify agent capabilities  
**Decision:** Skills and workflows defined in YAML, auto-loaded at startup  
**Consequences:** Easy to add/modify without code changes, version-controlled  

---

## Further Reading

- [Semantic Kernel Documentation](https://learn.microsoft.com/semantic-kernel/)
- [Model Context Protocol Spec](https://modelcontextprotocol.io/)
- [GitHub Copilot CLI](https://docs.github.com/en/copilot/github-copilot-in-the-cli)
- [Anthropic Claude API](https://docs.anthropic.com/en/docs)
- [OpenAI Codex API](https://platform.openai.com/docs)
