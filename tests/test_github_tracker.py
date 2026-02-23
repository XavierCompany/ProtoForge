"""Tests for the GitHub Tracker Agent — commit documentation, issue management, and changelogs."""

from __future__ import annotations

import pytest

from src.agents.github_tracker_agent import (
    _COMPILED_TYPE_PATTERNS,
    _TYPE_TO_LABELS,
    GitHubTrackerAgent,
)
from src.orchestrator.context import AgentResult, ConversationContext
from src.orchestrator.router import AgentType, IntentRouter

# ── Classification helpers ──────────────────────────────────────────────────


class TestClassifyChange:
    """Test _classify_change for all nine Conventional Commits types."""

    @pytest.mark.parametrize(
        ("message", "expected"),
        [
            ("add new login feature", "feat"),
            ("Added support for OAuth2", "feat"),
            ("introduce caching layer", "feat"),
            ("implement dark mode", "feat"),
            ("fix null reference in UserService", "fix"),
            ("resolved race condition in scheduler", "fix"),
            ("corrected pagination offset", "fix"),
            ("bug in password validation", "fix"),
            ("performance optimization for search queries", "perf"),
            ("cache response for faster startup", "perf"),
            ("refactored router module", "refactor"),
            ("clean up unused imports", "refactor"),
            ("restructured the config loader", "refactor"),
            ("update README documentation", "docs"),
            ("added changelog guide", "docs"),
            ("wrote unit tests for auth module", "test"),
            ("increase test coverage for router", "test"),
            ("update CI pipeline for Node 18", "ci"),
            ("update github action for linting", "ci"),
            ("pre-commit hook update", "ci"),
            ("bump dependency versions", "chore"),
            ("chore: lint fixes", "chore"),
            ("format code with ruff", "style"),
            ("adjust whitespace formatting", "style"),
        ],
    )
    def test_classifies_correctly(self, message: str, expected: str) -> None:
        result = GitHubTrackerAgent._classify_change(message, "")
        assert result == expected, f"Expected '{expected}' for '{message}', got '{result}'"

    def test_falls_back_to_chore(self) -> None:
        result = GitHubTrackerAgent._classify_change("random unrelated text xyz", "")
        assert result == "chore"

    def test_diff_contributes_to_classification(self) -> None:
        # Message is ambiguous, diff should tip it towards "test"
        result = GitHubTrackerAgent._classify_change(
            "update module",
            "diff --git a/tests/test_auth.py\n+def test_login():\n+   assert True",
        )
        assert result == "test"


class TestDetectScope:
    @pytest.mark.parametrize(
        ("message", "expected"),
        [
            ("updated the router to handle new patterns", "router"),
            ("refactored orchestration engine", "engine"),
            ("added new agent for security", "agents"),
            ("fix forge manifest loading", "forge"),
            ("mcp skill registration", "mcp"),
            ("server endpoint for health check", "server"),
            ("added pytest fixtures", "tests"),
            ("CI pipeline for releases", "ci"),
            ("updated the README guide", "docs"),
        ],
    )
    def test_detects_scope(self, message: str, expected: str) -> None:
        result = GitHubTrackerAgent._detect_scope(message, "")
        assert result == expected

    def test_empty_scope_for_generic_message(self) -> None:
        result = GitHubTrackerAgent._detect_scope("did some stuff", "")
        assert result == ""


class TestAssessImpact:
    def test_breaking(self) -> None:
        assert GitHubTrackerAgent._assess_impact("breaking change in API", "") == "breaking"

    def test_major(self) -> None:
        assert GitHubTrackerAgent._assess_impact("new feature for dashboard", "") == "major"

    def test_minor(self) -> None:
        assert GitHubTrackerAgent._assess_impact("improve search results", "") == "minor"

    def test_patch(self) -> None:
        assert GitHubTrackerAgent._assess_impact("fix typo in config", "") == "patch"

    def test_removal_is_breaking(self) -> None:
        assert GitHubTrackerAgent._assess_impact("removal of legacy endpoint", "") == "breaking"


class TestExtractSubject:
    def test_plain_message(self) -> None:
        result = GitHubTrackerAgent._extract_subject("Add user authentication")
        assert result == "Add user authentication"

    def test_strips_conventional_prefix(self) -> None:
        result = GitHubTrackerAgent._extract_subject("feat(auth): add OAuth2 support")
        assert result == "add OAuth2 support"

    def test_multiline_takes_first(self) -> None:
        result = GitHubTrackerAgent._extract_subject("First line\n\nBody paragraph")
        assert result == "First line"

    def test_empty_returns_default(self) -> None:
        result = GitHubTrackerAgent._extract_subject("")
        assert result == "No description provided"

    def test_strips_breaking_prefix(self) -> None:
        result = GitHubTrackerAgent._extract_subject("fix!: drop Python 3.8 support")
        assert result == "drop Python 3.8 support"

    def test_scoped_breaking(self) -> None:
        result = GitHubTrackerAgent._extract_subject("refactor(api)!: rewrite endpoints")
        assert result == "rewrite endpoints"


