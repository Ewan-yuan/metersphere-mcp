# MeterSphere MCP 使用 SOP

让本地 agent（Claude Code / Codex / DeepSeek harness）直接在 MeterSphere 上查写测试用例、管理测试计划。
仓库：<https://github.com/Ewan-yuan/metersphere-mcp>

## 1. 能力一览

| 模式 | agent 能做什么 |
|---|---|
| 只读（默认） | 查项目/模块/用例/计划/测试集，检索现有测试资产 |
| 读写（`MS_READ_ONLY=false`） | 创建/修改/删除用例（删进回收站）、建模块、建测试计划、把用例关联进计划 |

权限边界 = AK 所属账号的平台权限；SK 只存在于本机 MCP 进程，不经过 agent。

## 2. 准备

1. 装 [uv](https://docs.astral.sh/uv/)（一条命令：Windows `powershell -c "irm https://astral.sh/uv/install.ps1 | iex"`）
2. 平台**个人中心 → API KEY** 生成 AK/SK（lite 版入口被隐藏时找管理员调 `GET /user/api/key/add`）
3. 记下平台地址，如 `https://test.example.com`

## 3. 安装（三选一）

**Claude Code**

```bash
claude mcp add metersphere -s user \
  -e MS_BASE_URL=https://test.example.com \
  -e MS_ACCESS_KEY=AK -e MS_SECRET_KEY=SK \
  -e MS_READ_ONLY=false -- \
  uvx --from git+https://github.com/Ewan-yuan/metersphere-mcp metersphere-mcp
```

**Codex CLI** — 写入 `~/.codex/config.toml`

```toml
[mcp_servers.metersphere]
command = "uvx"
args = ["--from", "git+https://github.com/Ewan-yuan/metersphere-mcp", "metersphere-mcp"]

[mcp_servers.metersphere.env]
MS_BASE_URL = "https://test.example.com"
MS_ACCESS_KEY = "AK"
MS_SECRET_KEY = "SK"
MS_READ_ONLY = "false"
```

**DeepSeek harness / 其他标准 MCP 客户端** — 把下面的 JSON 写入该工具的 MCP 配置
（常见位置：项目根 `.mcp.json`）

```json
{
  "mcpServers": {
    "metersphere": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/Ewan-yuan/metersphere-mcp", "metersphere-mcp"],
      "env": {
        "MS_BASE_URL": "https://test.example.com",
        "MS_ACCESS_KEY": "AK",
        "MS_SECRET_KEY": "SK",
        "MS_READ_ONLY": "false"
      }
    }
  }
}
```

Claude Code 用户可再装配套 Skill（用例编写规范，按需加载）：见仓库 README「配套 Skill」一节。

## 4. 验证

重启 agent 会话后，对 agent 说：

> 用 metersphere 的 whoami 验证连接

返回用户 id 即成功。

## 5. 日常用法（对 agent 直接说）

| 场景 | 示例指令 |
|---|---|
| 写用例 | 「根据这份需求在 everoffice 项目创建功能用例，覆盖正常流和异常流，等级按影响定 P0-P3」 |
| 批量核对 | 「搜一下本周建的用例，列个清单（编号+名称+等级）」 |
| 改用例 | 「把『登录-密码错误提示』的预期结果改成xxx」 |
| 建计划 | 「创建 11 月回归测试计划，时间 11.10-11.15，把这个模块的用例都关联进去」 |

agent 会自己走「查项目→查模块/模板→写入→回读核对」的流程；id 一律现查现用。

## 6. 安全约定

- **默认只读**：需要写才配 `MS_READ_ONLY=false`，最小权限原则
- 删除操作只进回收站，平台可恢复；所有写操作以 AK 账号身份记录操作日志
- AK/SK 只放本机 agent 配置，不进代码仓库、不贴聊天群
- 怀疑泄露就回平台删 Key 重建，改一下配置里的值即可

## 7. 常见问题

| 现象 | 处理 |
|---|---|
| 401 / 鉴权失败 | AK 被删或禁用，重建 Key |
| 写工具不存在 | 默认只读模式，加 `MS_READ_ONLY=false` 并重启会话 |
| 提示 moduleId/templateId 校验失败 | agent 猜了 id，让它先查模块树/模板 |
| list_projects 要 organization_id | 从平台『组织-项目』页 URL 里复制，或问管理员 |
| 计划关联后数量没变 | 用 get_plan_collections 刷新核对；重复用例会被跳过 |
