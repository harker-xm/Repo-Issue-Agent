# app/agent/repo_assistant_agent.py
import json
from typing import Dict, List, Optional, Any
from pydantic import Field, PrivateAttr

from app.agent.toolcall import ToolCallAgent
from app.config import config
from app.logger import logger
from app.prompt.manus import SYSTEM_PROMPT, NEXT_STEP_PROMPT
from app.schema import Message
from app.tool import ToolCollection, Terminate
from app.tool.ask_human import AskHuman
from app.tool.RepoIssueTools.github_issue_watcher import GitHubIssueWatcher
from app.tool.RepoIssueTools.project_context_builder import ProjectContextBuilder
from app.tool.RepoIssueTools.planner_adapter import PlannerAdapter
from app.tool.RepoIssueTools.executor_adapter import PlanExecutorAdapter
from app.tool.base import ToolResult
import os


class RepoAssistantAgent(ToolCallAgent):
    name: str = "repo_issues_assistant"
    description: str = (
        "Detect latest open issue, build repo context, generate plan, "
        "ask human for approval and execute step-by-step."
    )

    system_prompt: str = SYSTEM_PROMPT.format(directory=config.workspace_root)
    next_step_prompt: str = NEXT_STEP_PROMPT
    max_steps: int = 30

    # ---------------- 运行期私有属性 ----------------
    _github_token: str = PrivateAttr()
    _github_repo_url: str = PrivateAttr()

    # ---------------- 工具集合（延迟初始化）----------------
    available_tools: ToolCollection = Field(default_factory=ToolCollection)

    # ---------------- 状态机缓存 ----------------
    _issue: Optional[Dict[str, Any]] = PrivateAttr(default=None)
    _context: Optional[Dict[str, Any]] = PrivateAttr(default=None)
    _plan_json: Optional[Dict[str, Any]] = PrivateAttr(default=None)
    _plan_id: Optional[str] = PrivateAttr(default=None)
    _step_index: int = PrivateAttr(default=0)
    _awaiting_human_approval: bool = PrivateAttr(default=False)
    _awaiting_merge_decision: bool = PrivateAttr(default=False)
    _human_response: Optional[str] = PrivateAttr(default=None)

    @classmethod
    async def create(
            cls,
            github_token: Optional[str] = None,
            github_repo_url: Optional[str] = None,
            **kwargs,
    ) -> "RepoAssistantAgent":
        """构造实例并注入 token/repo_url，再动态生成工具集合。"""
        token = github_token or os.getenv("GITHUB_TOKEN", "")
        repo_url = github_repo_url or os.getenv("GITHUB_REPO_URL", "")
        if not token or not repo_url:
            raise RuntimeError(
                "RepoAssistantAgent requires github_token & github_repo_url "
                "(args or env GITHUB_TOKEN / GITHUB_REPO_URL)"
            )

        instance = cls(**kwargs)
        object.__setattr__(instance, "_github_token", token)
        object.__setattr__(instance, "_github_repo_url", repo_url)

        tools = ToolCollection(
            GitHubIssueWatcher(token=token, repo_url=repo_url),
            ProjectContextBuilder(repo_full_name=repo_url),
            PlannerAdapter(),
            AskHuman(),
            PlanExecutorAdapter(
                github_token=token,
                github_repo_full=repo_url,
                repo_local_path="C:/Users/IVANXIE/Desktop/front-end-web-finals--master",
            ),
            Terminate(),
        )
        instance.available_tools = tools
        return instance

    async def think(self) -> bool:
        """重写think方法，控制整个工作流程"""
        # 初始化消息历史
        if not self.memory.messages:
            self.memory.add_message(Message.system_message(self.system_prompt))
            self.memory.add_message(
                Message.user_message("Please start by detecting the latest open issue in my repository.")
            )
            return await super().think()

        # 处理人类响应
        if self._human_response:
            await self._process_human_response()
            self._human_response = None
            return await super().think()

        # 检查是否需要等待人类批准
        if self._awaiting_human_approval or self._awaiting_merge_decision:
            logger.info("Waiting for human response, skipping think cycle")
            return False

        # 基于当前状态决定下一步
        last_message = self.memory.messages[-1] if self.memory.messages else None

        if last_message and last_message.role == "tool":
            await self._process_tool_result(last_message)

        return await super().think()

    async def _process_tool_result(self, tool_message: Message):
        """处理工具执行结果并更新状态机"""
        content = tool_message.content.lower()

        # 检测issue工具的结果
        if "github_issue_watcher" in content:
            if "executed successfully" in content:
                # 从工具消息中提取issue信息
                try:
                    # 这里需要根据实际工具返回格式解析
                    # 假设工具返回了结构化数据
                    if "no open issue" in content:
                        logger.info("No issues found, workflow complete")
                        self.memory.add_message(
                            Message.user_message("No open issues detected. Workflow complete.")
                        )
                    else:
                        logger.info("Issue detected, moving to context building")
                        self.memory.add_message(
                            Message.user_message("Great! Now please build the project context for this issue.")
                        )
                except Exception as e:
                    logger.error(f"Failed to parse issue result: {e}")

        # 项目上下文构建结果
        elif "project_context_builder" in content and "executed successfully" in content:
            logger.info("Context built, moving to planning")
            self.memory.add_message(
                Message.user_message("Context built successfully. Now generate an execution plan.")
            )

        # 规划器结果
        elif "planner_adapter" in content and "executed successfully" in content:
            logger.info("Plan generated, requesting human approval")
            self._awaiting_human_approval = True
            # 触发ask_human工具调用将在下一个think周期处理

        # 执行器结果
        elif "plan_executor_adapter" in content and "executed successfully" in content:
            logger.info("Plan execution completed, asking about merge")
            self._awaiting_merge_decision = True

        # ask_human工具的结果
        elif "ask_human" in content:
            self._human_response = content
            # 在下一个think周期中处理

    async def _process_human_response(self):
        """处理人类的响应"""
        if self._awaiting_human_approval:
            await self._handle_plan_approval()
        elif self._awaiting_merge_decision:
            await self._handle_merge_decision()

    async def _handle_plan_approval(self):
        """处理计划批准响应"""
        self._awaiting_human_approval = False

        if "y" in self._human_response:
            logger.info("Plan approved, starting execution")
            self.memory.add_message(
                Message.user_message("Plan approved! Please execute the plan step by step.")
            )
        elif "n" in self._human_response:
            logger.info("Plan rejected, terminating")
            self.memory.add_message(
                Message.user_message("Plan rejected. Terminating workflow.")
            )
            # 触发terminate工具调用
        else:
            logger.info("User provided custom input for plan")
            self.memory.add_message(
                Message.user_message(
                    f"User provided input: {self._human_response}. Please adjust the plan accordingly.")
            )

    async def _handle_merge_decision(self):
        """处理合并决策响应"""
        self._awaiting_merge_decision = False

        if "y" in self._human_response:
            logger.info("Merge approved, completing workflow")
            self.memory.add_message(
                Message.user_message("Merge approved! Please complete the workflow.")
            )
        else:
            logger.info("Merge rejected, terminating")
            self.memory.add_message(
                Message.user_message("Merge rejected. Terminating workflow.")
            )

    async def on_tool_result(self, tool_name: str, result: ToolResult, tool_call_id: str):
        """工具执行完成后的回调（如果父类支持）"""
        logger.debug(f"Tool {tool_name} completed with result: {result.output}")

        # 更新内部状态
        if tool_name == "github_issue_watcher" and result.output:
            self._issue = result.output
        elif tool_name == "project_context_builder" and result.output:
            self._context = result.output
        elif tool_name == "planner_adapter" and result.output:
            plan_data = result.output
            self._plan_id = plan_data.get("plan_id")
            self._plan_json = plan_data.get("plan")

            # 自动触发人类批准询问
            if self._plan_json:
                await self._trigger_plan_approval()

    async def _trigger_plan_approval(self):
        """触发计划批准询问"""
        plan_preview = f"Plan ID: {self._plan_id}\nGoal: {self._plan_json.get('goal')}\nSteps:\n"
        for s in self._plan_json.get("steps", []):
            plan_preview += f"- {s.get('action')} {s.get('target', '')}: {s.get('detail', '')}\n"

        # 通过添加用户消息来触发ask_human工具调用
        self.memory.add_message(
            Message.user_message(
                f"I need your approval for this plan:\n{plan_preview}\n\n"
                "Should I execute this plan? Please respond with 'y' for yes, 'n' for no, "
                "or provide specific modifications."
            )
        )