class TestExtractFiles:
    def test_extracts_from_diff(self) -> None:
        diff = (
            "diff --git a/src/main.py b/src/main.py\n"
            "--- a/src/main.py\n"
            "+++ b/src/main.py\n"
            "@@ -1,3 +1,4 @@\n"
            "+import os\n"
        )
        result = GitHubTrackerAgent._extract_files(diff)
        assert "src/main.py" in result

    def test_no_duplicates(self) -> None:
        diff = "diff --git a/src/main.py b/src/main.py\n--- a/src/main.py\n+++ b/src/main.py\n"
        result = GitHubTrackerAgent._extract_files(diff)
        # Should have src/main.py only once
        assert result.count("src/main.py") == 1

    def test_empty_diff(self) -> None:
        assert GitHubTrackerAgent._extract_files("") == []

    def test_excludes_dev_null(self) -> None:
        diff = "--- /dev/null\n+++ b/src/new_file.py\n"
        result = GitHubTrackerAgent._extract_files(diff)
        assert "/dev/null" not in result


# ── Action detection ────────────────────────────────────────────────────────


class TestDetectAction:
    def test_issue_keyword(self) -> None:
        assert GitHubTrackerAgent._detect_action("create issue for auth bug") == "manage_issue"

    def test_close_issue(self) -> None:
        assert GitHubTrackerAgent._detect_action("close issue #42") == "manage_issue"

    def test_changelog_keyword(self) -> None:
        assert GitHubTrackerAgent._detect_action("generate the changelog") == "changelog"

    def test_release_notes(self) -> None:
        assert GitHubTrackerAgent._detect_action("write release notes") == "changelog"

    def test_default_is_document_commit(self) -> None:
        assert GitHubTrackerAgent._detect_action("analyze this code change") == "document_commit"


# ── Execute dispatch ────────────────────────────────────────────────────────


class TestExecute:
    @pytest.fixture
    def agent(self) -> GitHubTrackerAgent:
        return GitHubTrackerAgent()

    @pytest.fixture
    def context(self) -> ConversationContext:
        return ConversationContext()

    @pytest.mark.asyncio
    async def test_document_commit_dispatch(self, agent: GitHubTrackerAgent, context: ConversationContext) -> None:
        result = await agent.execute(
            "document this commit",
            context,
            params={"action": "document_commit", "commit_message": "update README documentation"},
        )
        assert isinstance(result, AgentResult)
        assert result.agent_id == "github_tracker"
        assert result.artifacts["change_type"] == "docs"

    @pytest.mark.asyncio
    async def test_manage_issue_dispatch(self, agent: GitHubTrackerAgent, context: ConversationContext) -> None:
        result = await agent.execute(
            "create issue for login bug",
            context,
            params={"action": "manage_issue", "issue_action": "create"},
        )
        assert isinstance(result, AgentResult)
        assert result.artifacts["issue_action"] == "create"
        assert result.artifacts["title"]

    @pytest.mark.asyncio
    async def test_changelog_dispatch(self, agent: GitHubTrackerAgent, context: ConversationContext) -> None:
        result = await agent.execute(
            "generate changelog",
            context,
            params={"action": "changelog", "version": "1.2.0"},
        )
        assert isinstance(result, AgentResult)
        assert result.artifacts["version"] == "1.2.0"

    @pytest.mark.asyncio
    async def test_default_dispatch_is_document(self, agent: GitHubTrackerAgent, context: ConversationContext) -> None:
        result = await agent.execute("some random input", context)
        assert result.artifacts.get("change_type") is not None

    @pytest.mark.asyncio
    async def test_auto_detects_changelog_from_message(
        self, agent: GitHubTrackerAgent, context: ConversationContext
    ) -> None:
        result = await agent.execute("generate release notes", context)
        assert "Changelog" in result.content


# ── Document commit detail ──────────────────────────────────────────────────


