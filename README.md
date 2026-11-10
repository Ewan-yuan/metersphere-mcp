# metersphere-mcp

MeterSphere（v3.x / lite）MCP server：让本地 agent（Claude Code / Codex / DeepSeek harness 等）通过平台
REST API + AK/SK 鉴权，实现**功能用例增删改查、模块管理、测试计划创建查询、计划关联用例**。
纯客户端实现，平台侧零改动。

经 MeterSphere v3.6.8 生产环境全链路验证（16 项 smoke：读 6 项 + 写 10 项）。

## 快速安装（Claude Code）

```bash
claude mcp add metersphere -s user \
  -e MS_BASE_URL=https://your-metersphere.example.com \
  -e MS_ACCESS_KEY=你的AK -e MS_SECRET_KEY=你的SK \
  -e MS_READ_ONLY=false -- \
  uvx --from git+https://github.com/Ewan-yuan/metersphere-mcp metersphere-mcp
```

- `MS_BASE_URL`：平台地址（后端根路径，如 `https://test.example.com`；若仅前端反代可用 `https://host/front`）
- `MS_READ_ONLY` 省略或 `true` 时只注册 9 个只读工具（≈2K tokens）；`false` 注册全部 15 个（≈3.6K tokens）
- 前置条件：[uv](https://docs.astral.sh/uv/)（`uvx` 随 uv 安装）—— macOS/Linux：`curl -LsSf https://astral.sh/uv/install.sh | sh`；Windows：`powershell -c "irm https://astral.sh/uv/install.ps1 | iex"`
- 装完重启会话，用 `whoami` 验证连通性

## 安装到 Codex CLI

编辑 `~/.codex/config.toml`：

```toml
[mcp_servers.metersphere]
command = "uvx"
args = ["--from", "git+https://github.com/Ewan-yuan/metersphere-mcp", "metersphere-mcp"]

[mcp_servers.metersphere.env]
MS_BASE_URL = "https://your-metersphere.example.com"
MS_ACCESS_KEY = "你的AK"
MS_SECRET_KEY = "你的SK"
MS_READ_ONLY = "false"
```

保存后重启 Codex 会话即可。

## 安装到 DeepSeek harness（及一切标准 MCP 客户端）

任何支持 stdio MCP 的 harness 通用：把下面 JSON 写入该工具的 MCP 配置
（常见位置：项目根 `.mcp.json`，或该工具文档指定的配置文件）：

```json
{
  "mcpServers": {
    "metersphere": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/Ewan-yuan/metersphere-mcp", "metersphere-mcp"],
      "env": {
        "MS_BASE_URL": "https://your-metersphere.example.com",
        "MS_ACCESS_KEY": "你的AK",
        "MS_SECRET_KEY": "你的SK",
        "MS_READ_ONLY": "false"
      }
    }
  }
}
```

## 配套 Skill（Claude Code）

用例编写规范，按需加载、平时零开销：

```bash
git clone --depth 1 https://github.com/Ewan-yuan/metersphere-mcp /tmp/ms-mcp \
  && mkdir -p ~/.claude/skills && cp -r /tmp/ms-mcp/skills/metersphere ~/.claude/skills/ \
  && rm -rf /tmp/ms-mcp
```

Windows (PowerShell)：

```powershell
git clone --depth 1 https://github.com/Ewan-yuan/metersphere-mcp $env:TEMP\ms-mcp
New-Item -ItemType Directory -Force "$env:USERPROFILE\.claude\skills" | Out-Null
Copy-Item -Recurse "$env:TEMP\ms-mcp\skills\metersphere" "$env:USERPROFILE\.claude\skills\"
```

## AK/SK 从哪来

平台 **个人中心 → API KEY** 生成（lite 版若隐藏了该入口，可让管理员调用
`GET /user/api/key/add` 代为生成）。AK 所属用户需在目标项目具有用例/计划读写权限。
签名协议：`signature = Base64(AES-128-CBC("accessKey|uuid|timestampMs", key=SK, iv=AK))`，
与 MeterSphere `ApiKeyHandler` 完全一致。

## 安装后 agent 获得什么能力

**只读模式（默认）**：agent 能查询你的项目/模块/用例/测试计划/测试集，检索现有测试资产。

**写模式（MS_READ_ONLY=false）**：agent 能以 AK 所属账号身份创建/修改/删除功能用例
（删除进回收站）、建模块、建测试计划、把用例关联进计划。权限边界 = 该账号的平台权限，
越权操作会被平台拒绝并留操作日志；SK 不经过 agent（签名在 server 进程内完成）。

## 工具清单

**只读（默认注册）**

| 工具 | 说明 |
|---|---|
| `whoami` | 验证 AK/SK，返回当前用户 id |
| `list_projects` | 组织下可见项目（需 organization_id） |
| `get_case_template` | 项目默认用例模板 + 自定义字段（含等级可选值） |
| `list_case_modules` | 功能用例模块树 |
| `search_cases` | 关键字/模块分页搜索（精简字段） |
| `get_case_detail` | 用例全量详情（steps 已解析为数组） |
| `list_test_plans` / `get_test_plan` | 计划查询 / 详情 |
| `get_plan_collections` | 计划测试集（含各测试集已关联用例数） |

**写入（仅 MS_READ_ONLY=false 时注册）**

| 工具 | 说明 |
|---|---|
| `create_case` | 创建用例（STEP 结构化步骤 / TEXT 描述式；等级 P0-P3 默认 P0） |
| `update_case` | 修改用例（未传字段保持原值） |
| `delete_case` | 删除用例（进回收站，可恢复） |
| `create_case_module` | 创建模块（顶层 parent_id=NONE） |
| `create_test_plan` | 创建测试计划（自动带默认测试集） |
| `add_cases_to_plan` | 把功能用例关联进计划测试集 |

## 配置项

| 环境变量 | 必填 | 默认 | 说明 |
|---|---|---|---|
| `MS_BASE_URL` | 是 | — | 平台后端根地址 |
| `MS_ACCESS_KEY` | 是 | — | 平台 API Key |
| `MS_SECRET_KEY` | 是 | — | 平台 API Secret |
| `MS_READ_ONLY` | 否 | `true` | `false` 时注册写工具 |
| `MS_TIMEOUT` | 否 | `30` | 请求超时（秒） |

## 开发与测试

```bash
git clone https://github.com/Ewan-yuan/metersphere-mcp && cd metersphere-mcp
uv sync
uv run pytest tests/ -q    # 单元测试（签名/steps/脑图载荷，不触网）
uv run metersphere-mcp     # 本地 stdio 启动
```

## 实现备注（平台升级时需同步关注）

- 用例创建/更新是 multipart 接口：`request` part 以 `Content-Type: application/json` 传 JSON
- **计划关联用例没有独立端点**，唯一入口是脑图编辑 `POST /test-plan/mind/data/edit`。
  该接口会删除不在 `editList` 里的测试集——本实现先拉全量脑图数据原样回传，只在目标
  FUNCTIONAL 测试集挂 `associateDTOS`，测试集计数从 "N条" 展示节点解析
- `/is-login` 匿名放行，AK/SK 不生效；`whoami` 用 `GET /user/api/key/list`（其 `createUser` 即当前用户）
- 错误信封：HTTP 200 + `{code: 100500...}`；成功码 `100200`
