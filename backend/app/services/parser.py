"""格式解析器业务规则：状态流转、字段校验、筛选口径，以及从接入原始事件数据集取数解析。"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.services.ingestion_import import EVENT_MODULE, ST_ADMITTED
from app.store import store

MODULE = "parser"
RESULT_MODULE = "parse_result"
REQUIRED_FIELDS = ["解析编号", "输入格式", "输出格式"]
STATUS_ORDER = ["草稿", "已配置", "生效中", "已废弃"]
ACTION_RULES = {"提交配置": "已配置", "启用解析": "生效中", "废弃解析": "已废弃"}
NEGATIVE_ACTIONS = []


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class ParserService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
        extra: dict[str, str] | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("解析编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        if extra:
            for field, val in extra.items():
                rows = [row for row in rows if val in str(row.get(field, ""))]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"解析配置 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于格式解析器可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"解析配置已{action}"

    # ---------- 从接入原始事件数据集取数解析 ----------

    def run_parse(
        self,
        parser_id: int,
        *,
        channel_id: int,
        file_id: int | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        """读取通道下「已确认」的接入事件并解析，返回带文件标识与行号的解析明细。

        坏记录（坏记录/待投递/已跳过/空白）不在这里取数，保证解析器只接触获准事件；
        每条明细都回指来源文件与原始行号，后续可按原位置逐行核对。
        已被某个解析批次消费过的事件默认不重复解析，避免同一行重复交付。
        """
        parser_entry = store.find(MODULE, parser_id)
        if parser_entry is None:
            return None, f"解析配置 {parser_id} 不存在或已归档"
        if store.find("ingestion", channel_id) is None:
            return None, f"接入通道 {channel_id} 不存在或已归档"

        events = [
            event for event in store.rows(EVENT_MODULE)
            if int(event.get("通道id", 0)) == channel_id and event.get("状态") == ST_ADMITTED
        ]
        if file_id is not None:
            events = [event for event in events if int(event.get("来源文件id", 0)) == file_id]
        events = [event for event in events if not event.get("解析批次")]
        if not events:
            return None, "没有待解析的获准事件（坏记录不会进入解析，已解析事件不重复交付）"

        batch_no = max(
            (int(row.get("解析批次号", 0)) for row in store.rows(RESULT_MODULE)),
            default=0,
        ) + 1
        batch_label = f"PARSE-B{batch_no:04d}"
        mapping = parser_entry.get("字段映射")

        details: list[dict[str, Any]] = []
        for event in events:
            snapshot = event.get("字段快照") or {}
            output = self._apply_mapping(snapshot, mapping)
            detail = {
                "解析批次": batch_label,
                "解析批次号": batch_no,
                "解析配置id": parser_id,
                "解析编号": parser_entry.get("解析编号"),
                "通道id": channel_id,
                "来源事件id": event.get("id"),
                "文件标识": event.get("文件标识"),
                "来源文件id": event.get("来源文件id"),
                "原始行号": event.get("原始行号"),
                "原文": event.get("原文"),
                "输入格式": parser_entry.get("输入格式"),
                "输出格式": parser_entry.get("输出格式"),
                "解析结果": output,
                "解析时间": _now(),
            }
            store.insert(RESULT_MODULE, detail)
            details.append(detail)
            event["解析批次"] = batch_label

        return {
            "解析批次": batch_label,
            "解析配置id": parser_id,
            "通道id": channel_id,
            "文件范围id": file_id,
            "解析条数": len(details),
            "明细": details,
        }, f"解析完成，本批次共 {len(details)} 条获准事件"

    @staticmethod
    def _apply_mapping(snapshot: dict[str, Any], mapping: Any) -> dict[str, Any]:
        """按解析配置上的字段映射产出结果；映射缺失或写成样例占位时退化为原字段透传。"""
        if not isinstance(mapping, str) or ("：" not in mapping and ":" not in mapping):
            return dict(snapshot)
        separator = "：" if "：" in mapping else ":"
        result: dict[str, Any] = {}
        for pair in mapping.replace("；", ";").replace("，", ",").replace(";", ",").split(","):
            pair = pair.strip()
            if not pair or separator not in pair:
                continue
            source, target = (part.strip() for part in pair.split(separator, 1))
            if source in snapshot:
                result[target] = snapshot[source]
        return result or dict(snapshot)

    def list_results(
        self,
        *,
        channel_id: int | None = None,
        file_id: int | None = None,
        batch: str | None = None,
    ) -> list[dict[str, Any]]:
        rows = store.rows(RESULT_MODULE)
        if channel_id is not None:
            rows = [row for row in rows if int(row.get("通道id", 0)) == channel_id]
        if file_id is not None:
            rows = [row for row in rows if int(row.get("来源文件id", 0)) == file_id]
        if batch:
            rows = [row for row in rows if row.get("解析批次") == batch]
        return rows
