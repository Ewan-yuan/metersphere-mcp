"""MeterSphere REST API client with AK/SK signing."""

from __future__ import annotations

import base64
import json
import time
import uuid
from typing import Any
from urllib.parse import quote

import httpx
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


class MSApiError(Exception):
    def __init__(self, message: str, code: int | None = None):
        super().__init__(message)
        self.code = code


def aes_cbc_encrypt(plaintext: str, key: bytes, iv: bytes) -> bytes:
    """AES-128-CBC + PKCS7，对齐 Java CodingUtils.aesEncrypt(src, secretKey, accessKey)。"""
    if len(key) not in (16, 24, 32):
        raise ValueError(f"AES key must be 16/24/32 bytes, got {len(key)}")
    data = plaintext.encode("utf-8")
    pad = 16 - len(data) % 16
    data += bytes([pad]) * pad
    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    return encryptor.update(data) + encryptor.finalize()


def build_signature(access_key: str, secret_key: str, timestamp_ms: int | None = None) -> str:
    """签名 = Base64(AES-CBC("accessKey|uuid|timestampMs", key=SK, iv=AK))，服务端 30 分钟有效期。"""
    ts = timestamp_ms if timestamp_ms is not None else int(time.time() * 1000)
    payload = f"{access_key}|{uuid.uuid4()}|{ts}"
    encrypted = aes_cbc_encrypt(payload, secret_key.encode("utf-8"), access_key.encode("utf-8"))
    return base64.b64encode(encrypted).decode("ascii")


def build_steps_json(steps: list[dict[str, Any]]) -> str:
    """把 [{desc, result}] 转成平台的 steps JSON 字符串 [{"id","num","desc","result"}]。"""
    items = []
    for i, step in enumerate(steps):
        desc = (step.get("desc") or "").strip()
        if not desc:
            continue
        items.append({
            "id": step.get("id") or uuid.uuid4().hex,
            "num": i,
            "desc": desc,
            "result": step.get("result") or "",
        })
    return json.dumps(items, ensure_ascii=False)


def parse_steps_json(steps: str | None) -> list[dict[str, str]]:
    if not steps:
        return []
    try:
        raw = json.loads(steps)
    except (TypeError, ValueError):
        return []
    return [{"id": s.get("id", ""), "num": s.get("num", 0), "desc": s.get("desc", ""), "result": s.get("result", "")} for s in raw]


def to_epoch_ms(value: str) -> int:
    """支持 YYYY-MM-DD / YYYY-MM-DD HH:MM / ISO 8601，返回毫秒时间戳。"""
    import datetime as dt
    v = value.strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            naive = dt.datetime.strptime(v[:19] if fmt == "%Y-%m-%d %H:%M:%S" else v, fmt)
            return int(naive.timestamp() * 1000)
        except ValueError:
            continue
    try:
        return int(dt.datetime.fromisoformat(value).timestamp() * 1000)
    except ValueError as e:
        raise ValueError(f"无法解析时间: {value!r}，支持 YYYY-MM-DD 或 YYYY-MM-DD HH:MM") from e


class MSClient:
    def __init__(self, base_url: str, access_key: str, secret_key: str, timeout: float = 30.0):
        if not (base_url and access_key and secret_key):
            raise MSApiError("缺少配置：需要 MS_BASE_URL / MS_ACCESS_KEY / MS_SECRET_KEY")
        self.base_url = base_url.rstrip("/")
        self.access_key = access_key
        self.secret_key = secret_key
        self.http = httpx.Client(timeout=timeout, follow_redirects=True)

    def _headers(self) -> dict[str, str]:
        return {
            "accessKey": self.access_key,
            "signature": build_signature(self.access_key, self.secret_key),
        }

    def _request(self, method: str, path: str, *, params: dict | None = None,
                 json_body: Any = None, multipart_request: dict | None = None) -> Any:
        url = f"{self.base_url}{path}"
        headers = self._headers()
        kwargs: dict[str, Any] = {"params": params, "headers": headers}
        if multipart_request is not None:
            # @RequestPart("request") 需要 part 级 Content-Type: application/json
            kwargs["files"] = {
                "request": (None, json.dumps(multipart_request, ensure_ascii=False), "application/json")
            }
        elif json_body is not None:
            kwargs["content"] = json.dumps(json_body, ensure_ascii=False)
            headers["Content-Type"] = "application/json"

        resp = self.http.request(method, url, **kwargs)
        if resp.status_code == 401:
            raise MSApiError("鉴权失败：检查 MS_ACCESS_KEY / MS_SECRET_KEY 是否有效、key 是否启用", code=401)
        try:
            body = resp.json()
        except ValueError:
            resp.raise_for_status()
            raise MSApiError(f"非 JSON 响应 (HTTP {resp.status_code}): {resp.text[:200]}")

        if isinstance(body, dict) and "code" in body and not isinstance(body.get("list"), list):
            code = body.get("code")
            if code not in (200, 100200):  # MsHttpResultCode.SUCCESS = 100200，旧接口偶用 200
                detail = body.get("messageDetail") or ""
                raise MSApiError(f"{body.get('message') or '请求失败'}{' | ' + str(detail) if detail else ''}", code=code)
            return body.get("data", "")
        resp.raise_for_status()
        return body

    # ---- 只读 ----

    def whoami(self) -> list[dict]:
        """GET /user/api/key/list：走 ApiKey 过滤器，返回值含 createUser 即当前用户。"""
        return self._request("GET", "/user/api/key/list")

    def list_projects(self, organization_id: str) -> list[dict]:
        path = f"/project/list/visible/{quote(str(organization_id))}"
        return self._request("GET", path)

    def get_case_template(self, project_id: str) -> dict:
        return self._request("GET", f"/functional/case/default/template/field/{quote(str(project_id))}")

    def list_case_modules(self, project_id: str) -> list[dict]:
        return self._request("GET", f"/functional/case/module/tree/{quote(str(project_id))}")

    def search_cases(self, payload: dict) -> dict:
        return self._request("POST", "/functional/case/page", json_body=payload)

    def get_case_detail(self, case_id: str) -> dict:
        return self._request("GET", f"/functional/case/detail/{quote(str(case_id))}")

    def list_test_plans(self, payload: dict) -> dict:
        return self._request("POST", "/test-plan/page", json_body=payload)

    def get_test_plan(self, plan_id: str) -> dict:
        return self._request("GET", f"/test-plan/{quote(str(plan_id))}")

    def get_plan_mind(self, plan_id: str) -> list[dict]:
        return self._request("GET", f"/test-plan/mind/data/{quote(str(plan_id))}")

    # ---- 写入 ----

    def create_case(self, request: dict) -> dict:
        return self._request("POST", "/functional/case/add", multipart_request=request)

    def update_case(self, request: dict) -> dict:
        return self._request("POST", "/functional/case/update", multipart_request=request)

    def delete_case_to_gc(self, payload: dict) -> Any:
        return self._request("POST", "/functional/case/batch/delete-to-gc", json_body=payload)

    def create_case_module(self, payload: dict) -> str:
        return self._request("POST", "/functional/case/module/add", json_body=payload)

    def create_test_plan(self, payload: dict) -> dict:
        return self._request("POST", "/test-plan/add", json_body=payload)

    def edit_plan_mind(self, payload: dict) -> Any:
        return self._request("POST", "/test-plan/mind/data/edit", json_body=payload)
