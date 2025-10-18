# tools/project_context_builder.py
import asyncio
import os
import json
import subprocess
from pathlib import Path
from typing import Optional, Any, Dict

from pydantic import Field

from app.tool.base import BaseTool, ToolResult

_PROJECT_CONTEXT_DESCRIPTION = """\
Build a concise project context for the local repository:
* Ensures the repo is cloned / up-to-date (via GITHUB_REPO_URL / GITHUB_TOKEN)
* Extracts README text (case-insensitive lookup)
* Builds a limited-depth directory tree (default 3 levels)
* Embeds optional issue payload into the returned context

Use this tool when you need a quick snapshot of the project structure & docs.
"""

Context = dict  # 可扩展为泛型，同 BrowserUseTool


class ProjectContextBuilder(BaseTool):
    """Build project context: README, directory tree and optional issue summary."""

    name: str = "project_context_builder"
    description: str = _PROJECT_CONTEXT_DESCRIPTION

    # ── 供 Agent 解析的参数模式 ───────────────────────────────────────────────
    parameters: dict = {
        "type": "object",
        "properties": {
            "issue_payload": {
                "type": "object",
                "description": "Optional issue dict to embed in context",
            },
            "max_depth": {
                "type": "integer",
                "description": "Maximum depth for directory tree walk",
                "default": 3,
            },
        },
        "required": [],  # 全部可选
    }

    # ── 运行时状态（不序列化） ───────────────────────────────────────────────
    lock: asyncio.Lock = Field(default_factory=asyncio.Lock, exclude=True)

    workspace_root: Path = Field(default_factory=lambda: Path.cwd() / "workspace", exclude=True)
    repo_full_name: Optional[str] = Field(default=None, exclude=True)
    repo_local: Path = Field(default_factory=lambda: Path("dummy"), exclude=True)

    def __init__(
        self,
        workspace_root: Optional[Path] = None,
        repo_full_name: Optional[str] = None,
    ):
        # 调用 pydantic 基类初始化
        super().__init__()

        self.workspace_root = Path(workspace_root or Path.cwd() / "workspace_cxt")
        # self.repo_full_name = repo_full_name or os.getenv("GITHUB_REPO_URL", "")
        self.repo_full_name = "https://github.com/harker-xm/front-end-web-finals-.git"
        # 归一化 owner/repo
        if self.repo_full_name.startswith("http"):
            parts = self.repo_full_name.rstrip("/").split("/")
            self.repo_full_name = f"{parts[-2]}/{parts[-1].replace('.git', '')}"
        repo_dir_name = self.repo_full_name.split("/")[-1] if self.repo_full_name else "repo"
        self.repo_local = self.workspace_root / repo_dir_name

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------
    def _ensure_local_clone(self) -> None:
        """Clone 或拉取最新代码。"""
        if not self.repo_local.exists():
            self.workspace_root.mkdir(parents=True, exist_ok=True)
            clone_url = "https://github.com/harker-xm/front-end-web-finals-.git"
            if not clone_url:
                raise RuntimeError("GITHUB_REPO_URL not set, cannot clone.")
            subprocess.run(["git", "clone", clone_url, str(self.repo_local)], check=True)
        else:
            subprocess.run(["git", "checkout", "main"], cwd=str(self.repo_local), check=False)
            subprocess.run(["git", "pull"], cwd=str(self.repo_local), check=True)

    def _read_readme(self) -> str:
        for name in ("README.md", "README.MD", "readme.md", "README"):
            readme = self.repo_local / name
            if readme.is_file():
                return readme.read_text(encoding="utf-8", errors="ignore")
        return ""

    def _build_tree(self, path: Path, depth: int = 0, max_depth: int = 3) -> Any:
        if depth >= max_depth:
            return "..."
        if path.is_file():
            return {"type": "file", "size": path.stat().st_size}
        tree: Dict[str, Any] = {}
        for child in sorted(path.iterdir()):
            tree[child.name] = self._build_tree(child, depth + 1, max_depth)
        return tree

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------
    async def execute(
        self,
        issue_payload: Optional[Dict[str, Any]] = None,
        max_depth: int = 3,
        **kwargs,
    ) -> ToolResult:
        """
        Build and return project context.

        Args:
            issue_payload: dict — 可选 issue 信息
            max_depth: int — 目录树深度
        Returns:
            ToolResult(output=context_dict)
        """
        async with self.lock:
            try:
                await asyncio.to_thread(self._ensure_local_clone)

                readme = await asyncio.to_thread(self._read_readme)
                tree = await asyncio.to_thread(self._build_tree, self.repo_local, 0, max_depth)

                context = {
                    "repo_local_path": str(self.repo_local),
                    "readme": readme,
                    "tree": tree,
                    "issue": issue_payload or {},
                }
                context = json.dumps(context, indent=2, ensure_ascii=False)
                return ToolResult(output=json.dumps(context))

            except Exception as exc:
                return ToolResult(error=f"ProjectContextBuilder failed: {exc}")