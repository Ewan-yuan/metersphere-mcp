---
name: metersphere
description: 通过 metersphere MCP 工具在 MeterSphere 测试平台创建/修改功能用例、管理模块、创建测试计划并把用例关联进计划。当用户提到 MeterSphere、测试用例、写用例、用例设计、测试计划、把用例加进计划时使用。
---

# MeterSphere 用例与测试计划操作指南

## 前置检查

1. 确认 `mcp__metersphere__*` 工具存在。不存在则提示用户配置 MCP server
   （仓库 `tools/mcp/metersphere-mcp/`，需 MS_BASE_URL / MS_ACCESS_KEY / MS_SECRET_KEY）。
2. 首次使用先调 `whoami` 验证连通性和身份。
3. 写工具不存在 = 服务端只读模式（MS_READ_ONLY=true）。此时告知用户：改配置需设
   MS_READ_ONLY=false 并重启会话，不要反复尝试写操作。

## ID 发现流程（所有 id 必须查出来，禁止猜测）

```
whoami → list_projects(organization_id) → project_id
list_case_modules(project_id) → module_id（顶层用 "root"）
get_case_template(project_id) → template_id、用例等级可选值
search_cases / list_test_plans → 已有内容的 id
get_plan_collections(plan_id) → 测试集 collection_id
```

organization_id 用户没给时：让用户从平台『组织设置-项目』页面 URL 里复制，不要瞎猜。

## 创建功能用例的规范

`create_case` 用 STEP 模式（结构化步骤），除非用户明确要文本描述模式：

- **name**：`[功能点]-[场景]`，如 `登录-密码错误提示`；一句话聚焦单一场景，不写多个"和"
- **prerequisite**：执行前必须成立的状态（账号已注册、数据已存在）；没有就留空
- **steps**：每步一个可执行动作；`desc` = 操作（动词开头），`result` = 可断言的预期
  （页面文案、状态码、数据变化），禁止"显示正常""操作成功"这类不可验证的描述
- **priority**：P0 冒烟核心链路 / P1 主流程+常用分支 / P2 边界与异常 / P3 低频场景
- 一个需求先设计用例集再批量创建：正常流 P0-P1、每个输入项的边界与非法值 P2、
  权限与并发类 P2-P3
- 批量创建后用 `search_cases(keyword=...)` 核对条数，向用户汇报 num 列表（平台的用例编号）

## 修改与删除

- 改用例：`update_case` 只传要改的字段；改步骤时传完整 steps 数组（未传字段保持原值）
- 删用例：先向用户列出将删除的用例（name+num）确认，再调 `delete_case`（进回收站可恢复）

## 测试计划流程

```
create_test_plan(project_id, name, planned_start, planned_end, description)
  → 返回 plan_id（计划创建后自带一个 FUNCTIONAL 默认测试集）
add_cases_to_plan(plan_id, case_ids)          # 默认测试集
add_cases_to_plan(plan_id, case_ids, collection_id)  # 指定测试集，先 get_plan_collections 查
```

完成后调 `get_plan_collections(plan_id)` 核对数量，向用户汇报计划的测试集与用例数。

## 常见错误

| 报错 | 原因与处理 |
|---|---|
| 鉴权失败 401 | AK/SK 无效或被禁用，让用户在平台个人中心重新生成 |
| 只读模式 | MS_READ_ONLY=true，写操作被拒，见前置检查 |
| moduleId/templateId 校验失败 | 没走 ID 发现流程，id 是猜的 |
| collection 不是 FUNCTIONAL | add_cases_to_plan 只收功能用例测试集，重新选 |
