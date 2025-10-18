# Repo-Issue-Agent

Repo-Issue-Agent 是一个基于 OpenManus 魔改的代码仓库，旨在自动化处理 GitHub 仓库中的未解决 Issue。它通过一系列智能工具和流程，帮助开发者快速检测、分析、提供建议和解决 Issue，并将修改合并回代码仓库。

## 执行流程

1. **检测新 Issues**  
   自动检测指定 GitHub 仓库中的未解决 Issues。

2. **构建项目上下文**  
   读取项目的基本信息和关键代码，与 Issues 结合，生成详细的上下文信息。

3. **规划与执行**  
   利用 OpenManus 的 Planning Flow，先规划再执行，生成详细的执行方案。

4. **人工确认**  
   调用 OpenManus 的 `ask_human` 工具，输出执行方案并询问是否满意（`y/n`）。如果选择 `y`，则按照规划继续执行；如果选择 `n`，允许手动更改执行方案后再执行。

5. **合并修改**  
   再次调用 `ask_human` 工具，询问是否将修改合并至 GitHub 仓库（`y/n`）。如果选择 `y`，则合并修改并调用 `terminate` 工具结束流程；如果选择 `n`，直接调用 `terminate` 工具结束流程。

## 如何使用

### 1. 拉取代码

将 Repo-Issue-Agent 代码克隆到本地。本项目结构与 OpenManus 对应。

```bash
git clone https://github.com/your_name/Repo-Issue-Agent.git
cd Repo-Issue-Agent
```

### 2. 拉取代码

在根目录的 run_RepoAgent.py 文件中配置你的 GitHub Token 和仓库 URL。

```python
agent = await RepoAssistantAgent.create(
    github_token="your_github_token",
    github_repo_url="https://github.com/your_name/your_project.git",
)
```
### 3. 配置 LLM 服务

在 config/config.toml 文件中配置你的 LLM 服务 URL 和 API Key 等信息

```toml
[llm]
model = "model_name"       # 使用的 LLM 模型
base_url = "your_url"      # API 端点 URL
api_key = "Your_API_key"   # 你的 API Key
max_tokens = 8192          # 响应中的最大令牌数
temperature = 0.0
```
### 4. 运行程序

运行 run_RepoAgent.py 文件，启动 Agent

```python
python run_RepoAgent.py
```

## 注意事项
* 确保你的 GitHub Token 具有足够的权限（至少包括 repo 权限）
* 如果你使用的是私有仓库，请确保 Token 和仓库 URL 配置正确
* 如果你使用的是自定义 LLM 服务，请确保 config/config.toml 中的配置信息正确无误
