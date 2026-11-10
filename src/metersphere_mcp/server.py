"""MeterSphere MCP server.

工具集：功能用例 CRUD、模块管理、测试计划创建/查询、计划关联用例。
写工具仅在 MS_READ_ONLY=false 时注册。
"""

from __future__ import annotations

import os
from typing import Any

from .client import MSApiError, MSClient, build_steps_json, parse_steps_json, to_epoch_ms

PAGE_SIZE_MAX = 50


def _get_client() -> MSClient:
    # FastMCP 在 import 时构造工具 schema，client 必须延迟到调用时创建
    return MSClient(
        base_url=os.environ.get("MS_BASE_URL", ""),
        access_key=os.environ.get("MS_ACCESS_KEY", ""),
        secret_key=os.environ.get("MS_SECRET_KEY", ""),
        timeout=float(os.environ.get("MS_TIMEOUT", "30")),
    )


def _read_only() -> bool:
    return os.environ.get("MS_READ_ONLY", "true").strip().lower() not in ("false", "0", "no")


def _require_write() -> None:
    if _read_only():
        raise MSApiError("服务端为只读模式（MS_READ_ONLY 默认 true）。写操作需在 MCP 配置中设置 MS_READ_ONLY=false 后重启会话。")


def _page(current: int, page_size: int) -> dict:
    return {"current": max(1, current), "pageSize": min(max(5, page_size), PAGE_SIZE_MAX)}


# ---------- 脑图接口辅助：editList 必须全量回传，否则缺席的测试集会被服务端删除 ----------

def _flatten_mind_nodes(mind_data: list[dict]) -> list[dict]:
    """把 GET /test-plan/mind/data 的树压平，节点带上 level（1=类型节点，2=测试集），跳过根节点。
    测试集下还挂 "N条/默认环境/资源池" 展示节点（id 为 null），它们不是测试集，必须过滤。"""
    flat: list[dict] = []

    def walk(children: list[dict], level: int) -> None:
        for child in children or []:
            node = dict(child.get("data") or {})
            if node.get("id"):
                flat.append({**node, "level": level})
            walk(child.get("children") or [], level + 1)

    # mind_data = [根节点树]，根节点 level 0，类型节点 level 1，测试集 level 2（与服务端 edit DTO 的 level 语义一致）
    walk(mind_data or [], 0)
    return [n for n in flat if n.get("id") != "root"]


def _collection_case_count(mind_data: list[dict], collection_id: str) -> int | None:
    """从测试集的 "N条" 计数展示节点解析用例数。root/展示节点自身无 id，但其子树仍需遍历。"""
    import re

    def walk(children: list[dict]) -> int | None:
        for child in children or []:
            data = child.get("data") or {}
            nid = data.get("id")
            if nid and nid != "root" and nid == collection_id:
                for sub in child.get("children") or []:
                    m = re.match(r"^(\d+)", str((sub.get("data") or {}).get("text") or ""))
                    if m:
                        return int(m.group(1))
                return None
            found = walk(child.get("children") or [])
            if found is not None:
                return found
        return None

    return walk(mind_data or [])


def _functional_leaf(mind_data: list[dict], collection_id: str | None = None) -> dict:
    nodes = _flatten_mind_nodes(mind_data)
    if not nodes:
        raise MSApiError("该测试计划没有测试集（脑图数据为空），无法关联用例")
    leaves = [n for n in nodes if n.get("level", 2) >= 2 and n.get("type") == "FUNCTIONAL"]
    if not leaves:
        raise MSApiError("该计划没有 FUNCTIONAL 类型的测试集，无法关联功能用例")
    if collection_id:
        target = next((n for n in leaves if n["id"] == collection_id), None)
        if target is None:
            raise MSApiError(f"collection_id={collection_id} 不是该计划下类型为 FUNCTIONAL 的测试集")
        return target
    return leaves[0]


