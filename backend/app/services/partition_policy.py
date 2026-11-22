"""分区策略领域服务：管理数据湖表的动态分区策略及生命周期。

分区策略 → 湖表注册 → 冷热分层 → 小文件合并 → 保留策略
构成数据湖存储治理的主链路。策略生效时会联动下游模块。
"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class PartitionPolicyService(BaseModuleService):
    MODULE = "partition_policy"
    ENTITY = "分区策略"
    OPERATION_TERM = "归档"
    LIST_FIELDS = ["策略名称", "分区维度", "分区粒度", "目标湖表", "生命周期"]
    REQUIRED_FIELDS = ["策略名称", "分区维度", "分区粒度"]
    STATUS_ORDER = ["待审批", "已生效", "已归档"]
    ACTION_RULES = {"启用策略": "已生效", "归档策略": "已归档"}
    NEGATIVE_ACTIONS = []

    def enable_policy(self, entry_id: int) -> tuple[dict[str, Any] | None, str]:
        """启用分区策略：联动湖表注册分配分区键，并触发冷热分层检查。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return None, "分区策略不存在"
        if entry.get("status") != "待审批":
            return None, f"分区策略状态为{entry.get('status')}，不可启用"

        table_name = entry.get("目标湖表", "")
        partition_key = entry.get("分区维度", "")

        # 1) 联动湖表注册：将分区键写入对应湖表
        lake_tables = store.find_by_field("lake_table", "表名", table_name)
        if lake_tables:
            reconfirmed: list[dict[str, Any]] = []
            for lt in lake_tables:
                old_key = str(lt.get("分区键") or "").strip()
                lt["分区键"] = partition_key
                lt["pending"] = True
                # 已注册的湖表换键等于一次新的分区确认，要登记新的同步回执
                if lt.get("status") == "已注册" and old_key != str(partition_key).strip():
                    reconfirmed.append(lt)
            store.log_event("partition_to_lake", "partition_policy", "lake_table", {
                "policy_id": entry_id,
                "table_name": table_name,
                "partition_key": partition_key,
            })
            if reconfirmed:
                from app.services import service_for

                sync_svc = service_for("catalog_partition_sync")
                if sync_svc is not None:
                    sync_svc.submit_receipt(
                        table_name,
                        partition_key,
                        source_module="partition_policy",
                        source_note=f"分区策略 {entry_id} 生效后重新确认",
                        lake_table_id=reconfirmed[0].get("id"),
                    )

        # 2) 检查冷热分层策略是否需要更新
        tier_entries = store.find_by_field("storage_tier", "生效湖表", table_name)
        if tier_entries:
            store.log_event("partition_to_tier", "partition_policy", "storage_tier", {
                "policy_id": entry_id,
                "table_name": table_name,
                "affected_tiers": len(tier_entries),
            })

        # 3) 自身状态流转
        entry["status"] = "已生效"
        entry["pending"] = False
        entry["abnormal"] = False

        # 4) 创建分区周期快照记录
        snapshot_key = store.create_snapshot(self.MODULE)
        return entry, f"分区策略 {entry.get('策略名称')} 已生效，快照保存在 {snapshot_key}"

    def archive_policy(self, entry_id: int) -> tuple[dict[str, Any] | None, str]:
        """归档分区策略：检查是否有活跃依赖后才归档。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return None, "分区策略不存在"
        if entry.get("status") != "已生效":
            return None, f"分区策略状态为{entry.get('status')}，不可归档"

        table_name = entry.get("目标湖表", "")

        # 检查是否有未完成的合并任务
        compactions = store.find_by_field("file_compaction", "目标湖表", table_name)
        active_compactions = [c for c in compactions if c.get("status") not in ("已完成", "执行失败")]
        if active_compactions:
            return None, f"湖表 {table_name} 仍有 {len(active_compactions)} 个活跃合并任务，不可归档"

        # 检查保留策略是否仍在生效
        retentions = store.find_by_field("retention_mgr", "适用湖表", table_name)
        active_retentions = [r for r in retentions if r.get("status") == "已生效"]
        if active_retentions:
            return None, f"湖表 {table_name} 仍有 {len(active_retentions)} 个活跃保留策略，不可归档"

        entry["status"] = "已归档"
        entry["pending"] = False
        entry["abnormal"] = False
        return entry, f"分区策略 {entry.get('策略名称')} 已归档"

    # ---- 生命周期管理 ----

    def lifecycle_report(self, entry_id: int) -> dict[str, Any] | None:
        """生成分区策略的完整生命周期报告，汇总下游依赖状态。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return None

        table_name = entry.get("目标湖表", "")
        # 汇总下游模块状态
        lake_tables = store.find_by_field("lake_table", "表名", table_name)
        tiers = store.find_by_field("storage_tier", "生效湖表", table_name)
        compactions = store.find_by_field("file_compaction", "目标湖表", table_name)
        retentions = store.find_by_field("retention_mgr", "适用湖表", table_name)
        cross_events = store.events_for_module(self.MODULE)

        return {
            "policy": entry,
            "linked_lake_tables": len(lake_tables),
            "linked_storage_tiers": len(tiers),
            "linked_compactions": len(compactions),
            "linked_retentions": len(retentions),
            "cross_module_events": len(cross_events),
            "downstream_health": {
                "has_errors": any(
                    t.get("abnormal") for t in tiers + compactions + retentions
                ),
                "lake_table_ready": any(t.get("status") == "已注册" for t in lake_tables),
            },
        }

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        """覆盖基类方法，加入业务逻辑分支。"""
        if action == "启用策略":
            return self.enable_policy(entry_id)
        if action == "归档策略":
            return self.archive_policy(entry_id)
        return super().run_action(entry_id, action)