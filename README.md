# ProjectCompass

ProjectCompass 是在本地运行的项目看板，记录开发者与 coding Agent 的公开协作过程。个人视图保留各自的任务与决策候选，团队视图展示负责人确认的状态。调查回答链接原文，同时保留冲突及缺证说明。

这是 ProjectCompass 的公开源码候选包。样例全部为合成材料，不包含真实项目会话、验收日志、凭据或运行状态。ProjectCompass 原创代码按 MIT License 发布；上游依赖继续遵守各自许可证。

## 无凭据试用

需要 Python 3.11 或更新版本。以下命令适用于 Windows PowerShell，从本目录运行。离线核心使用 Python 标准库，无须安装模型 SDK。运行 JavaScript 测试另需 Node。

```powershell
python -m venv .venv
$env:PYTHONPATH = 'src'
.\.venv\Scripts\python.exe -m project_compass.cli --sample
.\.venv\Scripts\python.exe -m project_compass.cli --sample --web --port 8787
```

打开 http://127.0.0.1:8787/ 查看个人与团队视图，并点击证据 ID 查看原文。服务运行在终端中，按 Ctrl+C 停止。重新执行同一命令即可重新打开。样例模式只演示离线流程，拒绝赞助后端与私有原因预览参数，不能代替比赛要求的实际集成。

## 接入已有项目

用自己的项目目录替换下方占位路径。目标项目只读，扫描状态写在本应用目录的 `.project-memory` 中。首次扫描和再次打开分别使用以下命令。

```powershell
.\.venv\Scripts\python.exe -m project_compass.cli --scan 'D:\Projects\example-project' --web --port 8787
.\.venv\Scripts\python.exe -m project_compass.cli --open 'D:\Projects\example-project' --web --port 8787
```

扫描收集限定范围的 Markdown 与近期 Git 材料。原文采集不等于完整语义提取，普通文件不会自动成为任务或决定。已有公开 Codex JSONL 可以通过 `--scan-codex-jsonl PATH` 接入；日常使用可绑定一个现有 Codex 聊天，无须手工导出。

```powershell
.\.venv\Scripts\python.exe -m project_compass.cli --open 'D:\Projects\example-project' --codex-thread THREAD_UUID --member-id developer-a --codex-tools --web --port 8787
```

THREAD_UUID 必须替换为用户明确选择的聊天 ID。只读取该项目关联的公开消息；公开工具与命令记录使用有界窗口。隐藏推理、敏感记录和平台控制消息会被跳过，页面会显示接入与扫描边界。`--help` 列出所有参数。

模型提取与 Jev 均为可选功能，默认不启用。启用模型前需单独设置认证和用量预算。`--reason-preview` 只读取已有的项目绑定实验缓存，该私有缓存没有随包发布；它不生成新原因，也不提供原因关系的负责人审核。

## 运行检查

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

测试使用临时项目与合成材料；赞助适配器的单元测试不能证明真实后端已安装。真实集成方法见 [INTEGRATIONS.md](INTEGRATIONS.md)，数据路径与边界见 [ARCHITECTURE.md](ARCHITECTURE.md)。

## 比赛与复用披露

目标赛道为 The Agent That Can Explain Why，主赛道选择由提交表单最终核对。按[赛事规则](https://hackathon.genai.works/event/open-agent-hackathon-2026#rules)，每个比赛提交必须集成 Zetaris 与 Meterless。1至4分钟演示和幻灯片仍需单独准备；本源码包只包含可公开运行的代码和合成示例。

更早的本地原型提供了横向历程、分支卡片和证据抽屉的设计及服务思路。首个独立源码快照已包含核心 CLI 与样例，不能声称全部从零编写。后续加入了持久化、原生 Codex 接入、赞助适配器与团队审核等功能。精确的早期逐行创作时间未独立核实，最终表单披露仍需作者审核。

开发使用 Codex。Cursor 未用于当前开发流程；没有已核验的 NVIDIA 技术贡献。Jev 不参与默认演示。第三方来源与许可边界见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

