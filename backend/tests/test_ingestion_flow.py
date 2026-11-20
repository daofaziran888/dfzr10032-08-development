"""端到端核对：三行货运样本（中间一行缺闭合引号）的导入、坏记录追查、重投与解析。

运行：python3 -I tests/test_ingestion_flow.py
"""
from __future__ import annotations

import sys

sys.path.insert(0, "/workspace/backend")

from fastapi.testclient import TestClient

from app.main import app
from app.services.ingestion_import import (
    F_BLOCKED,
    F_DELIVERED,
    F_PARTIAL,
    ST_ADMITTED,
    ST_BAD,
    ST_FIXED,
    ST_SKIPPED,
)
from app.services.parser import RESULT_MODULE
from app.store import store

client = TestClient(app)

PASS = 0
FAIL = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name} {detail}")


def reset_store() -> None:
    """每个场景前重置内存仓库，保证用例互不干扰。"""
    for table in ("ingestion_file", "ingestion_event", RESULT_MODULE):
        store.rows(table).clear()
    for row in store.rows("ingestion"):
        row["输入格式"] = "CSV-货运记录"
    for row in store.rows("parser"):
        row["输入格式"] = "CSV-货运记录"
        row["输出格式"] = "货运标准事件"
        row["字段映射"] = "运单号：waybill_no,货物名称：cargo,重量：weight"


# 三行样本：两行正常货运记录，中间夹一行缺少闭合引号的坏文本。
SAMPLE = "\n".join(
    [
        'YD20260001,上海,北京,精密仪器,120',
        'YD20260002,广州,成都,电子元件,80',  # 这一行在下面替换成坏记录
        'YD20260003,深圳,杭州,机械配件,260',
    ]
)
BAD_LINE = 'YD20260002,广州,成都,"电子元件,80'  # 引号开了没闭
BAD_SAMPLE = SAMPLE.replace('YD20260002,广州,成都,电子元件,80', BAD_LINE)
FIXED_LINE = 'YD20260002,广州,成都,电子元件,80'


def scenario_available_first() -> None:
    print("\n[场景1] 可用先行：好行先交付，坏记录挂起并指回第二行")
    reset_store()

    resp = client.post(
        "/api/ingestion/imports/channels/1/files",
        json={"filename": "freight.csv", "content": BAD_SAMPLE, "delivery_policy": "可用先行"},
    )
    check("导入接口返回成功", resp.status_code == 200 and resp.json()["ok"], resp.text)
    file_row = resp.json()["entry"]
    file_id = file_row["id"]
    check("文件状态为部分已交付", file_row["状态"] == F_PARTIAL, str(file_row["状态"]))
    check("总行数为 3", file_row["总行数"] == 3, str(file_row["总行数"]))
    check("坏记录数为 1", file_row["坏记录数"] == 1, str(file_row["坏记录数"]))
    check("结果带文件标识", str(file_row["文件标识"]).startswith("FILE-"))

    events = client.get(f"/api/ingestion/imports/files/{file_id}/events").json()["items"]
    by_line = {event["原始行号"]: event for event in events}
    check("每行都有原始行号与原文", all(event["原文"] for event in events))
    check("第1行已确认", by_line[1]["状态"] == ST_ADMITTED, str(by_line[1]["状态"]))
    check("第2行是坏记录", by_line[2]["状态"] == ST_BAD, str(by_line[2]["状态"]))
    check("第3行已确认", by_line[3]["状态"] == ST_ADMITTED, str(by_line[3]["状态"]))
    check("坏记录保留原文", BAD_LINE in by_line[2]["原文"])
    check("坏记录带失败原因", bool(by_line[2]["错误原因"]), str(by_line[2]))
    check("坏记录带文件标识和行号", by_line[2]["文件标识"] == file_row["文件标识"] and by_line[2]["原始行号"] == 2)

    dead = client.get(f"/api/ingestion/imports/files/{file_id}/dead-letters").json()["items"]
    check("坏记录追查清单指回第二行", len(dead) == 1 and dead[0]["原始行号"] == 2, str(dead))
    check("追查清单含原文", dead and BAD_LINE in dead[0]["原文"])

    # 解析器只读取获准事件：坏记录不能混进有效事件
    run = client.post(
        "/api/parser/1/run",
        json={"channel_id": 1, "file_id": file_id},
    ).json()
    check("解析成功且只解析 2 条好行", run["ok"] and run["entry"]["解析条数"] == 2, str(run))
    details = run["entry"]["明细"]
    check("解析明细关联文件标识", all(d["文件标识"] == file_row["文件标识"] for d in details))
    check("解析明细关联原始行号", sorted(d["原始行号"] for d in details) == [1, 3])
    check("字段映射生效", details[0]["解析结果"].get("waybill_no") == "YD20260001", str(details[0]))
    check("明细保留原文便于核对", all(d["原文"] for d in details))

    # 重复触发解析：已消费事件不重复交付
    again = client.post("/api/parser/1/run", json={"channel_id": 1, "file_id": file_id}).json()
    check("重复解析不会再次交付已确认行", not again["ok"] and "没有待解析" in again["message"], str(again))

    print("\n[场景1续] 重投修正文件：沿用原行号，首末行不重复进入解析")
    # 修正文件按完整三行重投，验证第1、3行被识别为已确认而跳过
    re = client.post(
        f"/api/ingestion/imports/files/{file_id}/reimport",
        json={"filename": "freight-fixed.csv", "content": SAMPLE, "line_offset": 1},
    ).json()
    check("重投接口成功", re["ok"], str(re))
    correction = re["entry"]
    check("修正文件记录来源文件", correction["重投来源文件"] == file_row["文件标识"])
    check("修正文件跳过 2 条已确认行", correction["跳过行数"] == 2, str(correction["跳过行数"]))
    check("修正文件无坏记录", correction["坏记录数"] == 0, str(correction["坏记录数"]))

    correction_events = client.get(
        f"/api/ingestion/imports/files/{correction['id']}/events"
    ).json()["items"]
    statuses = {event["原始行号"]: event["状态"] for event in correction_events}
    check("第1行标记已跳过", statuses.get(1) == ST_SKIPPED, str(statuses))
    check("第2行在修正文件中确认", statuses.get(2) == ST_ADMITTED, str(statuses))
    check("第3行标记已跳过", statuses.get(3) == ST_SKIPPED, str(statuses))

    # 原坏记录留痕：原文还在，状态改为已修正
    old_events = client.get(f"/api/ingestion/imports/files/{file_id}/events").json()["items"]
    old_bad = next(event for event in old_events if event["原始行号"] == 2)
    check("原坏记录置为已修正且原文保留", old_bad["状态"] == ST_FIXED and BAD_LINE in old_bad["原文"], str(old_bad))
    check("原坏记录指向修正文件", old_bad.get("修正文件标识") == correction["文件标识"])

    # 再跑解析：只有新确认的第2行进入，首末行不重复
    run2 = client.post("/api/parser/1/run", json={"channel_id": 1}).json()
    check("二次解析只交付修正的第2行", run2["ok"] and run2["entry"]["解析条数"] == 1, str(run2))
    parsed_lines = [d["原始行号"] for d in run2["entry"]["明细"]]
    check("新批次明细行号为 2", parsed_lines == [2], str(parsed_lines))
    check("新批次明细沿用原文件标识", run2["entry"]["明细"][0]["文件标识"] == correction["文件标识"])

    all_results = client.get("/api/ingestion/imports/parse-results", params={"channel_id": 1}).json()["items"]
    check("累计解析明细 3 条且行号不重复", len(all_results) == 3 and
          sorted(d["原始行号"] for d in all_results) == [1, 2, 3], str(len(all_results)))


