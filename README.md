# Harness Agent

一个最小可运行的多 agent 代码开发 harness。它把你总结里的 Task Graph、Agent Loop、Worktree/目录隔离、Skill Loading、MessageBus、局部微调合在一起，目标是：

- 按“需求计划审核 -> agent prompt 审核 -> 执行实现”的三阶段流程，让多 agent 自动生成结构、写代码、测试、产出上线说明。
- 每个 agent 可单独配置模型、`provider`、`api_key_env` 和 `base_url`。
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

3. 在目标项目根目录写入目标文件：

```powershell
Set-Content C:\path\to\project\goal.md "做一个 FastAPI TODO API，包含测试和 README"
```

build 类命令固定从 `--root` 下的 `goal.md` 读取目标。

4. 生成需求计划，供人工审核：

```powershell
python -m harness_agent plan --root C:\path\to\project --config agents.local.json
```

该阶段会生成 `docs/requirements.md`。请人工审核并补充业务规则、边界条件、权限、状态流转和异常语义。

5. 生成各 agent 的动态执行 prompt，供人工审核：

```powershell
python -m harness_agent prompts --root C:\path\to\project --config agents.local.json
```

该阶段会生成 `.harness/agent-prompts/{agent}.md`。请人工审核每个 agent 的职责范围、需求引用、任务边界和风险。

6. 按已审核的 requirements 和 agent prompts 执行：

```powershell
python -m harness_agent execute --root C:\path\to\project --config agents.local.json
```

默认会读取仓库根目录的 `AGENTS.md` 作为所有 agent 的全局提示词。每个命令都可以显式指定：

```powershell
python -m harness_agent execute --root C:\path\to\project --config agents.local.json --agents-md AGENTS.md
```

`run` 命令仍然可用，但它会连续执行 `plan`、`prompts`、`execute`，不会在审核点暂停。需要人工审核时，请使用上面的三步流程。

7. 局部微调：

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
- `.harness/agent-prompts/`：每个 agent 的动态执行 prompt，用于指导本轮执行，也供人工审核。
- `goal.md`：build 类命令默认读取的用户目标文件。
- `docs/requirements.md`：规划阶段向用户澄清后的业务规则和需求结论。

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
- `provider`：模型供应商适配器，默认 `openai-compatible`，也支持 `anthropic`。
- `base_url`：供应商 API endpoint，例如 `https://api.openai.com/v1` 或 `https://api.anthropic.com/v1`。
- `api_key_env`：从哪个环境变量读取 key。
- `temperature`：温度。
- `max_tokens`：可选，限制模型最多生成的 token 数；Anthropic 原生接口未配置时默认使用 `4096`。
- `max_parse_retries`：模型输出不是合法 JSON 工具调用时，最多让模型重试几次。
- `enabled`：是否启用。

推荐先复制示例配置，再在本地文件里调整：

```powershell
Copy-Item configs\agents.example.json agents.local.json
```

配置由两层组成：

- `defaults`：所有 agent 的默认配置。
- `agents`：按角色名分别配置 agent；这里写的字段会覆盖 `defaults` 里的同名字段。

因此，如果大多数 agent 使用同一个供应商，只需要在 `defaults` 写一次 `base_url`、`api_key_env` 和默认 `model`；需要特殊模型或特殊供应商的 agent，再在自己的配置块里覆盖。

示例：

```json
{
  "defaults": {
    "provider": "openai-compatible",
    "base_url": "https://api.openai.com/v1",
    "api_key_env": "OPENAI_API_KEY",
    "model": "gpt-4.1-mini",
    "temperature": 0.2,
    "max_steps": 12,
    "max_parse_retries": 2
  },
  "agents": {
    "lead": {
      "enabled": true,
      "model": "gpt-4.1",
      "role": "负责规划、澄清和任务拆分。"
    },
    "coder": {
      "enabled": true,
      "provider": "anthropic",
      "base_url": "https://api.anthropic.com/v1",
      "api_key_env": "ANTHROPIC_API_KEY",
      "model": "claude-sonnet-4-20250514",
      "max_tokens": 4096,
      "role": "负责最小必要代码实现。"
    },
    "tester": {
      "enabled": true,
      "model": "gpt-4.1-mini",
      "role": "负责补测试和运行验证命令。"
    }
  }
}
```

上面示例中：

- `lead` 没有写 `base_url` 和 `api_key_env`，所以继承 `defaults`，使用 `OPENAI_API_KEY`。
- `coder` 覆盖了 `provider`、`base_url`、`api_key_env`、`model` 和 `max_tokens`，所以会向 `https://api.anthropic.com/v1/messages` 发请求，并从 `ANTHROPIC_API_KEY` 读取 key。
- `tester` 只覆盖了 `model`，其他连接参数继续继承 `defaults`。

需要为多个供应商配置 key 时，在运行命令前设置对应环境变量：

```powershell
$env:OPENAI_API_KEY="openai-key"
$env:ANTHROPIC_API_KEY="anthropic-key"
python -m harness_agent execute --root C:\path\to\project --config agents.local.json
```

`base_url` 应填写到 API 版本前缀为止，不要包含具体资源路径。`openai-compatible` 会自动追加 `/chat/completions`；`anthropic` 会自动追加 `/messages`。

模型每轮输出必须是一个 JSON 工具调用。如果输出了不完整 JSON、缺少 `tool` 字段，或把 JSON 写坏了，runtime 会把解析错误发回给同一个 agent，要求它重新生成。默认最多重试 2 次；如果仍然失败，该 agent 本轮会以 `failed` 结束，不会把坏内容派发给工具执行。

