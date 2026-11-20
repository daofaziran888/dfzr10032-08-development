"""上传文件导入：按通道声明的输入格式形成接入原始事件数据集。

设计要点（对应接入侧的硬性要求）：

1. 每个物理行都落一条接入原始事件，带「文件标识 + 原始行号 + 原文」，
   坏记录同样留存位置和原文，状态标成坏记录，永远不会被当成有效事件交付。
2. 两种交付策略：
   - 可用先行：结构正常的行立即确认为获准事件，坏记录挂起等修正；
   - 完整交付：只要有一行坏，整份文件全部阻断，一行都不交给解析器。
3. 重投修正文件按通道维度记住「已确认行号」：已确认的行再次出现一律跳过，
   成功行不会二次交付；修正行沿用原文件的行号定位。
4. 修正成功后，原坏记录保留原文但置为「已修正」，可追查整条修复链路。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.services.format_registry import parse_line, resolve_format, supported_format_names
from app.store import store

FILE_MODULE = "ingestion_file"
EVENT_MODULE = "ingestion_event"

# 事件状态
ST_PENDING = "待投递"        # 结构正常但被完整交付策略阻断
ST_ADMITTED = "已确认"       # 获准进入接入原始事件数据集，可被解析器读取
ST_BAD = "坏记录"            # 解析失败，保留原文与位置，不是有效事件
ST_SKIPPED = "已跳过"        # 重投时撞上已确认行号，不再二次交付
ST_BLANK = "空白跳过"        # 物理空行，占住行号但不参与交付
ST_FIXED = "已修正"          # 原坏记录已被修正文件中的同号行替换

# 文件状态
F_PARTIAL = "部分已交付"     # 可用先行：好行已交付，仍有坏记录
F_DELIVERED = "已全部交付"
F_BLOCKED = "已阻断"         # 完整交付：存在坏记录，整份压住

POLICY_AVAILABLE = "可用先行"
POLICY_COMPLETE = "完整交付"
DELIVERY_POLICIES = (POLICY_AVAILABLE, POLICY_COMPLETE)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _file_identifier(file_id: int) -> str:
    return f"FILE-{file_id:04d}"


class IngestionImportService:
    # ---------- 通道与格式 ----------

    def _load_channel(self, channel_id: int) -> dict[str, Any] | None:
        return store.find("ingestion", channel_id)

    def _channel_format(self, channel: dict[str, Any]):
        return resolve_format(str(channel.get("输入格式") or ""))

    def supported_formats(self) -> list[str]:
        return supported_format_names()

    # ---------- 行号口径 ----------

    def confirmed_lines(self, channel_id: int) -> set[int]:
        """通道维度已确认的行号：重投去重就靠它。"""
        return {
            int(event["原始行号"])
            for event in store.rows(EVENT_MODULE)
            if int(event.get("通道id", 0)) == channel_id and event.get("状态") == ST_ADMITTED
        }

    @staticmethod
    def _split_lines(content: str) -> list[str]:
        # splitlines 会丢掉末尾换行产生的空串，正好不制造多余空行；
        # 中间空行保留，单独占一个行号，避免后续行号错位。
        return content.splitlines()

    # ---------- 首次导入 ----------

    def import_file(
        self,
        channel_id: int,
        *,
        filename: str,
        content: str,
        delivery_policy: str = POLICY_AVAILABLE,
        remark: str | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        channel = self._load_channel(channel_id)
        if channel is None:
            return None, f"接入通道 {channel_id} 不存在或已归档"
        spec = self._channel_format(channel)
        if spec is None:
            return (
                None,
                f"通道声明的输入格式「{channel.get('输入格式')}」未登记，"
                f"可接入格式：{'、'.join(supported_format_names())}",
            )
        if delivery_policy not in DELIVERY_POLICIES:
            return None, f"交付策略只能是 {POLICY_AVAILABLE} 或 {POLICY_COMPLETE}"

        file_id = store.next_id(FILE_MODULE)
        identifier = _file_identifier(file_id)
        confirmed = self.confirmed_lines(channel_id)

        file_row: dict[str, Any] = {
            "id": file_id,
            "文件标识": identifier,
            "通道id": channel_id,
            "通道编号": channel.get("通道编号"),
            "输入格式": spec.name,
            "原始文件名": filename or f"{identifier}.txt",
            "交付策略": delivery_policy,
            "上传批次": 1,
            "重投来源文件": None,
            "总行数": 0,
            "正常行数": 0,
            "坏记录数": 0,
            "跳过行数": 0,
            "状态": F_DELIVERED,
            "摘要": remark or "",
            "上传时间": _now(),
        }
        store.rows(FILE_MODULE).append(file_row)

        events: list[dict[str, Any]] = []
        bad_count = 0
        for line_no, raw_line in enumerate(self._split_lines(content), start=1):
            event = self._build_event(identifier, channel_id, file_id, line_no, line_no, raw_line, spec)
            store.insert(EVENT_MODULE, event)
            events.append(event)
            if event["状态"] == ST_BAD:
                bad_count += 1

        self._apply_delivery(file_row, events, bad_count, confirmed)
        return file_row, "文件已按通道声明格式导入"

    def _build_event(
        self,
        identifier: str,
        channel_id: int,
        file_id: int,
        line_no: int,
        logical_line: int,
        raw_line: str,
        spec,
    ) -> dict[str, Any]:
        event = {
            "文件标识": identifier,
            "来源文件id": file_id,
            "通道id": channel_id,
            "原始行号": logical_line,
            "物理行号": line_no,
            "原文": raw_line,
            "状态": ST_PENDING,
            "错误原因": None,
            "字段快照": None,
            "解析批次": None,
            "上传时间": _now(),
        }
        if not raw_line.strip():
            event["状态"] = ST_BLANK
            return event
        result = parse_line(spec, raw_line)
        if result["ok"]:
            event["字段快照"] = result["record"]
        else:
            event["状态"] = ST_BAD
            event["错误原因"] = result["error"]
        return event

    def _apply_delivery(
        self,
        file_row: dict[str, Any],
        events: list[dict[str, Any]],
        bad_count: int,
        already_confirmed: set[int],
    ) -> None:
        """按交付策略把正常行确认为获准事件，或整份阻断。"""
        candidates = [event for event in events if event["状态"] in (ST_PENDING, ST_BAD)]
        normal = [event for event in candidates if event["状态"] == ST_PENDING]
        blocked = file_row["交付策略"] == POLICY_COMPLETE and bad_count > 0

        for event in normal:
            if blocked:
                continue  # 完整交付：好行也先压住，等修正文件解锁
            if event["原始行号"] in already_confirmed:
                event["状态"] = ST_SKIPPED
                continue
            event["状态"] = ST_ADMITTED

        file_row["总行数"] = len(events)
        file_row["正常行数"] = sum(1 for event in events if event["状态"] in (ST_ADMITTED, ST_PENDING, ST_SKIPPED, ST_FIXED))
        file_row["坏记录数"] = sum(1 for event in events if event["状态"] == ST_BAD)
        file_row["跳过行数"] = sum(1 for event in events if event["状态"] == ST_SKIPPED)
        if blocked:
            file_row["状态"] = F_BLOCKED
        elif file_row["坏记录数"]:
            file_row["状态"] = F_PARTIAL
        else:
            file_row["状态"] = F_DELIVERED

    # ---------- 重投修正文件 ----------

    def reimport_file(
        self,
        file_id: int,
        *,
        filename: str,
        content: str,
        line_offset: int = 1,
        remark: str | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        original = store.find(FILE_MODULE, file_id)
        if original is None:
            return None, f"接入文件 {file_id} 不存在"
        channel = self._load_channel(int(original["通道id"]))
        spec = self._channel_format(channel) if channel else None
        if spec is None:
            return None, "通道已不存在或输入格式登记被移除，无法重投"
        if line_offset < 1:
            return None, "行号偏移必须从 1 开始"

        correction_id = store.next_id(FILE_MODULE)
        correction_identifier = _file_identifier(correction_id)
        confirmed = self.confirmed_lines(int(original["通道id"]))

        correction_row: dict[str, Any] = {
            "id": correction_id,
            "文件标识": correction_identifier,
            "通道id": original["通道id"],
            "通道编号": original["通道编号"],
            "输入格式": spec.name,
            "原始文件名": filename or f"{correction_identifier}.txt",
            "交付策略": original["交付策略"],
            "上传批次": int(original["上传批次"]) + 1,
            "重投来源文件": original["文件标识"],
            "总行数": 0,
            "正常行数": 0,
            "坏记录数": 0,
            "跳过行数": 0,
            "状态": F_DELIVERED,
            "摘要": remark or "",
            "上传时间": _now(),
        }
        store.rows(FILE_MODULE).append(correction_row)

        all_events = store.rows(EVENT_MODULE)
        fixed_lines: list[int] = []
        still_bad = 0
        skipped = 0

        for physical_no, raw_line in enumerate(self._split_lines(content), start=1):
            logical_line = line_offset + physical_no - 1
            if not raw_line.strip():
                continue  # 修正文件里的空行不占业务行号
            if logical_line in confirmed:
                # 已确认行再次出现：记一条跳过事件，绝不二次交付。
                skip_event = {
                    "文件标识": correction_identifier,
                    "来源文件id": correction_id,
                    "通道id": original["通道id"],
                    "原始行号": logical_line,
                    "物理行号": physical_no,
                    "原文": raw_line,
                    "状态": ST_SKIPPED,
                    "错误原因": None,
                    "字段快照": None,
                    "解析批次": None,
                    "上传时间": _now(),
                }
                store.insert(EVENT_MODULE, skip_event)
                skipped += 1
                continue

            result = parse_line(spec, raw_line)
            event = {
                "文件标识": correction_identifier,
                "来源文件id": correction_id,
                "通道id": original["通道id"],
                "原始行号": logical_line,
                "物理行号": physical_no,
                "原文": raw_line,
                "状态": ST_PENDING,
                "错误原因": None,
                "字段快照": None,
                "解析批次": None,
                "上传时间": _now(),
            }
            if result["ok"]:
                event["状态"] = ST_ADMITTED
                event["字段快照"] = result["record"]
                fixed_lines.append(logical_line)
                # 原坏记录留痕：原文和位置保留，状态改为已修正，指向修正文件。
                for old in all_events:
                    if (
                        int(old.get("通道id", 0)) == int(original["通道id"])
                        and int(old.get("原始行号", 0)) == logical_line
                        and old.get("状态") == ST_BAD
                    ):
                        old["状态"] = ST_FIXED
                        old["修正文件标识"] = correction_identifier
            else:
                event["状态"] = ST_BAD
                event["错误原因"] = result["error"]
                still_bad += 1
            store.insert(EVENT_MODULE, event)

        # 完整交付策略：修正后若该文件再无坏记录，释放当初被阻断的正常行。
        if original["交付策略"] == POLICY_COMPLETE:
            remaining_bad = [
                event for event in all_events
                if int(event.get("来源文件id", 0)) == file_id and event["状态"] == ST_BAD
            ]
            if not remaining_bad and still_bad == 0:
                for event in all_events:
                    if (
                        int(event.get("来源文件id", 0)) == file_id
                        and event["状态"] == ST_PENDING
                    ):
                        event["状态"] = ST_ADMITTED

        self._refresh_file_state(original)
        self._refresh_file_state(correction_row)
        message = (
            f"修正文件已接收：修正行号 {fixed_lines or '无'}，跳过已确认行 {skipped} 条"
        )
        return correction_row, message

    def _refresh_file_state(self, file_row: dict[str, Any]) -> None:
        events = [
            event for event in store.rows(EVENT_MODULE)
            if int(event.get("来源文件id", 0)) == int(file_row["id"])
        ]
        bad = sum(1 for event in events if event["状态"] == ST_BAD)
        blocked_pending = sum(1 for event in events if event["状态"] == ST_PENDING)
        file_row["总行数"] = len(events)
        file_row["正常行数"] = sum(
            1 for event in events
            if event["状态"] in (ST_ADMITTED, ST_PENDING, ST_SKIPPED, ST_FIXED)
        )
        file_row["坏记录数"] = bad
        file_row["跳过行数"] = sum(1 for event in events if event["状态"] == ST_SKIPPED)
        if blocked_pending or (file_row["交付策略"] == POLICY_COMPLETE and bad):
            file_row["状态"] = F_BLOCKED
        elif bad:
            file_row["状态"] = F_PARTIAL
        else:
            file_row["状态"] = F_DELIVERED

    # ---------- 查询 ----------

    def list_files(self, channel_id: int | None = None) -> list[dict[str, Any]]:
        rows = store.rows(FILE_MODULE)
        if channel_id is not None:
            rows = [row for row in rows if int(row.get("通道id", 0)) == channel_id]
        return rows

    def get_file(self, file_id: int) -> dict[str, Any] | None:
        return store.find(FILE_MODULE, file_id)

    def list_events(
        self,
        *,
        file_id: int | None = None,
        channel_id: int | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        rows = store.rows(EVENT_MODULE)
        if file_id is not None:
            rows = [row for row in rows if int(row.get("来源文件id", 0)) == file_id]
        if channel_id is not None:
            rows = [row for row in rows if int(row.get("通道id", 0)) == channel_id]
        if status:
            rows = [row for row in rows if row.get("状态") == status]
        return rows

    def dead_letters(self, file_id: int | None = None) -> list[dict[str, Any]]:
        """坏记录清单：位置（文件标识+行号）和原文都在，直接可追查。"""
        return self.list_events(file_id=file_id, status=ST_BAD)


ingestion_import_service = IngestionImportService()