class TestDocumentCommit:
    @pytest.fixture
    def agent(self) -> GitHubTrackerAgent:
        return GitHubTrackerAgent()

    @pytest.mark.asyncio
    async def test_commit_with_sha_and_repo(self, agent: GitHubTrackerAgent) -> None:
        ctx = ConversationContext()
        result = await agent.execute(
            "document commit",
            ctx,
            params={
                "action": "document_commit",
                "commit_sha": "abc12345def67890",
                "commit_message": "fix(router): handle empty intent gracefully",
                "diff": (
                    "diff --git a/src/orchestrator/router.py"
                    " b/src/orchestrator/router.py\n"
                    "+    if not intent:\n"
                    "+        return default"
                ),
                "repo": "XavierCompany/ProtoForge",
            },
        )
        assert result.artifacts["change_type"] == "fix"
        assert result.artifacts["scope"] == "router"
        assert "abc12345" in result.content
        assert "XavierCompany/ProtoForge" in result.content
        assert result.artifacts["suggested_labels"] == ["bug"]

    @pytest.mark.asyncio
    async def test_breaking_change(self, agent: GitHubTrackerAgent) -> None:
        ctx = ConversationContext()
        result = await agent.execute(
            "doc",
            ctx,
            params={
                "action": "document_commit",
                "commit_message": "removal of deprecated API endpoint",
            },
        )
        assert result.artifacts["impact"] == "breaking"

    @pytest.mark.asyncio
    async def test_conventional_message_format(self, agent: GitHubTrackerAgent) -> None:
        ctx = ConversationContext()
        result = await agent.execute(
            "doc",
            ctx,
            params={
                "action": "document_commit",
                "commit_message": "add search filtering",
            },
        )
        conv = result.artifacts["conventional_message"]
        # Should start with the detected type
        assert conv.startswith("feat")
        assert "add search filtering" in conv.lower()


# ── Manage issue detail ─────────────────────────────────────────────────────


class TestManageIssue:
    @pytest.fixture
    def agent(self) -> GitHubTrackerAgent:
        return GitHubTrackerAgent()

    @pytest.mark.asyncio
    async def test_create_auto_generates_title(self, agent: GitHubTrackerAgent) -> None:
        ctx = ConversationContext()
        result = await agent.execute(
            "fix login timeout bug",
            ctx,
            params={"action": "manage_issue", "issue_action": "create"},
        )
        assert "[Fix]" in result.artifacts["title"]
        assert "bug" in result.artifacts["labels"]

    @pytest.mark.asyncio
    async def test_close_action(self, agent: GitHubTrackerAgent) -> None:
        ctx = ConversationContext()
        result = await agent.execute(
            "close this issue",
            ctx,
            params={
                "action": "manage_issue",
                "issue_action": "close",
                "issue_number": 42,
                "commit_sha": "abc12345",
            },
        )
        assert "Close" in result.content
        assert result.artifacts["issue_number"] == 42

    @pytest.mark.asyncio
    async def test_comment_action(self, agent: GitHubTrackerAgent) -> None:
        ctx = ConversationContext()
        result = await agent.execute(
            "add comment about test results",
            ctx,
            params={
                "action": "manage_issue",
                "issue_action": "comment",
                "issue_number": 7,
                "body": "All 200 tests pass.",
            },
        )
        assert "Comment" in result.content
        assert result.artifacts["body"] == "All 200 tests pass."

    @pytest.mark.asyncio
    async def test_update_action(self, agent: GitHubTrackerAgent) -> None:
        ctx = ConversationContext()
        result = await agent.execute(
            "update",
            ctx,
            params={
                "action": "manage_issue",
                "issue_action": "update",
                "issue_number": 3,
                "title": "Updated title",
                "labels": ["enhancement", "priority"],
            },
        )
        assert "Update" in result.content
        assert result.artifacts["title"] == "Updated title"

    @pytest.mark.asyncio
    async def test_create_with_repo_context(self, agent: GitHubTrackerAgent) -> None:
        ctx = ConversationContext()
        ctx.set_memory("github_repo", "XavierCompany/ProtoForge")
        result = await agent.execute(
            "new feature request",
            ctx,
            params={"action": "manage_issue", "issue_action": "create"},
        )
        assert "XavierCompany/ProtoForge" in result.content


# ── Changelog generation detail ─────────────────────────────────────────────


