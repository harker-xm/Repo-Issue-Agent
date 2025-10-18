# Repo-Issue-Agent

**Repo-Issue-Agent** is a customized project based on **OpenManus**, designed to automate the handling of unresolved GitHub Issues.  
It integrates a set of intelligent tools and workflows to help developers quickly detect, analyze, suggest fixes, and resolve Issues, then merge the modifications back into the repository.

## Execution Flow

1. **Detect New Issues**  
   Automatically detects unresolved Issues in the specified GitHub repository.

2. **Build Project Context**  
   Reads the project’s basic information and key source code, combines it with the Issues, and generates detailed contextual information.

3. **Planning and Execution**  
   Uses OpenManus’s Planning Flow — first planning, then executing — to produce a detailed execution plan.

4. **Human Confirmation**  
   Invokes OpenManus’s `ask_human` tool to output the execution plan and ask for confirmation (`y/n`).  
   - If `y`, the plan proceeds as designed.  
   - If `n`, manual modification of the plan is allowed before execution continues.

5. **Merge Changes**  
   Calls `ask_human` again to ask whether to merge the modifications into the GitHub repository (`y/n`).  
   - If `y`, merges the changes and calls the `terminate` tool to end the process.  
   - If `n`, calls `terminate` directly to end the process without merging.

## How to Use

### 1. Clone the Repository

Clone the Repo-Issue-Agent code to your local environment.  
This project’s structure corresponds to **OpenManus**.

```bash
git clone https://github.com/your_name/Repo-Issue-Agent.git
cd Repo-Issue-Agent
```
### 2. Configure GitHub Access

Edit the run_RepoAgent.py file in the root directory to set your GitHub Token and repository URL
```python
agent = await RepoAssistantAgent.create(
    github_token="your_github_token",
    github_repo_url="https://github.com/your_name/your_project.git",
)
```

### 3. Configure LLM Service

Set up your LLM service configuration in config/config.toml

```python
[llm]
model = "model_name"       # The LLM model to use
base_url = "your_url"      # API endpoint URL
api_key = "Your_API_key"   # Your API key
max_tokens = 8192          # Maximum number of tokens in response
temperature = 0.0
```
### 4. Run the Program
Execute run_RepoAgent.py to start the agent.
```python
python run_RepoAgent.py
```

Notes

* Ensure your GitHub Token has sufficient permissions (at least repo scope).

* For private repositories, make sure both the Token and repository URL are correctly configured.

* When using a custom LLM service, verify that all configurations in config/config.toml are accurate.
