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
        """重写think方法，确保消息历史正确"""
        if not self.memory.messages:
            # 如果没有任何消息，添加系统提示
            self.memory.add_message(Message.system_message(self.system_prompt))
            # 添加初始用户消息
            self.memory.add_message(
                Message.user_message("Please start by detecting the latest open issue in my repository."))

        result = await super().think()
        return result

    async def act(self) -> bool:
        """重写act方法，正确处理工具调用和消息历史"""
        if not self.tool_calls:
            logger.warning("No tool calls generated, finish.")
            return False

        # 收集所有工具调用的结果
        tool_results = []

        for call in self.tool_calls:
            logger.info(f"Executing tool call: {call.function.name}")

            # 根据工具名称处理不同的参数
            extra_params = {}
            if call.function.name == "project_context_builder" and self._issue:
                extra_params = {"issue_payload": self._issue}
            elif call.function.name == "planner_adapter" and self._context:
                extra_params = {"context": self._context}
            elif call.function.name == "plan_executor_adapter" and self._plan_json and self._issue:
                extra_params = {"plan_json": self._plan_json, "issue": self._issue}

            # 执行工具调用
            res = await self._exec_tool_call(call, **extra_params)

            # 处理特定工具的状态更新
            await self._handle_tool_result(call.function.name, res)

            # 将工具结果添加到消息历史
            if res.output:
                content = f"Tool {call.function.name} executed successfully: {res.output}"
            else:
                content = f"Tool {call.function.name} failed: {res.error}"

            tool_results.append({
                "tool_call_id": call.id,
                "content": content,
                "result": res
            })

        # 将所有工具结果一次性添加到消息历史
        for result in tool_results:
            self.memory.add_message(Message.tool_message(
                content=result["content"],
                tool_call_id=result["tool_call_id"]
            ))

        return True

    async def _handle_tool_result(self, tool_name: str, result: ToolResult):
        """处理特定工具的结果并更新状态"""
        if result.error:
            logger.error(f"Tool {tool_name} error: {result.error}")
            return

        if tool_name == "github_issue_watcher":
            self._issue = result.output
            if not self._issue:
                logger.info("No open issue detected.")

        elif tool_name == "project_context_builder":
            self._context = result.output
            logger.info("Project context built successfully.")

        elif tool_name == "planner_adapter":
            if result.output:
                self._plan_id = result.output.get("plan_id")
                self._plan_json = result.output.get("plan")
                logger.info(f"Plan generated with ID: {self._plan_id}")

                # 询问用户是否批准计划
                confirm = await self._ask_human_approval()
                if confirm != "y":
                    logger.info("User rejected the plan.")
                    # 添加用户拒绝的消息到历史
                    self.memory.add_message(Message.user_message(
                        f"User rejected the plan. Response: {confirm}"
                    ))

        elif tool_name == "plan_executor_adapter":
            self._step_index += 1
            logger.info(f"Plan execution step {self._step_index} completed.")

    async def _exec_tool_call(self, call, **extra) -> ToolResult:
        """执行工具调用"""
        import json
        try:
            args = json.loads(call.function.arguments) if isinstance(call.function.arguments,
                                                                     str) else call.function.arguments
        except json.JSONDecodeError as e:
            return ToolResult(error=f"Invalid tool arguments: {e}")

        # 合并参数
        tool_input = {**args, **extra}

        # 记录工具调用
        logger.debug(f"Executing {call.function.name} with args: {tool_input}")

        return await self.available_tools.execute(
            name=call.function.name,
            tool_input=tool_input,
        )

    async def _ask_human_approval(self) -> str:
        """询问用户是否批准计划"""
        if not self._plan_json:
            return "n"

        plan_preview = f"Plan ID: {self._plan_id}\nGoal: {self._plan_json.get('goal')}\nSteps:\n"
        for s in self._plan_json.get("steps", []):
            plan_preview += f"- {s.get('action')} {s.get('target', '')}: {s.get('detail', '')}\n"

        res = await self.available_tools.execute(
            "ask_human",
            tool_input={
                "prompt": f"Proposed plan:\n{plan_preview}\nExecute? (y/n/edit-json)\n"
            }
        )
        return res.output.strip().lower() if res.output else "n"