def scenario_complete_delivery() -> None:
    print("\n[场景2] 完整交付：存在坏行时整份阻断，修正后一次性释放")
    reset_store()

    resp = client.post(
        "/api/ingestion/imports/channels/1/files",
        json={"filename": "freight.csv", "content": BAD_SAMPLE, "delivery_policy": "完整交付"},
    ).json()
    file_row = resp["entry"]
    file_id = file_row["id"]
    check("文件状态为已阻断", file_row["状态"] == F_BLOCKED, str(file_row["状态"]))

    events = client.get(f"/api/ingestion/imports/files/{file_id}/events").json()["items"]
    check("好行也保持待投递、未进入获准数据集",
          all(event["状态"] != ST_ADMITTED for event in events),
          str([(e["原始行号"], e["状态"]) for e in events]))

    run = client.post("/api/parser/1/run", json={"channel_id": 1, "file_id": file_id}).json()
    check("阻断期间解析器取不到获准事件", not run["ok"], str(run))

    # 只重投第二行的修正内容，行号偏移 2
    re = client.post(
        f"/api/ingestion/imports/files/{file_id}/reimport",
        json={"filename": "line2-fix.csv", "content": FIXED_LINE, "line_offset": 2},
    ).json()
    check("单行修正重投成功", re["ok"], str(re))

    events_after = client.get(f"/api/ingestion/imports/files/{file_id}/events").json()["items"]
    by_line = {event["原始行号"]: event["状态"] for event in events_after}
    check("修正后第1、3行被释放为已确认", by_line.get(1) == ST_ADMITTED and by_line.get(3) == ST_ADMITTED, str(by_line))
    check("原文件已全部交付", client.get("/api/ingestion/imports/files").json()["items"][0]["状态"] == F_DELIVERED)

    run = client.post("/api/parser/1/run", json={"channel_id": 1}).json()
    check("释放后三行一次性解析", run["ok"] and run["entry"]["解析条数"] == 3, str(run))
    check("行号仍为原始 1/2/3", sorted(d["原始行号"] for d in run["entry"]["明细"]) == [1, 2, 3])


def scenario_guardrails() -> None:
    print("\n[场景3] 护栏：未登记格式、空文件、不存在通道")
    reset_store()
    store.rows("ingestion")[0]["输入格式"] = "未登记的格式-XYZ"

    resp = client.post(
        "/api/ingestion/imports/channels/1/files",
        json={"content": "a,b,c"},
    )
    check("未登记格式被拒绝", resp.json()["ok"] is False and "未登记" in resp.json()["message"], resp.text)

    resp = client.post(
        "/api/ingestion/imports/channels/999/files",
        json={"content": "a,b,c"},
    )
    check("不存在通道被拒绝", resp.json()["ok"] is False, resp.text)

    resp = client.post(
        "/api/ingestion/imports/channels/1/files",
        json={"content": "   "},
    )
    check("空文件被拒绝", resp.json()["ok"] is False, resp.text)

    formats = client.get("/api/ingestion/imports/formats").json()["items"]
    check("格式登记清单可查询", "CSV-货运记录" in formats, str(formats))


if __name__ == "__main__":
    scenario_available_first()
    scenario_complete_delivery()
    scenario_guardrails()
    print(f"\n结果：通过 {PASS} 项，失败 {FAIL} 项")
    sys.exit(1 if FAIL else 0)