class TestGenerateChangelog:
    @pytest.fixture
    def agent(self) -> GitHubTrackerAgent:
        return GitHubTrackerAgent()

    @pytest.mark.asyncio
    async def test_empty_history_guidance(self, agent: GitHubTrackerAgent) -> None:
        ctx = ConversationContext()
        result = await agent.execute("generate changelog", ctx, params={"action": "changelog"})
        assert result.confidence == 0.5
        assert "No commit history" in result.content

    @pytest.mark.asyncio
    async def test_changelog_with_history(self, agent: GitHubTrackerAgent) -> None:
        ctx = ConversationContext()
        ctx.set_memory(
            "commit_history",
            [
                {"sha": "aaa11111", "message": "add user auth", "type": "feat"},
                {"sha": "bbb22222", "message": "fix login crash", "type": "fix"},
                {"sha": "ccc33333", "message": "improve query speed", "type": "perf"},
                {"sha": "ddd44444", "message": "update README", "type": "docs"},
            ],
        )
        result = await agent.execute(
            "generate changelog",
            ctx,
            params={"action": "changelog", "version": "2.0.0"},
        )
        assert "2.0.0" in result.content
        assert "Features" in result.content
        assert "Fixes" in result.content
        assert result.artifacts["stats"]["total_commits"] == 4
        assert result.artifacts["stats"]["features"] == 1
        assert result.artifacts["stats"]["fixes"] == 1

    @pytest.mark.asyncio
    async def test_changelog_auto_classifies(self, agent: GitHubTrackerAgent) -> None:
        ctx = ConversationContext()
        ctx.set_memory(
            "commit_history",
            [
                {"sha": "eee55555", "message": "added new dark mode feature"},
                {"sha": "fff66666", "message": "fix typo in error message"},
            ],
        )
        result = await agent.execute(
            "changelog",
            ctx,
            params={"action": "changelog", "version": "1.1.0"},
        )
        # Should auto-classify: feat and fix
        assert result.artifacts["stats"]["features"] == 1
        assert result.artifacts["stats"]["fixes"] == 1

    @pytest.mark.asyncio
    async def test_changelog_version_in_output(self, agent: GitHubTrackerAgent) -> None:
        ctx = ConversationContext()
        ctx.set_memory(
            "commit_history",
            [{"sha": "ggg77777", "message": "chore: cleanup", "type": "chore"}],
        )
        result = await agent.execute(
            "gen",
            ctx,
            params={"action": "changelog", "version": "3.0.0-rc1"},
        )
        assert "3.0.0-rc1" in result.artifacts["changelog_md"]


# ── from_manifest ───────────────────────────────────────────────────────────


class TestFromManifest:
    def test_from_manifest(self) -> None:
        from src.forge.loader import AgentManifest

        manifest = AgentManifest(
            id="github_tracker",
            name="GitHub Tracker Agent",
            type="specialist",
            version="1.0.0",
            description="Test github tracker",
            context_budget={"max_input_tokens": 20000, "max_output_tokens": 10000},
            resolved_prompts={"system": "Custom tracker prompt"},
        )
        agent = GitHubTrackerAgent.from_manifest(manifest)
        assert agent.agent_id == "github_tracker"
        assert "Custom tracker prompt" in agent.system_prompt


# ── Router integration ──────────────────────────────────────────────────────


class TestRouterIntegration:
    @pytest.fixture
    def router(self) -> IntentRouter:
        return IntentRouter()

    def test_github_keyword_routes(self, router: IntentRouter) -> None:
        result = router.route_by_keywords("document this github commit")
        assert result.primary_agent == AgentType.GITHUB_TRACKER

    def test_changelog_keyword_routes(self, router: IntentRouter) -> None:
        result = router.route_by_keywords("generate a changelog for the release")
        assert result.primary_agent == AgentType.GITHUB_TRACKER

    def test_issue_keyword_routes(self, router: IntentRouter) -> None:
        result = router.route_by_keywords("create a github issue for the bug")
        assert result.primary_agent == AgentType.GITHUB_TRACKER

    def test_commit_keyword_routes(self, router: IntentRouter) -> None:
        result = router.route_by_keywords("what does this commit do?")
        assert result.primary_agent == AgentType.GITHUB_TRACKER

    def test_pr_keyword_routes(self, router: IntentRouter) -> None:
        result = router.route_by_keywords("review this pull request")
        assert result.primary_agent == AgentType.GITHUB_TRACKER


# ── Module-level constants ──────────────────────────────────────────────────


class TestModuleConstants:
    def test_compiled_patterns_match_raw(self) -> None:
        """Ensure compiled patterns exist for every raw pattern set."""
        from src.agents.github_tracker_agent import _CHANGE_TYPE_PATTERNS

        assert set(_COMPILED_TYPE_PATTERNS.keys()) == set(_CHANGE_TYPE_PATTERNS.keys())

    def test_type_to_labels_covers_all_types(self) -> None:
        expected = {"feat", "fix", "perf", "refactor", "docs", "test", "ci", "chore", "style"}
        assert set(_TYPE_TO_LABELS.keys()) == expected

    def test_labels_are_non_empty_lists(self) -> None:
        for ctype, labels in _TYPE_TO_LABELS.items():
            assert isinstance(labels, list), f"{ctype} labels should be a list"
            assert len(labels) > 0, f"{ctype} labels should not be empty"
