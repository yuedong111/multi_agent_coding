# Harness Agent

一个最小可运行的多 agent 代码开发 harness。它把你总结里的 Task Graph、Agent Loop、Worktree/目录隔离、Skill Loading、MessageBus、局部微调合在一起，目标是：

- 一键指定项目根目录和目标，让多 agent 自动规划、生成结构、写代码、测试、产出上线说明。
- 每个 agent 可单独配置模型、`api_key_env` 和 `base_url`。
- 通过 `skills/` 目录复用 Codex/Claude Code 风格工作流约束。
- 后续可以用 refine 命令做局部微调，而不是整项目重来。

## 快速开始

1. 复制配置：

```powershell
Copy-Item configs\agents.example.json agents.local.json
```

2. 设置密钥：

```powershell
$env:OPENAI_API_KEY="你的 key"
```

3. 一键生成项目：

```powershell
python -m harness_agent run --root C:\path\to\project --goal "做一个 FastAPI TODO API，包含测试和 README" --config agents.local.json
```

4. 局部微调：

```powershell
python -m harness_agent refine --root C:\path\to\project --request "只调整错误返回格式，保持接口路径不变" --config agents.local.json
```

也可以限制改动范围：

```powershell
python -m harness_agent refine --root C:\path\to\project --request "优化登录错误文案" --files src/auth.py,tests/test_auth.py --config agents.local.json
```

## 目录产物

运行后，目标项目根目录会出现：

- `.tasks/`：任务图，JSON 持久化。
- `.team/`：队友配置、消息 inbox、事件日志。
- `.harness/`：运行摘要、计划、验证报告、上线说明。

## Agent 分工

- `lead`：拆任务、维护任务图、收敛结果。
- `architect`：输出项目结构和模块边界。
- `coder`：按结构创建/修改代码。
- `tester`：生成并运行测试命令。
- `reviewer`：做代码审查和局部修复建议。
- `release`：生成上线说明、运行方式和风险清单。

## 配置说明

配置文件是 JSON，避免额外依赖。每个 agent 支持：

- `model`：模型名。
- `base_url`：OpenAI-compatible endpoint，例如 `https://api.openai.com/v1`。
- `api_key_env`：从哪个环境变量读取 key。
- `temperature`：温度。
- `enabled`：是否启用。

你可以让不同 agent 使用不同模型，比如 planner 用强模型，tester/release 用便宜模型。

## 支持的工具

LLM 通过 JSON action 调用工具，runtime 执行：

- `list_files`
- `read_file`
- `write_file`
- `append_file`
- `run_command`
- `create_task`
- `update_task`
- `send_message`
- `finish`

工具只在 `--root` 指定目录下操作，避免 agent 随意改到别处。

## 设计取舍

这是一个偏工程骨架的 harness，不追求一次塞进所有平台能力。它的重点是：

- 任务与消息都落盘，便于恢复。
- 每轮输出必须是结构化 JSON，便于 dispatch。
- skills 按需拼进 agent system prompt。
- refine 走同一套任务图，只增加变更任务，降低重写概率。
