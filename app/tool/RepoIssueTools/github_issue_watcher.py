import json
from typing import Optional, Any
import os
from github import Github
from pydantic import Field
from app.tool.base import BaseTool, ToolResult

class GitHubIssueWatcher(BaseTool):
    """
    获取最新 open issue（非 PR）。
    优先读取环境变量 GITHUB_TOKEN 和 GITHUB_REPO_URL。
    """
    name: str = "github_issue_watcher"
    description: str = "Fetch latest open non-PR issue from configured repo."
    gh: Optional[Any] = Field(default=None, exclude=True)
    repo_full_name: Optional[str] = Field(default=None, exclude=True)
    repo: Optional[Any] = Field(default=None, exclude=True)

    def __init__(self, token: Optional[str] = None, repo_url: Optional[str] = None):
        token = token or os.environ.get("GITHUB_TOKEN")
        repo_url = repo_url or os.environ.get("GITHUB_REPO_URL")
        if not token or not repo_url:
            raise ValueError("Please set GITHUB_TOKEN and GITHUB_REPO_URL environment variables.")
        super().__init__()
        self.gh = Github(token)
        self.repo_full_name = self._normalize_repo(repo_url)
        self.repo = self.gh.get_repo(self.repo_full_name)

    def _normalize_repo(self, repo_url: str) -> str:
        if repo_url.startswith("http"):
            parts = repo_url.rstrip("/").split("/")
            return f"{parts[-2]}/{parts[-1].replace('.git','')}"
        return repo_url

    async def execute(self) -> ToolResult:
        """
        Fetch the latest open non-PR issue from the configured repository.
        """
        if not self.repo:
            return ToolResult(error="Repository not initialized")

        issues = self.repo.get_issues(state="open", sort="created", direction="desc")
        for issue in issues:
            # Skip PRs
            if hasattr(issue, "pull_request") and issue.pull_request:
                continue
            payload = {
                "number": issue.number,
                "title": issue.title,
                "body": issue.body or "",
                "url": issue.html_url,
                "labels": [l.name for l in issue.labels],
            }

            return ToolResult(output=json.dumps(payload))
        return ToolResult(output=None)