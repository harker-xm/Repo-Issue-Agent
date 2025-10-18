# adapters/executor_adapter.py
from __future__ import annotations

import asyncio
import json
import os
import subprocess
from pathlib import Path
from typing import Optional, Any, Dict

from pydantic import Field, model_validator, PrivateAttr
from github import Github, GithubException

from app.tool.base import BaseTool, ToolResult
from app.tool.ask_human import AskHuman
from app.llm import LLM


class PlanExecutorAdapter(BaseTool):
    """
    Execute plan steps. Every destructive action requires AskHuman confirmation.
    plan: dict with "goal" and "steps" (list of dicts with action/target/detail)
    repo_local_path: path to local repo
    """

    name: str = "plan_executor_adapter"
    description: str = "Execute structured plan with human approvals."

    # ---------------- 输入参数（可外部传入） ----------------
    repo_local_path: Path
    github_token: Optional[str] = Field(default=None, exclude=True)
    github_repo_full: Optional[str] = Field(default=None, exclude=True)

    # ---------------- 运行时依赖（不序列化） ----------------
    _repo_local: Path = PrivateAttr()
    ask: AskHuman = Field(default_factory=AskHuman, exclude=True)
    llm: LLM = Field(default_factory=LLM, exclude=True)
    gh: Optional[Github] = Field(default=None, exclude=True)
    gh_repo: Optional[Any] = Field(default=None, exclude=True)

    # ---------------- Pydantic 初始化钩子 ----------------
    @model_validator(mode="after")
    def init_dependencies(self) -> "PlanExecutorAdapter":
        """完成原来 __init__ 里的所有逻辑。"""
        # 1. 路径
        self._repo_local = Path(self.repo_local_path)

        # 2. token / repo 归一化
        token = self.github_token or os.getenv("GITHUB_TOKEN")
        repo_url = self.github_repo_full or os.getenv("GITHUB_REPO_URL", "")

        if token and repo_url:
            self.gh = Github(token)
            if repo_url.startswith("http"):
                parts = repo_url.rstrip("/").split("/")
                repo_url = f"{parts[-2]}/{parts[-1].replace('.git', '')}"
            self.github_repo_full = repo_url
            try:
                self.gh_repo = self.gh.get_repo(repo_url)
            except GithubException as e:
                # 让错误在第一次使用时再抛，保持构造阶段不中断
                self.gh_repo = None
        else:
            self.gh = None
            self.gh_repo = None
        return self

    # ---------------- 原功能函数（逻辑未变） ----------------
    def _git(self, *args: str) -> str:
        proc = subprocess.run(
            ["git", *args], cwd=self._repo_local, capture_output=True, text=True
        )
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr)
        return proc.stdout

    async def execute(self, plan: dict, issue: dict) -> ToolResult:
        """完整执行逻辑与之前完全一致，仅把 self.repo_local / self.gh 等换成字段即可。"""
        branch = f"auto/fix-issue-{issue.get('number')}"
        try:
            self._git("fetch", "origin")
            self._git("checkout", "-B", branch, "origin/main")
        except Exception:
            self._git("checkout", "-B", branch)

        log: list[str] = [f"Using branch {branch}"]

        for idx, step in enumerate(plan.get("steps", [])):
            action = step.get("action")
            target = step.get("target")
            detail = step.get("detail", "")
            log.append(f"STEP {idx}: {action} target={target} detail={detail}")

            if action in {"identify", "identify_file"}:
                continue

            elif action in {"read", "read_file"}:
                file_path = self._repo_local / target
                if not file_path.exists():
                    log.append(f"File not found: {target}")
                    continue
                content = file_path.read_text(encoding="utf-8", errors="ignore")
                log.append(f"Read {target} -> {len(content)} bytes")

            elif action in {"modify", "modify_file"}:
                file_path = self._repo_local / target
                base_content = file_path.read_text(encoding="utf-8", errors="ignore") if file_path.exists() else ""
                prompt = (
                    f"Modify the file to achieve the goal.\nGoal: {plan.get('goal')}\nDetail: {detail}\n"
                    f"Original file path: {target}\nOriginal content (first 5000 chars):\n{base_content[:5000]}\n"
                    "Return ONLY the full new file content (no commentary)."
                )
                llm_resp = await self.llm.ask(prompt, max_tokens=2048, temperature=0.0)
                new_content = llm_resp.get("text") if isinstance(llm_resp, dict) else str(llm_resp)

                preview = new_content[:800] + ("\n...\n" + new_content[-400:] if len(new_content) > 1200 else "")
                inquire = (
                    f"Proposed modification to {target} (preview):\n{preview}\n\n"
                    "Approve and write to file? (y / n / edit)\n"
                    "If you type 'edit', paste full new content as JSON string in response."
                )
                human_resp = (await self.ask.execute(inquire)).strip().lower()
                if human_resp == "y":
                    file_path.parent.mkdir(parents=True, exist_ok=True)
                    file_path.write_text(new_content, encoding="utf-8")
                    self._git("add", target)
                    self._git("commit", "-m", f"Auto-fix: {plan.get('goal')} (issue #{issue.get('number')})")
                    log.append(f"Committed changes to {target}")
                elif human_resp == "edit":
                    edited = input("Paste full file content now, end with Ctrl-D / Ctrl-Z:\n")
                    file_path.write_text(edited, encoding="utf-8")
                    self._git("add", target)
                    self._git("commit", "-m", f"Manual edit auto-fix (issue #{issue.get('number')})")
                    log.append(f"Committed edited content for {target}")
                else:
                    log.append(f"User skipped modification for {target}")

            elif action in {"test", "run_tests"}:
                proc = await asyncio.to_thread(
                    subprocess.run,
                    ["pytest", "-q"],
                    cwd=self._repo_local,
                    capture_output=True,
                    text=True,
                )
                log.append(f"Tests return {proc.returncode}")
                log.append(proc.stdout[:2000] + proc.stderr[:2000])

            elif action in {"commit", "commit_changes"}:
                pass

            elif action in {"push", "push_branch"}:
                resp = (await self.ask.execute(f"Push branch {branch} to origin? (y/n)")).strip().lower()
                if resp == "y":
                    self._git("push", "--set-upstream", "origin", branch)
                    log.append(f"Pushed branch {branch}")
                else:
                    log.append("User declined to push branch")

            else:
                log.append(f"Unknown action: {action} - skipped")

        # PR / Merge 流程
        resp_pr = (await self.ask.execute(f"Create PR from branch {branch} to main? (y/n)")).strip().lower()
        if resp_pr == "y":
            try:
                self._git("push", "--set-upstream", "origin", branch)
            except Exception as e:
                log.append(f"Push failed: {e}")
                return ToolResult(output=json.dumps({"status": "push_failed", "log": log}))

            if not self.gh_repo:
                log.append("GitHub token/repo not configured - cannot create PR.")
                return ToolResult(output=json.dumps({"status": "no_github", "log": log}))

            title = f"Auto-fix issue #{issue.get('number')}: {plan.get('goal')}"
            body = f"Automated fix proposed by agent.\n\nExecution log (truncated):\n" + "\n".join(log[-50:])
            pr = self.gh_repo.create_pull(title=title, body=body, head=branch, base="main")
            log.append(f"Created PR #{pr.number}: {pr.html_url}")

            resp_merge = (await self.ask.execute(f"Merge PR #{pr.number} now? (y/n)")).strip().lower()
            if resp_merge == "y":
                res = pr.merge(commit_message=f"Merged by agent for issue #{issue.get('number')}")
                log.append(f"Merge result: {res}")
                return ToolResult(output=json.dumps({"status": "merged", "pr": {"number": pr.number, "url": pr.html_url}, "log": log}))
            return ToolResult(output=json.dumps({"status": "pr_created", "pr": {"number": pr.number, "url": pr.html_url}, "log": log}))