运行流程按固定角色名调度：`lead`、`architect`、`coder`、`tester`、`reviewer`、`release`。如果某个角色没有在配置文件的 `agents` 中声明，或声明了但设置 `"enabled": false`，runtime 会跳过该角色对应的流程。额外声明但不在当前调度顺序里的角色不会自动运行，除非同步调整 workflow 里的调度顺序。

你可以让不同 agent 使用不同模型，比如 planner 用强模型，tester/release 用便宜模型。

## 动态执行 Prompt

`agents` 配置里的 `role` 是静态角色职责，适合描述某个 agent 长期负责什么；具体到某次运行的执行边界、需求快照、任务图和审核依据，会落到 `.harness/agent-prompts/{agent}.md`。

每个 agent 启动前，runtime 会检查对应 prompt 文件：

- 如果文件不存在或内容为空，会根据当前 `docs/requirements.md`、任务图、角色职责和本次 objective 生成默认 prompt。
- 如果文件已经存在且内容非空，runtime 会保留该文件，不会覆盖人工或上游流程已生成的内容。
- 同一份 prompt 会追加进该 agent 的执行 objective，因此它既是运行依据，也是人工审核材料。

分阶段 coder 的代码对接由两层保证：

- 文件系统承接：每个 agent 先在隔离副本中执行；只有该阶段成功完成时，runtime 才把改动 merge 回目标项目根目录。所以下一个 coder 阶段启动时，项目文件系统已经包含上一阶段成功生成的代码。
- 上下文承接：每个 agent 启动时，runtime 会在 objective 里附加当前目录结构、已完成阶段摘要、前序阶段 `changedFiles` 和对接要求。后续 coder 会被要求先用 `list_files` 查看目录，并用 `read_file` 读取将要修改或依赖的现有文件，再继续实现当前业务切片。

不建议把前面所有代码全文一次性塞进 prompt。更稳的方式是提供目录树和变更清单，让 agent 通过工具按需读取相关文件：这样既能保持上下文小，又能避免读到过期代码。对于跨切片必须共享的契约，例如公共接口、数据模型、路由约定或配置入口，应优先让 architect 在前置阶段生成清晰边界，并让每个 coder 阶段读取这些文件后再修改。

如果后续阶段需要在已生成脚本中插入代码，推荐流程是：

1. 用 `read_file` 读取目标文件。
2. 找到唯一、稳定的上下文锚点，例如某个函数、配置块或导出列表。
3. 用 `replace_text` 把这段锚点替换为“原锚点 + 新代码”，完成插入。
4. 运行相关测试或命令验证衔接。

`replace_text` 会要求 `old` 文本默认只出现一次；如果锚点重复，会返回错误，提示 agent 选择更具体的上下文，避免把代码插错位置。对于需要重构整个文件的情况，才使用 `write_file` 写回完整文件；对于单纯在文件末尾增加内容，才使用 `append_file`。

这种设计把业务规则和执行职责分开：`docs/requirements.md` 记录已确认业务规则，任务图记录工作拆分，`.harness/agent-prompts/*.md` 记录每个 agent 本轮应该如何按这些材料执行。

每个 agent 的 `skills` 是默认加载的技能。运行时还会扫描 `--skills-dir` 下所有 `SKILL.md`，把名称和描述提供给 agent。agent 如果发现某个未默认加载的技能适合当前任务，可以先调用 `load_skill` 工具按需加载，再继续执行。

## 支持的工具

LLM 通过 JSON action 调用工具，runtime 执行：

- `list_files`
- `read_file`
- `write_file`
- `append_file`
- `replace_text`
- `run_command`
- `create_task`
- `update_task`
- `send_message`
- `ask_user`
- `record_requirement`
- `load_skill`
- `finish`

工具只在 `--root` 指定目录下操作，避免 agent 随意改到别处。

## 三阶段审核流

build 流程拆成三个可单独运行的阶段，方便在关键材料进入执行前进行人工审核。

- build 类命令固定从目标项目根目录的 `goal.md` 读取用户目标；命令行不再通过 `--goal` 传入长字符串。
- `plan`：如果 `docs/requirements.md` 不存在或为空，生成需求计划模板；如果文件已有非空内容，则跳过并保留原文。该文件是业务规则和需求结论的审核入口。
- `prompts`：读取已审核的 `docs/requirements.md`，创建基础任务图，并为执行阶段的 agent 生成 `.harness/agent-prompts/{agent}.md`；如果 prompt 文件已有非空内容，则跳过并保留原文。
- `execute`：要求 `docs/requirements.md` 和执行阶段所需的 agent prompt 都已存在且非空，然后按 prompt 运行 agent 完成各自职责。

在执行阶段，如果某个 agent 发现会影响代码行为的业务规则仍不明确，可以调用 `ask_user`。runtime 会暂停当前 agent，直接在命令行向用户提问。用户回答后，答案会自动追加到 `docs/requirements.md`，再作为工具结果返回给 agent。

## 设计取舍

这是一个偏工程骨架的 harness，不追求一次塞进所有平台能力。它的重点是：

- 任务与消息都落盘，便于恢复。
- 每轮输出必须是结构化 JSON，便于 dispatch。
- 配置里的默认 skills 会拼进 agent system prompt；其他已发现 skills 可通过 `load_skill` 按需引用。
- `AGENTS.md` 提供所有 agent 共用的边界、防护和协作规则。
- refine 走同一套任务图，只增加变更任务，降低重写概率。
