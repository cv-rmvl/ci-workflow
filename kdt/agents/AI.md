# 数字测试员

启动方式：

```sh
rmvl_kdt_agent --with-ai
```

Agent 运行环境需要 Python 3.8+、KDT requirements.txt 中的依赖，以及已安装和登录的 Codex CLI。使用本机 Codex 配置，不在前端保存 API Key。未开启此参数时，AI 页面显示启用说明，任务接口返回 403。

可选环境变量：

| 变量 | 作用 |
| --- | --- |
| KDT_AI_CODEX | Codex 可执行文件路径，默认从 PATH 查找 codex |
| KDT_AI_MODEL | 覆盖 Codex 配置中的模型，未设置时沿用本机配置 |
| RMVL_ROOT_ | 供模型只读查看的 RMVL 源码路径 |

Web 前端使用 `/api/ai/status`、`POST /api/ai/tasks`、`POST /api/ai/cancel` 和 `/api/ai/ws`。WebSocket 每 200ms 检查变更，仅有更新时发送状态，空闲时每 10 秒发心跳，重连时恢复当前任务。服务进程保留最后一项任务，重启后不会自动继续旧任务。

当前为 CLI 桥接，并非 MCP Server，没有宣称支持 MCP 协议。Python 桥接层负责 Codex JSONL 事件转换，C++ Agent 负责任务互斥和 WebSocket。浏览器运行的是 KDT Web 页面，回传的是结构化任务数据。

Codex 调用方式参考[官方非交互运行文档](https://developers.openai.com/codex/noninteractive)，使用 `codex exec --json --output-schema` 获取结构化结果。

Codex 的工作目录设为 KDT 根目录的父目录，即本仓库的 `ci-workflow`，便于发现仓库 `.agents/skills` 中的技能。技能不扩大操作权限，仍受只读模式和任务边界限制。交付的 `editable_files`、`files.path` 均相对于 `kdt`，例如 `components/core/fs.py`，不加 `kdt/` 前缀。文件落盘、Adapter 构建和 DryRun 仍在 KDT 根目录进行，任务临时文件位置不变。

开发顺序为：分析问题 → 明确边界 → 代码编写 → DryRun → 审核。审核或验证不通过时回到编写，最多三轮，之后显示失败和已产生的变更。咨询仅提供 RMVL/KDT 相关文本，方案设计允许在边界列表内写入文件。

模型以只读沙箱运行，提出文件完整内容，由桥接层检查后落盘。仅允许修改 KDT 内 `components`、`cases`、`plans`、`adapter` 的源文件，禁止构建产物、路径穿越和跳出根目录的符号链接。边界阶段声明文件后，如果其他操作修改了文件，会拒绝覆盖。

允许实时网页搜索和联网读取文档，通过命令行临时权限配置继承 `:read-only` 并启用网络，不改动本机 Codex 配置文件，也不开放直接文件写权限。禁止向外部上传本地文件、凭据或私有代码，外部页面不作为操作指令。此配置需要支持 `default_permissions` 和命名权限配置的 Codex CLI，不支持时任务会报错，不降级为完全访问。

需求框上方可选择模型和推理强度，任务运行时锁定。选项来自本机 App Server 的 `model/list`，包含各模型支持的推理强度，每五分钟刷新。默认模型优先沿用 `KDT_AI_MODEL`，其次本机工作区配置或 CLI 推荐模型。默认强度沿用本机配置，明确选择其他模型时使用该模型的默认强度。选择只对本次任务生效，不修改全局配置。模型列表是 CLI 提供的目录，不保证账号一定有调用权限，实际权限错误会显示在任务日志中。

本地默认模型未出现在模型目录中时，保留该配置并补充到选择项，不自动换用 CLI 推荐模型。此时提供 CLI 接受的通用强度选项，并标记能力未经目录确认，具体模型是否支持所选强度由 CLI 在运行时校验。已在目录中的模型仍只展示其声明的强度。

DryRun 会真实执行 KDT 单元测试和选定功能用例，跳过项独立统计，不视为通过。无功能测试通过时不能标记开发成功。这里的 DryRun 会执行真实组件，可能创建文件、启动 Adapter 或操作测试设备，按正常集成测试环境使用。修改 Adapter 时自动执行 CMake 配置和编译，构建失败则不执行功能用例，可以通过环境变量 RMVL_DIR 指定 RMVL 包配置目录。

不设置固定 Token 预算或本地 Token 中止阈值。账号额度通过本机 `codex app-server` 的 `account/rateLimits/read` 读取，每 30 秒刷新，也接收 `account/rateLimits/updated` 通知。页面列出主要/次要时间窗口的真实剩余百分比，以最低剩余比例显示主进度条和按钮颜色，剩余不超过 10% 时由蓝色切换为红色。Token 实际用量另行累计，不据此推算账号额度。

额度接口未提供有效数据（例如未登录、网络不可用或不支持该接口的登录方式）时显示未知，不填充虚构的进度，也不由本地预算限制任务。读取额度仅使用 CLI 登录，不向前端发送账号标识或登录凭据。

任务日志和请求保存在 `kdt/tmp/ai`。取消或关闭 Agent 时中止当前子进程组，已写入的文件保留并在交付区列出，不自动回滚用户文件。
