"""输入格式登记：接入通道按这里登记的格式接收上传文件。

通道的「输入格式」字段必须能在登记表里找到对应格式，上传文件才会被接受。
当前只依赖标准库，CSV 类格式共用同一套按行解析器，JSON 行格式单独处理；
新增格式时在 FORMATS 里登记一条即可，不需要改动导入主流程。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class FormatSpec:
    """一种可接入的输入格式。

    kind:      解析器类型，csv 走按行分隔解析，jsonl 走逐行 JSON 解析。
    delimiter: csv 分隔符。
    quotechar: csv 引号字符；引号未闭合时该行判定为坏记录。
    field_count: 每行应有的字段数；数量不符判定为坏记录。None 表示不校验。
    headers:   字段序号到字段名的映射，供解析器输出结构化事件。
    """

    name: str
    kind: str
    delimiter: str = ","
    quotechar: str = '"'
    field_count: int | None = None
    headers: tuple[str, ...] = ()

    def header_name(self, index: int) -> str:
        if index < len(self.headers):
            return self.headers[index]
        return f"字段{index + 1}"


# 货运记录：运单号、始发站、目的站、货物名称、重量（千克）
FREIGHT_CSV = FormatSpec(
    name="CSV-货运记录",
    kind="csv",
    delimiter=",",
    quotechar='"',
    field_count=5,
    headers=("运单号", "始发站", "目的站", "货物名称", "重量"),
)

# 通用 CSV：不固定字段数，坏行只看引号闭合与分隔结果
GENERIC_CSV = FormatSpec(
    name="CSV-通用文本",
    kind="csv",
)

# JSON Lines：每行一个 JSON 对象
JSON_LINES = FormatSpec(
    name="JSONL-事件行",
    kind="jsonl",
)

# 登记顺序即前端可选项顺序；用规范化名称做键，避免大小写/空格差异对不上。
FORMATS: tuple[FormatSpec, ...] = (FREIGHT_CSV, GENERIC_CSV, JSON_LINES)


def _normalize(name: str) -> str:
    return str(name or "").strip().casefold()


_REGISTRY: dict[str, FormatSpec] = {_normalize(spec.name): spec for spec in FORMATS}


def supported_format_names() -> list[str]:
    return [spec.name for spec in FORMATS]


def resolve_format(name: str) -> FormatSpec | None:
    """按通道声明的输入格式找登记项；未登记返回 None，由调用方决定报错口径。"""
    return _REGISTRY.get(_normalize(name))


def parse_line(spec: FormatSpec, raw_line: str) -> dict[str, Any]:
    """按登记格式解析单行文本，返回结构化结果。

    正常返回 {"ok": True, "fields": [...], "record": {...}}；
    坏记录返回 {"ok": False, "error": 可读原因}，不抛异常，由导入流程留存原文。
    """
    if spec.kind == "jsonl":
        return _parse_jsonl(raw_line)
    return _parse_csv(spec, raw_line)


def _parse_csv(spec: FormatSpec, raw_line: str) -> dict[str, Any]:
    import csv
    import io

    # strict 让未闭合引号直接报错；单行喂入读取器，保证错误能指回具体行号。
    reader = csv.reader(
        io.StringIO(raw_line),
        delimiter=spec.delimiter,
        quotechar=spec.quotechar,
        skipinitialspace=False,
        strict=True,
    )
    try:
        fields = next(reader)
    except (csv.Error, StopIteration) as exc:
        return {"ok": False, "error": f"CSV 语法错误（引号未闭合或分隔符异常）：{exc}"}

    if spec.field_count is not None and len(fields) != spec.field_count:
        return {
            "ok": False,
            "error": f"字段数量不符：期望 {spec.field_count} 个，实际 {len(fields)} 个",
        }

    record = {spec.header_name(index): value for index, value in enumerate(fields)}
    return {"ok": True, "fields": fields, "record": record}


def _parse_jsonl(raw_line: str) -> dict[str, Any]:
    import json

    try:
        payload = json.loads(raw_line)
    except json.JSONDecodeError as exc:
        return {"ok": False, "error": f"JSON 语法错误：{exc.msg}（位置 {exc.pos}）"}
    if not isinstance(payload, dict):
        return {"ok": False, "error": "JSON 行必须是对象，不能是数组或标量"}
    return {"ok": True, "fields": list(payload.values()), "record": payload}
