# Harness Agent 产品文档

## 1. 产品定位

Harness Agent 是一个最小可运行的多 agent 代码开发 harness。它把需求澄清、任务拆解、按角色执行、测试审查和发布说明组织成可审核的工程流程，让用户在真正执行代码生成前先检查关键材料。

产品重点不是替代完整项目管理系统，而是给本地代码项目提供一条可恢复、可审计、可分阶段暂停的自动化开发链路。

## 2. 目标用户

- 需要用多个 LLM agent 协作生成或修改代码的开发者。
- 希望在代码生成前审核需求、任务边界和 agent prompt 的团队。
- 需要对 agent 写入文件、执行命令和业务澄清过程保留本地痕迹的使用者。

## 3. 核心目标

- 所有 agent 只在目标项目根目录内工作。
- 业务规则必须先进入 `docs/requirements.md`，再作为实现和审查依据。
- 文件归属和允许修改路径进入 `docs/file-plan.md`，再作为 coder 分片落文件依据。
- build 流程拆成 `plan`、`prompts`、`execute` 三个可单独运行的阶段。
- 动态执行 prompt 写入 `.harness/agent-prompts/`，便于人工审核。
- 每个 coder 阶段后运行静态扫描，提前发现重复定义、循环导入和语法错误；扫描覆盖 Python、JS/TS、Go、C/C++、Java、Dart、PHP、C# 等常见工程语言。
- agent 执行前创建检查点，并在隔离目录中运行；失败时回滚，成功时合并变更。
- 工具调用只接受结构化 JSON action，降低自由文本误操作风险。

## 4. 主要命令

### 4.1 plan

输入：目标项目根目录的 `goal.md`。

行为：

- 初始化 `.team/config.json`。
- 如果 `docs/requirements.md` 已存在且非空，则跳过生成并保留原文。
- 如果需求文档缺失或为空，则生成需求审核模板。

产物：

- `docs/requirements.md`

### 4.2 prompts

输入：已审核的 `docs/requirements.md` 和 `goal.md`。

行为：

- 需求文档必须存在且非空。
- 创建基础任务图。
- 生成或保留 `docs/file-plan.md`，用于人工审核每个 coder 分片允许修改的路径。
- 为 architect、tester、integrator、reviewer、release 生成动态执行 prompt。
- 为 coder 生成一个审核总览 `coder.md`。
- 按需求文档长度把 coder 执行内容拆成 `coder_1.md`、`coder_2.md` 等分片 prompt。

产物：

- `.tasks/task_*.json`
- `docs/file-plan.md`
- `.harness/agent-prompts/*.md`
- `.harness/run-summary.json`

### 4.3 execute

输入：已审核的需求文档、任务图和动态执行 prompt。

行为：

- 需求文档必须存在且非空。
- 构造执行顺序：architect，然后按 coder 分片重复执行 coder/tester，再执行 integrator、reviewer 和 release。
- 每个 agent 执行前创建检查点和隔离目录。
- agent 成功完成后合并隔离目录中的文件改动。
- 每个 coder 合并后运行静态扫描，扫描通过才进入下一阶段。
- agent 失败或 tester 命令失败时恢复检查点并停止本轮流程。

产物：

- 代码变更。
- `.harness/run-state.json`
- `.harness/run-summary.json`
- `.harness/patch-journal/*.jsonl`
- `.harness/static-scan/*.json`

### 4.4 run

连续运行 `plan`、`prompts`、`execute`。该命令不会提供人工审核暂停点，适合已经确认流程和需求的小任务。

### 4.5 refine

输入：局部变更请求，可选 `--files` 限定文件范围。

行为：

- 创建一个 refine 任务。
- 运行 lead、coder、tester、reviewer、release 的局部微调流程。
- 如果指定了文件范围，agent prompt 会明确该范围。

## 5. 业务门禁规则

- `docs/requirements.md` 是 build 模式的业务事实来源。
- 当文件不存在或为空时，必须先进入 plan 阶段。
- 当文件已存在且非空时，系统认为业务需求已被人工确认，并在 build 流程中跳过 lead 规划澄清。
- 如果 agent 发现会影响代码行为的业务规则仍然不明确，应使用 `ask_user`。
- 用户回答会通过 `record_requirement` 追加进 `docs/requirements.md`，之后 agent 继续基于该文档执行。

## 6. Agent 分工

- lead：规划、业务澄清、任务图拆分。
- architect：项目结构、模块边界、接口契约。
- coder：按需求文档、任务图和分片 prompt 实现代码。
- tester：补充测试、运行验证命令、记录失败。
- integrator：在 reviewer 前统一命名、去重、串联接口、确认项目入口和整体验证路径。
- reviewer：审查 bug、回归风险和缺失测试。
- release：整理运行、验证、部署和风险说明。

## 7. 工具模型

Agent 每轮只能返回一个 JSON 工具操作。

支持工具包括：

- 文件工具：`list_files`、`read_file`、`write_file`、`append_file`
- 命令工具：`run_command`
- 任务工具：`create_task`、`update_task`
- 协作工具：`send_message`
- 需求澄清工具：`ask_user`、`record_requirement`
- 技能工具：`load_skill`
- 结束工具：`finish`

所有路径都会经过根目录边界检查，禁止逃逸目标项目根目录。

## 8. 状态与恢复

- `.tasks/` 保存任务图。
- `.team/` 保存团队配置、消息 inbox 和事件日志。
- `.harness/run-state.json` 保存当前运行进度。
- `.harness/checkpoints/` 保存 agent 执行前快照。
- `.harness/isolated/` 保存 agent 的隔离执行工作区。
- `.harness/patch-journal/` 记录工具写文件前后的内容。

当流程恢复时，如果存在进行中的 checkpoint，系统会先恢复到该 checkpoint，再继续当前执行状态。

## 9. 失败处理

- Agent 返回非 completed 状态时，本轮 agent 改动会被回滚。
- tester 执行命令出现非零退出码时，会把 tester 结果标记为 failed 并回滚。
- coder 后的静态扫描如果发现语法错误、重复函数/类定义或循环导入，会标记失败并回滚该 coder 阶段。
- 重复顶层变量和疑似未引用文件会进入静态扫描报告，供 integrator、reviewer 和人工审核处理。
- Python 使用 AST 做精确扫描；其他语言使用轻量正则和本地依赖图扫描，用于快速发现明显问题，不能替代各语言原生编译、lint 或类型检查。
- LLM 调用、文件越界、缺失 prompt 等异常都会导致当前 agent 回滚并停止流程。
- 回滚只处理项目托管文件，不覆盖 `.git`、`.tasks`、`.team`、`.harness`、虚拟环境和缓存目录。

## 10. 审核重点

人工审核时建议重点检查：

- `docs/requirements.md` 是否包含完整业务规则和边界条件。
- `docs/file-plan.md` 是否为每个 coder 分片列出清晰、可执行的允许路径。
- `.harness/agent-prompts/*.md` 是否引用了正确需求和任务范围。
- coder 分片是否没有遗漏关键业务段落。
- `.harness/static-scan/*.json` 是否存在需要 integrator 或 reviewer 处理的问题。
- integrator 是否完成命名统一、重复实现消除、接口串联和项目入口验证。
- tester 是否运行了与改动风险匹配的验证命令。
- release 是否如实说明了运行方式、验证结果和剩余风险。
