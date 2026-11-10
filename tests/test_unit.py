"""纯函数单元测试：签名加密、steps 序列化、时间解析、脑图编辑载荷构造。

不触网。签名协议的正确性最终由对真实服务的 smoke 验证（服务端跑的就是 Java 解密实现）。
"""

import base64
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from metersphere_mcp.client import (
    aes_cbc_encrypt,
    build_signature,
    build_steps_json,
    parse_steps_json,
    to_epoch_ms,
)
from metersphere_mcp.server import (
    _collection_case_count,
    _flatten_mind_nodes,
    _functional_leaf,
    build_case_request,
    build_mind_edit_payload,
)

# AES-CBC 已知答案向量（明文为 ASCII，可直接走 str API）；期望值经 openssl enc -aes-128-cbc 交叉验证
RFC3602_KEY = bytes.fromhex("06a9214036b8a15b512e03d534120006")
RFC3602_IV = bytes.fromhex("3dafba429d9eb430b422da802c9fac41")
RFC3602_PT = "Single block msg"
RFC3602_CT = bytes.fromhex("e353779c1079aeb82708942dbe77181ab97c825e1c785146542d396941bce55d")


def test_aes_cbc_rfc3602_vector():
    ct = aes_cbc_encrypt(RFC3602_PT, RFC3602_KEY, RFC3602_IV)
    # 16 字节明文 + PKCS7 整块填充 = 32 字节密文，期望值经 openssl 交叉验证
    assert ct == RFC3602_CT


def test_build_signature_round_trip():
    ak = "AbCdEf1234567890"
    sk = "ZxYw9876543210vU"
    sig = build_signature(ak, sk, timestamp_ms=1730000000000)
    raw = base64.b64decode(sig)
    decryptor = Cipher(algorithms.AES(sk.encode()), modes.CBC(ak.encode())).decryptor()
    plain = decryptor.update(raw) + decryptor.finalize()
    pad = plain[-1]
    plain = plain[:-pad].decode()
    parts = plain.split("|")
    # 服务端 ApiKeyHandler 的校验规则：parts[0]==ak、末段为毫秒时间戳、30 分钟内
    assert parts[0] == ak
    assert parts[-1] == "1730000000000"
    assert len(parts) == 3


def test_build_steps_json():
    steps = [{"desc": "打开登录页", "result": "显示登录表单"}, {"desc": "输入错误密码", "result": "提示密码错误"}]
    parsed = json.loads(build_steps_json(steps))
    assert [s["desc"] for s in parsed] == ["打开登录页", "输入错误密码"]
    assert [s["num"] for s in parsed] == [0, 1]
    assert parsed[0]["result"] == "显示登录表单"
    assert all("id" in s for s in parsed)


def test_build_steps_skips_blank_and_keeps_ids():
    steps = [{"id": "keep-me", "desc": "步骤一", "result": ""}, {"desc": "  ", "result": ""}]
    parsed = json.loads(build_steps_json(steps))
    assert len(parsed) == 1
    assert parsed[0]["id"] == "keep-me"


def test_parse_steps_json_tolerates_garbage():
    assert parse_steps_json(None) == []
    assert parse_steps_json("") == []
    assert parse_steps_json("not-json") == []
    assert parse_steps_json('[{"id":"1","num":0,"desc":"a","result":"b"}]') == [
        {"id": "1", "num": 0, "desc": "a", "result": "b"}
    ]


def test_to_epoch_ms_formats():
    assert isinstance(to_epoch_ms("2026-11-10"), int)
    assert isinstance(to_epoch_ms("2026-11-10 09:30"), int)
    assert isinstance(to_epoch_ms("2026-11-10T09:30:00+08:00"), int)
    try:
        to_epoch_ms("明天")
        assert False, "should raise"
    except ValueError:
        pass


def _sample_mind():
    # 模拟 GET /test-plan/mind/data 返回：root -> 三个类型节点 -> FUNCTIONAL 下一个测试集
    def node(nid, text, typ, level_parent=False, **kw):
        return {"id": nid, "text": text, "type": typ, **kw}

    return [{
        "data": {"id": "root", "text": "计划"},
        "children": [
            {"data": node("f-type", "功能用例", "FUNCTIONAL", executeMethod="SERIAL", extended=True,
                          pos=0, environmentId="NONE", testResourcePoolId="NONE", retryOnFail=False),
             "children": [
                 {"data": node("f-default", "默认测试集", "FUNCTIONAL", executeMethod="SERIAL",
                               extended=True, pos=4096, grouped=False, environmentId="NONE",
                               testResourcePoolId="NONE", retryOnFail=False, retryType="STEP",
                               retryTimes=1, retryInterval=0, stopOnFail=False, num=3),
                  "children": []},
             ]},
            {"data": node("a-type", "接口用例", "API", executeMethod="PARALLEL", extended=True, pos=8192),
             "children": [
                 {"data": node("a-default", "默认测试集", "API", executeMethod="PARALLEL",
                               extended=True, pos=12288), "children": []},
             ]},
            {"data": node("s-type", "场景用例", "SCENARIO", executeMethod="PARALLEL", extended=True, pos=16384),
             "children": []},
        ],
    }]