def _node_to_edit_dto(node: dict) -> dict:
    pos = node.get("pos") or 0
    return {
        "id": node["id"],
        "tempCollectionNode": False,
        "text": node.get("text", ""),
        "num": max(0, pos // 4096 - 1),
        "executeMethod": node.get("executeMethod") or "SERIAL",
        "type": node.get("type", "FUNCTIONAL"),
        "extended": bool(node.get("extended")),
        "grouped": bool(node.get("grouped")),
        "environmentId": node.get("environmentId") or "NONE",
        "testResourcePoolId": node.get("testResourcePoolId") or "NONE",
        "retryOnFail": bool(node.get("retryOnFail")),
        "retryType": node.get("retryType") or "STEP",
        "retryTimes": node.get("retryTimes") if node.get("retryTimes") is not None else 1,
        "retryInterval": node.get("retryInterval") if node.get("retryInterval") is not None else 0,
        "stopOnFail": bool(node.get("stopOnFail")),
        "level": node.get("level", 2),
    }


def build_mind_edit_payload(mind_data: list[dict], plan_id: str, project_id: str,
                            case_ids: list[str], collection_id: str | None = None) -> dict:
    target = _functional_leaf(mind_data, collection_id)
    edit_list = [_node_to_edit_dto(n) for n in _flatten_mind_nodes(mind_data)]
    for dto in edit_list:
        if dto["id"] == target["id"]:
            dto["associateDTOS"] = [{
                "associateType": "FUNCTIONAL",
                "projectId": project_id,
                "selectAllModule": False,
                "moduleMaps": {"root": {"selectAll": False, "selectIds": list(case_ids), "excludeIds": []}},
                "syncCase": False,
            }]
            break
    return {"planId": plan_id, "editList": edit_list}


# ---------- 用例请求构造 ----------

def _priority_field(template: dict) -> dict | None:
    for f in template.get("customFields") or []:
        if f.get("internalFieldKey") == "functional_priority":
            return f
    return None


def _custom_fields_from_template(template: dict, priority: str | None) -> list[dict]:
    fields: list[dict] = []
    for f in template.get("customFields") or []:
        value = f.get("defaultValue")
        value = "" if value is None else (value if isinstance(value, str) else str(value))
        if f.get("internalFieldKey") == "functional_priority":
            if priority:
                value = priority
            elif not value:
                value = "P0"  # 平台 AI 生成用例同款兜底：等级默认 P0
        fields.append({"fieldId": f.get("fieldId"), "value": value})
    return fields


def build_case_request(project_id: str, name: str, module_id: str, case_edit_type: str,
                       steps: list[dict] | None, text_description: str, expected_result: str,
                       prerequisite: str, description: str, tags: list[str] | None,
                       template_id: str, template: dict | None, priority: str | None = None) -> dict:
    req: dict[str, Any] = {
        "projectId": project_id,
        "templateId": template_id,
        "name": name,
        "moduleId": module_id,
        "caseEditType": case_edit_type,
        "prerequisite": prerequisite or "",
        "description": description or "",
        "publicCase": False,
    }
    if case_edit_type == "STEP":
        req["steps"] = build_steps_json(steps or [])
        req["textDescription"] = ""
        req["expectedResult"] = ""
    else:
        req["steps"] = ""
        req["textDescription"] = text_description or ""
        req["expectedResult"] = expected_result or ""
    if tags:
        req["tags"] = tags
    if template is not None:
        custom_fields = _custom_fields_from_template(template, priority)
        if custom_fields:
            req["customFields"] = custom_fields
    return req


def _resolve_template(client: MSClient, project_id: str, template_id: str) -> tuple[str, dict]:
    """template_id 为空时取项目默认模板；模板同时用于自定义字段默认值与等级映射。"""
    if template_id:
        return template_id, client.get_case_template(project_id)
    template = client.get_case_template(project_id)
    tid = template.get("id") or ""
    if not tid:
        raise MSApiError("项目默认模板获取失败，请显式传 template_id")
    return tid, template


# ---------- FastMCP 工具 ----------

def register_tools(mcp):  # pragma: no cover - FastMCP 装饰器包装，可测逻辑在上方纯函数
    @mcp.tool()
    def whoami() -> dict:
        """验证 MeterSphere 连接与 AK/SK，返回当前用户 id（createUser）与 API Key 列表。"""
        keys = _get_client().whoami()
        users = sorted({k.get("createUser") for k in keys if k.get("createUser")})
        return {"userId": users[0] if users else None, "apiKeys": [
            {"accessKey": k.get("accessKey"), "enable": k.get("enable"), "forever": k.get("forever")}
            for k in keys
        ]}

    @mcp.tool()
    def list_projects(organization_id: str) -> list[dict]:
        """列出组织下可见项目。organization_id 可在平台『组织-项目』页面 URL 中找到，或询问管理员。"""
        projects = _get_client().list_projects(organization_id)
        return [{"id": p.get("id"), "name": p.get("name"), "enable": p.get("enable"),
                 "authorized": p.get("authorized")} for p in projects or []]

    @mcp.tool()
    def get_case_template(project_id: str) -> dict:
        """获取项目默认功能用例模板：templateId + 自定义字段（含用例等级 functional_priority 的可选值）。"""
        t = _get_client().get_case_template(project_id)
        return {
            "id": t.get("id"),
            "name": t.get("name"),
            "customFields": [
                {"fieldId": f.get("fieldId"), "fieldName": f.get("fieldName"),
                 "internalFieldKey": f.get("internalFieldKey"), "type": f.get("type"),
                 "required": f.get("required"), "defaultValue": f.get("defaultValue"),
                 "options": [o.get("value") for o in f.get("options") or []]}
                for f in t.get("customFields") or []
            ],
        }

    @mcp.tool()
    def list_case_modules(project_id: str) -> list[dict]:
        """获取功能用例模块树。新建用例的 module_id 取自这里，顶层默认 root。"""
        def trim(nodes: list[dict]) -> list[dict]:
            return [{"id": n.get("id"), "name": n.get("name"), "children": trim(n.get("children") or [])}
                    for n in nodes or []]
        return trim(_get_client().list_case_modules(project_id))

    @mcp.tool()
    def search_cases(project_id: str, keyword: str = "", module_ids: list[str] | None = None,
                     current: int = 1, page_size: int = 20) -> dict:
        """按关键字/模块分页搜索功能用例（精简字段）。要完整内容用 get_case_detail。"""
        payload: dict[str, Any] = {"projectId": project_id, **_page(current, page_size)}
        if keyword:
            payload["keyword"] = keyword
        if module_ids:
            payload["moduleIds"] = module_ids
        pager = _get_client().search_cases(payload)
        items = [{
            "id": c.get("id"), "num": c.get("num"), "name": c.get("name"),
            "moduleId": c.get("moduleId"), "reviewStatus": c.get("reviewStatus"),
            "caseEditType": c.get("caseEditType"),
        } for c in pager.get("list") or []]
        return {"total": pager.get("total", 0), "current": pager.get("current"), "items": items}

    @mcp.tool()
    def get_case_detail(case_id: str) -> dict:
        """获取功能用例全量详情（steps 已解析为数组）。"""
        d = _get_client().get_case_detail(case_id)
        return {
            "id": d.get("id"), "num": d.get("num"), "name": d.get("name"),
            "projectId": d.get("projectId"), "moduleId": d.get("moduleId"),
            "templateId": d.get("templateId"), "caseEditType": d.get("caseEditType"),
            "prerequisite": d.get("prerequisite"),
            "steps": parse_steps_json(d.get("steps")),
            "textDescription": d.get("textDescription"),
            "expectedResult": d.get("expectedResult"),
            "description": d.get("description"),
            "tags": d.get("tags"),
            "reviewStatus": d.get("reviewStatus"),
            "customFields": [
                {"fieldId": f.get("fieldId"), "fieldName": f.get("fieldName"), "value": f.get("value")}
                for f in (d.get("customFields") or [])
            ],
        }

    @mcp.tool()
    def list_test_plans(project_id: str, keyword: str = "", current: int = 1, page_size: int = 20) -> dict:
        """分页查询项目测试计划（含计划组）。"""
        payload: dict[str, Any] = {"projectId": project_id, "type": "ALL", **_page(current, page_size)}
        if keyword:
            payload["keyword"] = keyword
        pager = _get_client().list_test_plans(payload)
        items = [{
            "id": p.get("id"), "num": p.get("num"), "name": p.get("name"),
            "type": p.get("type"), "status": p.get("status"),
            "plannedStartTime": p.get("plannedStartTime"), "plannedEndTime": p.get("plannedEndTime"),
        } for p in pager.get("list") or []]
        return {"total": pager.get("total", 0), "current": pager.get("current"), "items": items}

    @mcp.tool()
    def get_test_plan(plan_id: str) -> dict:
        """获取测试计划详情（用于编辑场景的字段回显）。"""
        return _get_client().get_test_plan(plan_id)

    @mcp.tool()
    def get_plan_collections(plan_id: str) -> list[dict]:
        """列出测试计划的测试集（含各测试集已关联用例数）。add_cases_to_plan 的 collection_id 从这里取。"""
        mind = _get_client().get_plan_mind(plan_id)
        return [{
            "id": n.get("id"), "name": n.get("text"), "type": n.get("type"),
            "level": n.get("level"), "executeMethod": n.get("executeMethod"),
            "caseCount": _collection_case_count(mind, n.get("id", "")),
        } for n in _flatten_mind_nodes(mind)]

    # ---- 写工具：MS_READ_ONLY=false 时才注册 ----

    if not _read_only():

        @mcp.tool()
        def create_case(project_id: str, name: str, module_id: str = "root",
                        case_edit_type: str = "STEP",
                        steps: list[dict] | None = None,
                        text_description: str = "", expected_result: str = "",
                        prerequisite: str = "", description: str = "",
                        priority: str = "", tags: list[str] | None = None,
                        template_id: str = "") -> dict:
            """创建功能用例。case_edit_type=STEP 时用 steps=[{"desc","result"}]（顺序即步骤序号）；
            =TEXT 时用 text_description + expected_result。priority 取值 P0-P3（默认 P0）。
            template_id 省略时自动用项目默认模板并带出自定义字段默认值。"""
            _require_write()
            client = _get_client()
            template_id, template = _resolve_template(client, project_id, template_id)
            req = build_case_request(
                project_id, name, module_id, case_edit_type, steps,
                text_description, expected_result, prerequisite, description,
                tags, template_id, template, (priority.upper() or None) if priority else None,
            )
            result = client.create_case(req)
            return {"id": result.get("id"), "num": result.get("num"), "name": result.get("name")}

        @mcp.tool()
        def update_case(case_id: str, name: str = "", module_id: str = "",
                        steps: list[dict] | None = None,
                        text_description: str = "", expected_result: str = "",
                        prerequisite: str = "", description: str = "",
                        priority: str = "", tags: list[str] | None = None) -> dict:
            """修改功能用例：未传的字段保持原值（steps 传 [] 可清空步骤；文本字段传非空值覆盖）。
            只支持与原用例相同的编辑模式（STEP/TEXT）。priority 传 P0-P3 修改用例等级。"""
            _require_write()
            client = _get_client()
            detail = client.get_case_detail(case_id)
            project_id = detail.get("projectId")
            case_edit_type = detail.get("caseEditType") or "STEP"

            req = build_case_request(
                project_id,
                name or detail.get("name") or "",
                module_id or detail.get("moduleId") or "root",
                case_edit_type,
                parse_steps_json(detail.get("steps")) if steps is None else steps,
                text_description or detail.get("textDescription") or "",
                expected_result or detail.get("expectedResult") or "",
                prerequisite or detail.get("prerequisite") or "",
                description or detail.get("description") or "",
                tags if tags is not None else detail.get("tags"),
                detail.get("templateId") or "", None,
            )
            if priority:
                template = client.get_case_template(project_id)
                pf = _priority_field(template or {})
                field_id = (pf or {}).get("fieldId")
                if not field_id:
                    raise MSApiError("项目模板没有用例等级字段，无法设置 priority")
                custom_fields = [{"fieldId": f.get("fieldId"), "value": f.get("value")}
                                 for f in detail.get("customFields") or []]
                for f in custom_fields:
                    if f["fieldId"] == field_id:
                        f["value"] = priority.upper()
                        break
                else:
                    custom_fields.append({"fieldId": field_id, "value": priority.upper()})
                req["customFields"] = custom_fields
            req["id"] = case_id
            result = client.update_case(req)
            return {"id": result.get("id", case_id), "num": result.get("num"), "name": result.get("name")}

        @mcp.tool()
        def delete_case(case_id: str) -> dict:
            """删除功能用例（进回收站，可在平台恢复）。删除前必须先向用户确认。"""
            _require_write()
            client = _get_client()
            detail = client.get_case_detail(case_id)
            client.delete_case_to_gc({
                "projectId": detail.get("projectId"),
                "selectAll": False,
                "selectIds": [case_id],
                "deleteAll": True,
            })
            return {"id": case_id, "name": detail.get("name"), "deleted": True}

        @mcp.tool()
        def create_case_module(project_id: str, name: str, parent_id: str = "NONE") -> dict:
            """创建功能用例模块（分类目录），返回新模块 id。顶层模块 parent_id 用 NONE；
            子模块用 list_case_modules 查到的模块 id。"""
            _require_write()
            module_id = _get_client().create_case_module(
                {"projectId": project_id, "name": name, "parentId": parent_id})
            return {"id": module_id, "name": name, "parentId": parent_id}

        @mcp.tool()
        def create_test_plan(project_id: str, name: str, description: str = "",
                             planned_start: str = "", planned_end: str = "",
                             tags: list[str] | None = None, pass_threshold: float = 100) -> dict:
            """创建测试计划。planned_start/planned_end 格式 YYYY-MM-DD 或 YYYY-MM-DD HH:MM。"""
            _require_write()
            payload: dict[str, Any] = {
                "projectId": project_id, "name": name, "type": "TEST_PLAN",
                "groupId": "NONE", "moduleId": "root", "passThreshold": pass_threshold,
                "automaticStatusUpdate": False, "repeatCase": False,
            }
            if description:
                payload["description"] = description
            if planned_start:
                payload["plannedStartTime"] = to_epoch_ms(planned_start)
            if planned_end:
                payload["plannedEndTime"] = to_epoch_ms(planned_end)
            if tags:
                payload["tags"] = tags
            result = _get_client().create_test_plan(payload)
            return {"id": result.get("id"), "num": result.get("num"), "name": result.get("name")}

        @mcp.tool()
        def add_cases_to_plan(plan_id: str, case_ids: list[str], collection_id: str = "") -> dict:
            """把功能用例加入测试计划的测试集。collection_id 省略时用第一个 FUNCTIONAL 测试集
            （可先用 get_plan_collections 查看全部测试集）。重复用例按计划 repeatCase 配置可能被跳过。"""
            _require_write()
            client = _get_client()
            mind = client.get_plan_mind(plan_id)
            target = _functional_leaf(mind, collection_id or None)
            payload = build_mind_edit_payload(mind, plan_id,
                                              client.get_test_plan(plan_id).get("projectId"),
                                              case_ids, collection_id or None)
            client.edit_plan_mind(payload)
            return {
                "planId": plan_id,
                "collectionId": target.get("id"),
                "collectionName": target.get("text"),
                "requestedCount": len(case_ids),
                "note": "已提交关联请求，可用 get_plan_collections 核对各测试集数量",
            }


def main() -> None:  # pragma: no cover
    from mcp.server.mcpserver import MCPServer

    mcp = MCPServer(
        name="metersphere",
        instructions=(
            "MeterSphere 测试平台工具。写操作（create/update/delete/add_cases_to_plan）默认关闭，"
            "需服务端配置 MS_READ_ONLY=false。所有 id 必须来自查询工具，不要凭记忆猜测。"
        ),
    )
    register_tools(mcp)
    mcp.run()


if __name__ == "__main__":  # pragma: no cover
    main()
