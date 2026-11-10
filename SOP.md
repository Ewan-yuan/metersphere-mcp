# MeterSphere MCP 使用 SOP

> 让本地 AI agent（Claude Code / Codex / DeepSeek harness 等）直接在 MeterSphere 平台上查询、编写测试用例和管理测试计划。
> 工具仓库：https://github.com/Ewan-yuan/metersphere-mcp （v0.1.0，已通过生产环境全链路验证）

---

## 一、能力一览

| 模式 | agent 能做什么 |
| --- | --- |
| 只读（默认） | 查询项目、模块、用例、测试计划、测试集，检索现有测试资产 |
| 读写（`MS_READ_ONLY=false`） | 创建 / 修改 / 删除功能用例（删除进回收站）、创建模块、创建测试计划、把用例关联进计划 |

权限边界 = AK 所属账号的平台权限，越权操作会被平台拒绝并记录操作日志；SK 只保存在本机 MCP 进程内参与签名，不经过 agent。

## 二、准备

1. 安装 [uv](https://docs.astral.sh/uv/)（Windows PowerShell：`powershell -c "irm https://astral.sh/uv/install.ps1 | iex"`）
2. 平台**个人中心 → 个人设置 → API KEY → 新增**，生成 AK/SK（Secret Key 仅创建时可见，及时复制）
3. 记下平台地址（如 `https://test.example.com`）

## 三、安装（按所用 agent 三选一）

### 3.1 Claude Code

```bash
claude mcp add metersphere -s user \
  -e MS_BASE_URL=https://test.example.com \
  -e MS_ACCESS_KEY=你的AK -e MS_SECRET_KEY=你的SK \
  -e MS_READ_ONLY=false -- \
  uvx --from git+https://github.com/Ewan-yuan/metersphere-mcp metersphere-mcp
```

### 3.2 Codex CLI

编辑 `~/.codex/config.toml`，追加：

```toml
[mcp_servers.metersphere]
command = "uvx"
args = ["--from", "git+https://github.com/Ewan-yuan/metersphere-mcp", "metersphere-mcp"]

[mcp_servers.metersphere.env]
MS_BASE_URL = "https://test.example.com"
MS_ACCESS_KEY = "你的AK"
MS_SECRET_KEY = "你的SK"
MS_READ_ONLY = "false"
```

### 3.3 DeepSeek harness（及一切标准 MCP 客户端）

把下面的 JSON 写入该工具的 MCP 配置文件（常见位置：项目根目录 `.mcp.json`）：

```json
{
  "mcpServers": {
    "metersphere": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/Ewan-yuan/metersphere-mcp", "metersphere-mcp"],
      "env": {
        "MS_BASE_URL": "https://test.example.com",
        "MS_ACCESS_KEY": "你的AK",
        "MS_SECRET_KEY": "你的SK",
        "MS_READ_ONLY": "false"
      }
    }
  }
}
```

> Claude Code 用户可选装配套 Skill（用例编写规范，按需加载不占上下文），见仓库 README「配套 Skill」一节。

## 四、验证

重启 agent 会话后对 agent 说：**「用 metersphere 的 whoami 验证连接」**，返回用户 id 即成功。

## 五、日常用法（直接对 agent 说）

| 场景 | 示例指令 |
| --- | --- |
| 写用例 | 「根据这份需求在 everoffice 项目创建功能用例，覆盖正常流和异常流，等级按影响定 P0-P3」 |
| 批量核对 | 「搜一下本周建的用例，列个清单（编号+名称+等级）」 |
| 改用例 | 「把『登录-密码错误提示』的预期结果改成 xxx」 |
| 建计划 | 「创建 11 月回归测试计划，时间 11.10-11.15，把这个模块的用例都关联进去」 |

agent 会自动走「查项目 → 查模块/模板 → 写入 → 回读核对」流程，所有 id 现查现用，无需人工提供。

## 六、安全约定

1. **默认只读**：需要写操作才配置 `MS_READ_ONLY=false`，遵循最小权限
2. 删除操作只进回收站，平台可恢复；所有写操作以 AK 账号身份记录操作日志
3. AK/SK 只放本机 agent 配置，**不进代码仓库、不贴聊天群**
4. 怀疑泄露：回平台删除 Key 重建，更新本机配置即可

## 七、常见问题

| 现象 | 处理 |
| --- | --- |
| 401 / 鉴权失败 | AK 被删或禁用，到个人中心重建 Key |
| 写工具不存在 | 默认只读模式，配置 `MS_READ_ONLY=false` 并重启会话 |
| 提示 moduleId / templateId 校验失败 | agent 猜了 id，让它先查模块树和模板 |
| list_projects 要求 organization_id | 从平台「组织-项目」页面 URL 复制，或询问管理员 |
| 计划关联后数量没变 | 用 get_plan_collections 刷新核对；重复用例会被自动跳过 |

---

*文档版本：2026-11-10 · 工具版本 v0.1.0 · 问题反馈：平台管理员*
