"""上传文件接入接口：把文件交给格式解析入口，形成带文件标识与行号的接入原始事件。

路由挂在 /api/ingestion/imports 下，和通道登记接口（/api/ingestion）分开，
避免 /{entry_id} 与子路径互相抢匹配。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.schemas import ActionResult
from app.services.ingestion_import import (
    POLICY_AVAILABLE,
    ingestion_import_service,
)
from app.services.parser import ParserService

router = APIRouter(prefix="/api/ingestion/imports", tags=["数据接入-文件导入"])

parser_service = ParserService()


class ImportPayload(BaseModel):
    """上传文件内容；平台演示态走 JSON 文本，不依赖 multipart 上传组件。"""

    filename: str | None = None
    content: str
    delivery_policy: str = POLICY_AVAILABLE
    remark: str | None = None


class ReimportPayload(BaseModel):
    filename: str | None = None
    content: str
    line_offset: int = Field(default=1, ge=1, description="修正内容首行对应的原始行号")
    remark: str | None = None


class ParsePayload(BaseModel):
    parser_id: int
    file_id: int | None = None


@router.get("/formats")
def list_formats() -> dict[str, Any]:
    """列出当前登记可接入的输入格式，供上传时选择与通道声明核对。"""
    return {"items": ingestion_import_service.supported_formats()}


@router.get("/files")
def list_files(channel_id: int | None = None) -> dict[str, Any]:
    """接入文件清单，可按通道过滤；每条带坏记录数与交付状态。"""
    items = ingestion_import_service.list_files(channel_id)
    return {"items": items, "total": len(items)}


@router.get("/files/{file_id}")
def get_file(file_id: int) -> dict[str, Any]:
    file_row = ingestion_import_service.get_file(file_id)
    if file_row is None:
        raise HTTPException(status_code=404, detail=f"接入文件 {file_id} 不存在")
    return file_row


@router.get("/files/{file_id}/events")
def list_file_events(file_id: int, status: str | None = None) -> dict[str, Any]:
    """文件级原始事件：每行一条，含文件标识、原始行号、原文与当前状态。"""
    items = ingestion_import_service.list_events(file_id=file_id, status=status)
    return {"items": items, "total": len(items)}


@router.get("/files/{file_id}/dead-letters")
def list_dead_letters(file_id: int) -> dict[str, Any]:
    """坏记录追查清单：直接给出文件标识、行号、原文和失败原因。"""
    items = ingestion_import_service.dead_letters(file_id)
    return {"items": items, "total": len(items)}


@router.get("/events")
def list_events(channel_id: int | None = None, status: str | None = None) -> dict[str, Any]:
    """跨文件查看通道的接入原始事件数据集。"""
    items = ingestion_import_service.list_events(channel_id=channel_id, status=status)
    return {"items": items, "total": len(items)}


@router.post("/channels/{channel_id}/files", response_model=ActionResult)
def import_file(channel_id: int, payload: ImportPayload) -> ActionResult:
    """按通道声明的输入格式导入上传文件，形成接入原始事件数据集。"""
    if not payload.content.strip():
        return ActionResult(ok=False, message="上传文件内容为空，无法形成接入事件")
    file_row, message = ingestion_import_service.import_file(
        channel_id,
        filename=payload.filename or "",
        content=payload.content,
        delivery_policy=payload.delivery_policy,
        remark=payload.remark,
    )
    if file_row is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=file_row)


@router.post("/files/{file_id}/reimport", response_model=ActionResult)
def reimport_file(file_id: int, payload: ReimportPayload) -> ActionResult:
    """重投修正文件：已确认行号自动跳过，修正行沿用原行号定位且不重复交付。"""
    if not payload.content.strip():
        return ActionResult(ok=False, message="修正文件内容为空，无法重投")
    file_row, message = ingestion_import_service.reimport_file(
        file_id,
        filename=payload.filename or "",
        content=payload.content,
        line_offset=payload.line_offset,
        remark=payload.remark,
    )
    if file_row is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=file_row)


@router.post("/channels/{channel_id}/parse", response_model=ActionResult)
def parse_events(channel_id: int, payload: ParsePayload) -> ActionResult:
    """让格式解析器从接入原始事件数据集读取获准事件，产出带文件与行号的解析明细。"""
    result, message = parser_service.run_parse(
        payload.parser_id,
        channel_id=channel_id,
        file_id=payload.file_id,
    )
    if result is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=result)


@router.get("/parse-results")
def list_parse_results(
    channel_id: int | None = None,
    file_id: int | None = None,
    batch: str | None = None,
) -> dict[str, Any]:
    """解析明细查询：每条都关联接入文件标识与原始行号，便于按原位置核对。"""
    items = parser_service.list_results(channel_id=channel_id, file_id=file_id, batch=batch)
    return {"items": items, "total": len(items)}
