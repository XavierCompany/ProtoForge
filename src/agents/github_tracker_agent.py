"""GitHub Tracker Agent — commit documentation and issue management.

This agent:
1. Analyzes git commits (diff, message, files changed) to classify
   the change type (feat, fix, refactor, etc.)
2. Generates structured commit documentation following Conventional
   Commits conventions
3. Creates, updates, and comments on GitHub issues with detailed
   context, labels, and cross-references
4. Produces changelogs grouped by change type for releases

The agent can operate locally (reading from the git log) or via the
GitHub API (using the ``repo`` parameter).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

import structlog

from src.agents.base import BaseAgent
from src.orchestrator.context import AgentResult, ConversationContext

if TYPE_CHECKING:
    from src.forge.loader import AgentManifest

logger = structlog.get_logger(__name__)

_DEFAULT_GITHUB_TRACKER_PROMPT = """\
You are the GitHub Tracker Agent — a specialist in repository
documentation and issue management.

You analyze code changes and generate structured commit documentation
following Conventional Commits conventions.  You create and update
GitHub issues with detailed context, labels, and cross-references.

Change types: feat, fix, perf, refactor, docs, test, ci, chore, style.

Always be specific in descriptions — "Fixed null reference in
UserService.GetById()" is better than "Fixed a bug".
"""

# ── Change-type classification heuristics ──────────────────────────────
_CHANGE_TYPE_PATTERNS: dict[str, list[str]] = {
    "feat": [r"\bnew\s+feature\b", r"\badd(?:ed|s)?\b", r"\bintroduc", r"\bimplement"],
    "fix": [r"\bfix(?:ed|es)?\b", r"\bbug\b", r"\bpatch\b", r"\bresolve[ds]?\b", r"\bcorrect"],
    "perf": [r"\bperformance\b", r"\boptimiz", r"\bspeed\b", r"\bfaster\b", r"\bcache"],
    "refactor": [r"\brefactor", r"\brestructur", r"\bclean\s*up\b", r"\breorganiz"],
    "docs": [r"\bdoc(?:s|umentation)?\b", r"\breadme\b", r"\bguide\b", r"\bchangelog\b"],
    "test": [r"\btest(?:s|ing)?\b", r"\bcoverage\b", r"\bspec\b"],
    "ci": [r"\bci\b", r"\bpipeline\b", r"\bgithub\s*action", r"\bworkflow\b", r"\bpre-commit\b"],
    "chore": [r"\bchore\b", r"\bdep(?:endency|s)\b", r"\bbump\b", r"\bupgrade\b", r"\blint"],
    "style": [r"\bformat", r"\bwhitespace\b", r"\bindent", r"\bstyle\b"],
}

# Compiled patterns for performance
_COMPILED_TYPE_PATTERNS: dict[str, list[re.Pattern[str]]] = {
    change_type: [re.compile(p, re.IGNORECASE) for p in patterns]
    for change_type, patterns in _CHANGE_TYPE_PATTERNS.items()
}

# Map change types to suggested GitHub labels
_TYPE_TO_LABELS: dict[str, list[str]] = {
    "feat": ["enhancement"],
    "fix": ["bug"],
    "perf": ["performance"],
    "refactor": ["refactor"],
    "docs": ["documentation"],
    "test": ["testing"],
    "ci": ["ci/cd"],
    "chore": ["maintenance"],
    "style": ["style"],
}


class GitHubTrackerAgent(BaseAgent):
    """Agent that documents commits and manages GitHub issues.

    Can be created two ways:

    * ``GitHubTrackerAgent()`` — uses the built-in fallback prompt.
    * ``GitHubTrackerAgent.from_manifest(manifest)`` — reads from forge/.
    """

    def __init__(
        self,
        agent_id: str = "github_tracker",
        description: str = (
            "Analyzes commits, generates structured documentation, "
            "creates/updates GitHub issues, and produces changelogs"
        ),
        system_prompt: str = _DEFAULT_GITHUB_TRACKER_PROMPT,
        *,
        manifest: AgentManifest | None = None,
    ) -> None:
        super().__init__(
            agent_id=agent_id,
            description=description,
            system_prompt=system_prompt,
            manifest=manifest,
        )

    async def execute(
        self,
        message: str,
        context: ConversationContext,
        params: dict[str, Any] | None = None,
    ) -> AgentResult:
        logger.info(
            "github_tracker_executing",
            message_length=len(message),
        )

        self._build_messages(message, context)

        # Determine the requested action from params or message
        action = (params or {}).get("action", self._detect_action(message))

        if action == "document_commit":
            return self._document_commit(message, context, params or {})
        if action == "manage_issue":
            return self._manage_issue(message, context, params or {})
        if action == "changelog":
            return self._generate_changelog(message, context, params or {})

        # Default: analyze the message and produce commit documentation
        return self._document_commit(message, context, params or {})

    # ── Action detection ────────────────────────────────────────────────

    @staticmethod
    def _detect_action(message: str) -> str:
        """Detect which skill the user is requesting from the message."""
        lower = message.lower()
        if any(kw in lower for kw in ["issue", "create issue", "update issue", "close issue"]):
            return "manage_issue"
        if any(kw in lower for kw in ["changelog", "release note", "release notes"]):
            return "changelog"
        return "document_commit"

    # ── Commit documentation ────────────────────────────────────────────

    def _document_commit(
        self,
        message: str,
        context: ConversationContext,
        params: dict[str, Any],
    ) -> AgentResult:
        """Analyze and document a commit or set of changes."""
        commit_sha = params.get("commit_sha", "")
        commit_message = params.get("commit_message", message)
        diff = params.get("diff", "")
        repo = params.get("repo", context.get_memory("github_repo", ""))

        # Classify the change type
        change_type = self._classify_change(commit_message, diff)
        scope = self._detect_scope(commit_message, diff)
        labels = _TYPE_TO_LABELS.get(change_type, ["maintenance"])
        impact = self._assess_impact(commit_message, diff)

        # Build conventional commit message
        scope_str = f"({scope})" if scope else ""
        breaking = "!" if impact == "breaking" else ""
        subject = self._extract_subject(commit_message)
        conventional_msg = f"{change_type}{scope_str}{breaking}: {subject}"

        # Build human-readable summary
        files_hint = self._extract_files(diff) if diff else []
        files_block = ""
        if files_hint:
            files_block = "\n**Files changed:**\n" + "\n".join(f"  - {f}" for f in files_hint)

        sha_ref = f" (`{commit_sha[:8]}`)" if commit_sha else ""
        repo_ref = f" in `{repo}`" if repo else ""

        summary = (
            f"**Commit Documentation**{sha_ref}{repo_ref}\n\n"
            f"**Type:** `{change_type}` | **Scope:** `{scope or 'general'}` | "
            f"**Impact:** `{impact}`\n\n"
            f"**Conventional message:**\n```\n{conventional_msg}\n```\n\n"
            f"**Summary:** {subject}\n"
            f"{files_block}\n\n"
            f"**Suggested labels:** {', '.join(f'`{lbl}`' for lbl in labels)}"
        )

        return AgentResult(
            agent_id=self.agent_id,
            content=summary,
            confidence=0.80,
            artifacts={
                "conventional_message": conventional_msg,
                "change_type": change_type,
                "scope": scope,
                "impact": impact,
                "suggested_labels": labels,
                "files_changed": files_hint,
                "commit_sha": commit_sha,
                "repo": repo,
            },
        )

    # ── Issue management ────────────────────────────────────────────────

    def _manage_issue(
        self,
        message: str,
        context: ConversationContext,
        params: dict[str, Any],
    ) -> AgentResult:
        """Create, update, or comment on a GitHub issue."""
        issue_action = params.get("issue_action", "create")
        repo = params.get("repo", context.get_memory("github_repo", ""))
        title = params.get("title", "")
        body = params.get("body", "")
        issue_number = params.get("issue_number")
        commit_sha = params.get("commit_sha", "")
        labels = params.get("labels", [])

        if not title and issue_action == "create":
            # Auto-generate title from message
            change_type = self._classify_change(message, "")
            subject = self._extract_subject(message)
            title = f"[{change_type.capitalize()}] {subject}"
            if not labels:
                labels = _TYPE_TO_LABELS.get(change_type, [])

        if not body and issue_action == "create":
            body = self._generate_issue_body(message, commit_sha, context)

        # Build response describing the action
        repo_ref = f" in `{repo}`" if repo else ""
        if issue_action == "create":
            response = (
                f"**GitHub Issue — Create**{repo_ref}\n\n"
                f"**Title:** {title}\n"
                f"**Labels:** {', '.join(f'`{lbl}`' for lbl in labels) or '_none_'}\n\n"
                f"**Body:**\n{body}\n\n"
                f"_Ready to create — awaiting confirmation._"
            )
        elif issue_action == "close":
            response = (
                f"**GitHub Issue — Close** #{issue_number}{repo_ref}\n\n"
                f"Linked to commit `{commit_sha[:8]}`.\n"
                f"_Ready to close — awaiting confirmation._"
            )
        elif issue_action == "comment":
            response = (
                f"**GitHub Issue — Comment** #{issue_number}{repo_ref}\n\n"
                f"**Comment:**\n{body or message}\n\n"
                f"_Ready to add comment — awaiting confirmation._"
            )
        else:  # update
            response = (
                f"**GitHub Issue — Update** #{issue_number}{repo_ref}\n\n"
                f"**New title:** {title or '_unchanged_'}\n"
                f"**Labels:** {', '.join(f'`{lbl}`' for lbl in labels) or '_unchanged_'}\n\n"
                f"_Ready to update — awaiting confirmation._"
            )

        return AgentResult(
            agent_id=self.agent_id,
            content=response,
            confidence=0.75,
            artifacts={
                "issue_action": issue_action,
                "title": title,
                "body": body,
                "labels": labels,
                "issue_number": issue_number,
                "commit_sha": commit_sha,
                "repo": repo,
            },
        )

    # ── Changelog generation ────────────────────────────────────────────

    def _generate_changelog(
        self,
        _message: str,
        context: ConversationContext,
        params: dict[str, Any],
    ) -> AgentResult:
        """Produce a grouped changelog from commit history context."""
        version = params.get("version", "Unreleased")
        from_ref = params.get("from_ref", "")
        to_ref = params.get("to_ref", "HEAD")

        # Read commit history from working memory (populated by prior steps)
        commit_history: list[dict] = context.get_memory("commit_history", [])

        if not commit_history:
            # Provide guidance on how to use
            return AgentResult(
                agent_id=self.agent_id,
                content=(
                    "**Changelog Generator**\n\n"
                    "No commit history found in context.  To generate a changelog:\n\n"
                    "1. Provide commits via the `commit_history` working memory, or\n"
                    "2. Pass individual commit info (SHA, message, diff) and I'll "
                    "classify each one.\n\n"
                    f"**Range:** `{from_ref or '(start)'}..{to_ref}`\n"
                    f"**Version:** `{version}`"
                ),
                confidence=0.5,
                artifacts={"version": version, "from_ref": from_ref, "to_ref": to_ref},
            )

        # Group commits by type
        groups: dict[str, list[dict]] = {}
        for commit in commit_history:
            ctype = commit.get("type") or self._classify_change(
                commit.get("message", ""),
                commit.get("diff", ""),
            )
            groups.setdefault(ctype, []).append(commit)

        # Build Markdown
        type_display = {
            "feat": "Features",
            "fix": "Fixes",
            "perf": "Performance",
            "refactor": "Refactors",
            "docs": "Documentation",
            "test": "Tests",
            "ci": "CI/CD",
            "chore": "Chores",
            "style": "Style",
        }

        sections = []
        section_order = ["feat", "fix", "perf", "refactor", "docs", "test", "ci", "chore", "style"]
        for ctype in section_order:
            commits = groups.get(ctype, [])
            if not commits:
                continue
            heading = type_display.get(ctype, ctype.capitalize())
            lines = [f"### {heading}"]
            for c in commits:
                sha_ref = f" ({c['sha'][:8]})" if c.get("sha") else ""
                lines.append(f"- {c.get('message', 'No description')}{sha_ref}")
            sections.append("\n".join(lines))

        changelog_md = f"## [{version}]\n\n" + "\n\n".join(sections) if sections else f"## [{version}]\n\nNo changes."

        stats = {
            "total_commits": len(commit_history),
            "features": len(groups.get("feat", [])),
            "fixes": len(groups.get("fix", [])),
            "improvements": len(groups.get("perf", [])) + len(groups.get("refactor", [])),
            "other": sum(len(v) for k, v in groups.items() if k not in {"feat", "fix", "perf", "refactor"}),
        }

        return AgentResult(
            agent_id=self.agent_id,
            content=f"**Generated Changelog**\n\n{changelog_md}",
            confidence=0.85,
            artifacts={
                "changelog_md": changelog_md,
                "stats": stats,
                "version": version,
            },
        )

    # ── Classification helpers ──────────────────────────────────────────

    @staticmethod
    def _classify_change(message: str, diff: str) -> str:
        """Classify a change into a Conventional Commits type.

        Scores each type by keyword pattern matches in the message and diff,
        returning the highest-scoring type (or ``chore`` as default).
        """
        combined = f"{message} {diff}"
        scores: dict[str, int] = {}
        for ctype, patterns in _COMPILED_TYPE_PATTERNS.items():
            score = sum(1 for p in patterns if p.search(combined))
            if score:
                scores[ctype] = score

        if not scores:
            return "chore"
        return max(scores, key=lambda k: scores[k])

    @staticmethod
    def _detect_scope(message: str, diff: str) -> str:
        """Detect the module/scope from filenames or message keywords."""
        combined = f"{message} {diff}".lower()
        scope_map = {
            "router": ["router", "routing", "intent"],
            "engine": ["engine", "orchestrat"],
            "agents": ["agent"],
            "forge": ["forge", "manifest", "yaml"],
            "mcp": ["mcp", "skill"],
            "server": ["server", "endpoint", "api"],
            "tests": ["test", "pytest", "fixture"],
            "ci": ["ci", "pipeline", "github action", "pre-commit"],
            "docs": ["readme", "guide", "changelog", "documentation"],
        }
        for scope, keywords in scope_map.items():
            if any(kw in combined for kw in keywords):
                return scope
        return ""

    @staticmethod
    def _assess_impact(message: str, diff: str) -> str:
        """Assess the impact level of a change."""
        combined = f"{message} {diff}".lower()
        if any(kw in combined for kw in ["breaking", "removal", "deprecat", "incompatible"]):
            return "breaking"
        if any(kw in combined for kw in ["new feature", "new agent", "architecture", "major"]):
            return "major"
        if any(kw in combined for kw in ["improve", "enhance", "add", "extend"]):
            return "minor"
        if any(kw in combined for kw in ["fix", "patch", "typo", "lint"]):
            return "patch"
        return "minor"

    @staticmethod
    def _extract_subject(message: str) -> str:
        """Extract a clean one-line subject from a commit message."""
        # Take first non-empty line
        for line in message.strip().splitlines():
            stripped = line.strip()
            if stripped:
                # Remove existing conventional prefix if present
                match = re.match(r"^(?:feat|fix|perf|refactor|docs|test|ci|chore|style)(?:\([^)]*\))?!?:\s*", stripped)
                if match:
                    return stripped[match.end() :]
                return stripped[:120]
        return "No description provided"

    @staticmethod
    def _extract_files(diff: str) -> list[str]:
        """Extract file paths from a diff string."""
        files: list[str] = []
        for match in re.finditer(r"^(?:diff --git a/|---|\+\+\+) (.+?)(?:\s|$)", diff, re.MULTILINE):
            path = match.group(1).strip()
            if path and path not in files and not path.startswith("/dev/null"):
                # Clean git diff prefixes
                path = re.sub(r"^[ab]/", "", path)
                if path not in files:
                    files.append(path)
        return files

    def _generate_issue_body(
        self,
        message: str,
        commit_sha: str,
        context: ConversationContext,
    ) -> str:
        """Generate a structured issue body from context."""
        change_type = self._classify_change(message, "")
        scope = self._detect_scope(message, "")
        impact = self._assess_impact(message, "")

        sha_ref = f"\n\n**Commit:** `{commit_sha[:8]}`" if commit_sha else ""
        plan_output = context.get_memory("plan_output", "")
        plan_ref = f"\n\n**Related plan:**\n{plan_output[:300]}..." if plan_output else ""

        return (
            f"## Summary\n\n{message}\n\n"
            f"## Details\n\n"
            f"- **Type:** `{change_type}`\n"
            f"- **Scope:** `{scope or 'general'}`\n"
            f"- **Impact:** `{impact}`\n"
            f"{sha_ref}{plan_ref}\n\n"
            f"## Testing\n\n_Describe how this was verified._\n\n"
            f"## Related\n\n_Link related issues, PRs, or commits._"
        )
