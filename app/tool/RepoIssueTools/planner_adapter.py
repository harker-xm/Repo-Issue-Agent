# adapters/planner_adapter.py
import asyncio
import json
import uuid
import re
from typing import Optional, Any, Dict

from pydantic import Field, PrivateAttr

from app.llm import LLM
from app.tool.planning import PlanningTool
from app.tool.base import BaseTool, ToolResult

_PLANNER_ADAPTER_DESCRIPTION = """\
Generate a structured execution plan (JSON) via LLM based on repository context
and register it into PlanningTool for later tracking.

Input context must contain:
  readme: str          – project README text
  tree: dict           – limited-depth directory tree
  issue: dict          – issue fields (title, body, etc.)

Returns:
  plan_id   – registered plan identifier
  plan      – raw JSON plan produced by LLM
"""


class PlannerAdapter(BaseTool):
    name: str = "planner_adapter"
    description: str = _PLANNER_ADAPTER_DESCRIPTION

    # ── 供 Agent 解析的参数模式 ───────────────────────────────────────────────
    parameters: dict = {
        "type": "object",
        "properties": {
            "context": {
                "type": "object",
                "description": "Project context dict with keys: readme, tree, issue",
            }
        },
        "required": ["context"],
    }

    # ── 运行时依赖（不序列化） ────────────────────────────────────────────────
    lock: asyncio.Lock = Field(default_factory=asyncio.Lock, exclude=True)
    llm: Optional[LLM] = Field(default=None, exclude=True)
    planning_tool: Optional[PlanningTool] = Field(default=None, exclude=True)

    # 私有属性，完全跳过 pydantic 校验
    _llm: LLM = PrivateAttr()
    _planning_tool: PlanningTool = PrivateAttr()

    def __init__(self, llm: Optional[LLM] = None, planning_tool: Optional[PlanningTool] = None):
        # 先让 pydantic 完成自身初始化
        super().__init__()
        object.__setattr__(self, "_llm", llm or LLM())
        object.__setattr__(self, "_planning_tool", planning_tool or PlanningTool())

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------
    async def _llm_generate_plan(self, context: Dict[str, Any]) -> Dict[str, Any]:
        print('📃正在使用大语言模型{}，生成计划...'.format(self._llm.model))
        prompt = f"""
You are an expert software engineer agent. Given the README (brief), repository tree summary, and an issue,
please propose a step-by-step execution plan to fix the issue. Output JSON with structure:
{{
  "goal": "...", 
  "steps": [
    {{"title":"step title","action":"identify/read/modify/test/commit/push/etc","target":"rel/path or None","detail":"..."}},
    ...
  ]
}}

Repo README (first 2000 chars):
{context["readme"][:2000]}

Repo tree (short):
{json.dumps(context["tree"], ensure_ascii=False)[:2000]}

Issue:
Title: {context["issue"].get("title")}
Body: {context["issue"].get("body")[:2000]}

Produce ONLY JSON.
"""
        resp = await self._llm.ask(prompt, max_tokens=1024, temperature=0.0)
        text = resp.get("text") if isinstance(resp, dict) else str(resp)

        try:
            return json.loads(text)
        except Exception:
            # 容错抽取
            m = re.search(r"\{.*\}", text, re.S)
            if not m:
                raise RuntimeError("LLM returned non-JSON plan: " + text[:500])
            return json.loads(m.group(0))

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------
    async def execute(
        self,
        context: Dict[str, Any],
        **kwargs,
    ) -> ToolResult:
        """
        Args:
            context: dict 必须包含 readme / tree / issue
        Returns:
            ToolResult(output={"plan_id": str, "plan": dict, "planning_tool_res": Any})
        """
        async with self.lock:
            try:
                plan_json = await self._llm_generate_plan(context)
                plan_id = f"auto-{context['issue'].get('number')}-{uuid.uuid4().hex[:6]}"

                steps_for_planning = [
                    f"{s.get('title') or s.get('action', '')} -- {s.get('detail', '')}"
                    for s in plan_json.get("steps", [])
                ]

                create_res = await self._planning_tool.execute(
                    command="create",
                    plan_id=plan_id,
                    title=plan_json.get("goal", ""),
                    steps=steps_for_planning,
                )
                print("[DEBUG] planning_tool output:", create_res.output)
                print("[DEBUG] planning_tool error :", create_res.error)
                if create_res.error:
                    return ToolResult(error=f"PlanningTool error: {create_res.error}")

                return ToolResult(
                    output={
                        "plan_id": plan_id,
                        "plan": plan_json,
                        "planning_tool_res": create_res.output,
                    }
                )

            except Exception as exc:
                return ToolResult(error=f"PlannerAdapter failed: {exc}")