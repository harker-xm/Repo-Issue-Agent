# repo_main.py
import argparse
import asyncio

from app.agent.RepoIssueAgent import RepoAssistantAgent
from app.logger import logger


async def main():
    parser = argparse.ArgumentParser(
        description="Run RepoAssistantAgent to handle the latest open issue"
    )
    parser.add_argument(
        "--prompt",
        type=str,
        required=False,
        help="(Optional) custom prompt for the agent; defaults to 'Handle the latest open issue'",
    )
    args = parser.parse_args()

    # 创建并初始化代理
    agent = await RepoAssistantAgent.create(
        github_token="your github token",
        github_repo_url="https://github.com/your_name/your_project.git",
    )
    try:
        prompt = (
            args.prompt
            if args.prompt
            else input("Enter your prompt (press Enter for default): ").strip()
        )
        if not prompt:
            prompt = "Handle the latest open issue"  # 与 Agent 内置逻辑一致

        logger.warning("Processing your request...")
        await agent.run(prompt)
        logger.info("Request processing completed.")
    except KeyboardInterrupt:
        logger.warning("Operation interrupted.")
    finally:
        await agent.cleanup()


if __name__ == "__main__":
    asyncio.run(main())