def test_flatten_mind_nodes_levels_and_root_skip():
    flat = _flatten_mind_nodes(_sample_mind())
    ids = [n["id"] for n in flat]
    assert "root" not in ids
    assert ids == ["f-type", "f-default", "a-type", "a-default", "s-type"]
    by_id = {n["id"]: n for n in flat}
    assert by_id["f-type"]["level"] == 1
    assert by_id["f-default"]["level"] == 2


def test_flatten_skips_display_nodes():
    # 真实脑图数据：测试集下挂 "N条/环境/资源池" 展示节点（id 为 null），不是测试集
    mind = [{
        "data": {"id": "root", "text": "计划"},
        "children": [
            {"data": {"id": "f-type", "text": "Case", "type": "FUNCTIONAL", "pos": 4096},
             "children": [
                 {"data": {"id": "f-default", "text": "Basic", "type": "FUNCTIONAL", "pos": 4096},
                  "children": [
                      {"data": {"id": None, "text": "1Strip"}, "children": []},
                      {"data": {"id": None, "text": "Default environment"}, "children": []},
                  ]},
             ]},
        ],
    }]
    flat = _flatten_mind_nodes(mind)
    assert [n["id"] for n in flat] == ["f-type", "f-default"]
    assert _collection_case_count(mind, "f-default") == 1
    assert _collection_case_count(mind, "f-type") is None
    assert _collection_case_count(mind, "no-such") is None


def test_functional_leaf_selection_and_errors():
    leaf = _functional_leaf(_sample_mind())
    assert leaf["id"] == "f-default"
    assert _functional_leaf(_sample_mind(), "f-default")["id"] == "f-default"
    try:
        _functional_leaf(_sample_mind(), "a-default")  # API 类型不允许
        assert False
    except Exception:
        pass


def test_build_mind_edit_payload_echoes_all_and_attaches_association():
    payload = build_mind_edit_payload(_sample_mind(), "plan-1", "proj-1", ["case-a", "case-b"])
    edit_list = payload["editList"]
    # 关键：全量回传 5 个节点，缺一个服务端就会删掉对应的测试集
    assert {d["id"] for d in edit_list} == {"f-type", "f-default", "a-type", "a-default", "s-type"}
    target = next(d for d in edit_list if d["id"] == "f-default")
    assoc = target["associateDTOS"][0]
    assert assoc["associateType"] == "FUNCTIONAL"
    assert assoc["projectId"] == "proj-1"
    assert assoc["moduleMaps"]["root"]["selectIds"] == ["case-a", "case-b"]
    assert assoc["syncCase"] is False
    # 其余节点不应携带 associateDTOS
    assert all("associateDTOS" not in d for d in edit_list if d["id"] != "f-default")
    # num 由 pos 推导：pos=4096 -> num=0（updateCollection 里 pos=(num+1)*4096 还原）
    assert target["num"] == 0
    assert all(d["tempCollectionNode"] is False for d in edit_list)


def test_build_case_request_step_and_text():
    template = {"customFields": [
        {"fieldId": "f-prio", "internalFieldKey": "functional_priority", "defaultValue": ""},
        {"fieldId": "f-other", "internalFieldKey": None, "defaultValue": "X"},
    ]}
    req = build_case_request("p1", "登录-错误密码", "root", "STEP",
                              [{"desc": "s1", "result": "r1"}], "", "", "前置", "备注",
                              ["smoke"], "tpl-1", template, "P2")
    assert json.loads(req["steps"])[0]["desc"] == "s1"
    assert req["textDescription"] == ""
    custom = {f["fieldId"]: f["value"] for f in req["customFields"]}
    assert custom["f-prio"] == "P2"
    assert custom["f-other"] == "X"

    req_text = build_case_request("p1", "描述型用例", "root", "TEXT", None,
                                  "文本描述", "预期结果", "", "", None, "tpl-1", None)
    assert req_text["steps"] == ""
    assert req_text["textDescription"] == "文本描述"
    assert req_text["expectedResult"] == "预期结果"
    assert "customFields" not in req_text


def test_priority_defaults_to_p0():
    template = {"customFields": [
        {"fieldId": "f-prio", "internalFieldKey": "functional_priority", "defaultValue": ""},
    ]}
    req = build_case_request("p1", "n", "root", "STEP", [], "", "", "", "", None, "t", template, None)
    assert req["customFields"][0]["value"] == "P0"
    # 模板自带默认值时不覆盖
    template2 = {"customFields": [
        {"fieldId": "f-prio", "internalFieldKey": "functional_priority", "defaultValue": "P3"},
    ]}
    req2 = build_case_request("p1", "n", "root", "STEP", [], "", "", "", "", None, "t", template2, None)
    assert req2["customFields"][0]["value"] == "P3"
