"""跨模块编排端点：暴露跨模块数据流链路、事件日志和快照信息。

提供 /api/cross/events, /api/cross/snapshots, /api/cross/pipeline-status 等端点，
让前端和治理报告可以消费跨模块链路数据。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.services import service_for, SERVICES
from app.store import store
from app.services.partition_policy import PartitionPolicyService
from app.services.lake_table import LakeTableService
from app.services.storage_tier import StorageTierService
from app.services.file_compaction import FileCompactionService
from app.services.retention_mgr import RetentionMgrService
from app.services.backfill_job import BackfillJobService
from app.services.govern_report import GovernReportService

router = APIRouter(prefix="/api/cross", tags=["跨模块编排"])


@router.get("/events")
def list_cross_events(
    source: str | None = Query(default=None),
    target: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
) -> dict[str, Any]:
    """查询跨模块事件日志，支持按 source/target 筛选。"""
    if source and target:
        events = store.events_between(source, target)
    elif source or target:
        events = store.events_for_module(source or target or "")
    else:
        events = list(store._events)
    return {
        "total": len(events),
        "items": events[-limit:],
    }


@router.get("/snapshots")
def list_snapshots() -> dict[str, Any]:
    """查询所有模块快照记录。"""
    return {
        "total": len(store._snapshots),
        "keys": store.snapshot_keys(),
    }


@router.get("/overview")
def cross_module_overview() -> dict[str, Any]:
    """跨模块统计概览。"""
    return store.cross_module_stats()


@router.get("/catalog-partition-trace/{entry_id}")
def catalog_partition_trace(entry_id: int) -> dict[str, Any]:
    """目录条目分区字段溯源。

    返回目录条目当前生效的分区版本（来源注册号、分区版本、同步编号）、
    所属湖表的全部目录分区同步记录（两个版本并存时可对照两个注册号
    各自的键），以及对应的湖表注册记录。
    """
    service = service_for("data_catalog")
    trace = getattr(service, "partition_field_trace", None)
    if service is None or trace is None:
        raise HTTPException(status_code=404, detail="数据目录模块未启用")
    result = trace(entry_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"目录条目 {entry_id} 不存在或已归档")
    return result


@router.get("/pipeline-status")
def pipeline_status() -> dict[str, Any]:
    """汇总数据湖治理全链路状态。
    从分区策略 → 湖表注册 → 冷热分层 → 小文件合并 → 保留策略 → 治理报告。
    """
    # 分区策略概览
    policies = store.rows("partition_policy")
    active_policies = [p for p in policies if p.get("status") == "已生效"]

    # 湖表注册概览
    lake_tables = store.rows("lake_table")
    registered_tables = [lt for lt in lake_tables if lt.get("status") == "已注册"]

    # 冷热分层概览
    tiers = store.rows("storage_tier")
    active_tiers = [t for t in tiers if t.get("status") == "已生效"]

    # 小文件合并概览
    compactions = store.rows("file_compaction")
    pending_compactions = [c for c in compactions if c.get("status") == "待调度"]
    running_compactions = [c for c in compactions if c.get("status") == "执行中"]
    completed_compactions = [c for c in compactions if c.get("status") == "已完成"]

    # 保留策略概览
    retentions = store.rows("retention_mgr")
    active_retentions = [r for r in retentions if r.get("status") == "已生效"]

    # 回填任务概览
    backfills = store.rows("backfill_job")
    active_backfills = [b for b in backfills if b.get("status") in ("待调度", "回填中")]

    # 血缘链路概览
    lineages = store.rows("lineage_graph")
    broken_lineages = [l for l in lineages if l.get("status") == "链路断裂"]

    # 治理报告概览
    reports = store.rows("govern_report")

    # 跨模块事件
    cross_stats = store.cross_module_stats()

    return {
        "pipeline": {
            "partition_policies": {"total": len(policies), "active": len(active_policies)},
            "lake_tables": {"total": len(lake_tables), "registered": len(registered_tables)},
            "storage_tiers": {"total": len(tiers), "active": len(active_tiers)},
            "file_compactions": {
                "total": len(compactions),
                "pending": len(pending_compactions),
                "running": len(running_compactions),
                "completed": len(completed_compactions),
            },
            "retention_mgrs": {"total": len(retentions), "active": len(active_retentions)},
            "backfill_jobs": {"total": len(backfills), "active": len(active_backfills)},
            "lineage_graphs": {"total": len(lineages), "broken": len(broken_lineages)},
            "govern_reports": {"total": len(reports)},
        },
        "cross_module": cross_stats,
        "health": {
            "has_broken_lineages": len(broken_lineages) > 0,
            "has_active_backfills": len(active_backfills) > 0,
            "all_lake_tables_registered": len(registered_tables) == len(lake_tables),
        },
    }