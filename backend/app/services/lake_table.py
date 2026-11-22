"""湖表注册领域服务：管理数据湖表的注册、下线及存储格式。

湖表是数据湖治理的核心对象，上游接收分区策略的分区键分配，
下游向冷热分层、合并任务和保留策略提供目标表引用。
"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class LakeTableService(BaseModuleService):
    MODULE = "lake_table"
    ENTITY = "湖表"
    OPERATION_TERM = "入湖"
    LIST_FIELDS = ["表名", "所属业务域", "存储格式", "分区键", "当前分片数"]
    REQUIRED_FIELDS = ["表名", "所属业务域", "存储格式"]
    STATUS_ORDER = ["创建中", "已注册", "已下线"]
    ACTION_RULES = {"注册入湖": "已注册", "下线湖表": "已下线"}
    NEGATIVE_ACTIONS = ["下线湖表"]

    def register_table(self, entry_id: int) -> tuple[dict[str, Any] | None, str]:
        """注册湖表入湖：触发冷热分层和保留策略的初始化检查。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return None, "湖表不存在"
        if entry.get("status") != "创建中":
            return None, f"湖表状态为{entry.get('status')}，不可注册"

        table_name = entry.get("表名", "")

        # 1) 检查是否已有分区策略分配了分区键
        policies = store.find_by_field("partition_policy", "目标湖表", table_name)
        active_policies = [p for p in policies if p.get("status") == "已生效"]
        if active_policies:
            # 从生效策略中读取分区键
            for pol in active_policies:
                pk = pol.get("分区维度", "")
                if pk:
                    entry["分区键"] = pk
                    break
            store.log_event("lake_table_from_policy", "lake_table", "partition_policy", {
                "table_id": entry_id,
                "table_name": table_name,
                "active_policy_count": len(active_policies),
            })

        # 2) 自身状态流转
        entry["status"] = "已注册"
        entry["pending"] = False
        entry["abnormal"] = False

        # 3) 通知冷热分层模块
        tiers = store.find_by_field("storage_tier", "生效湖表", table_name)
        if not tiers:
            store.log_event("lake_table_needs_tier", "lake_table", "storage_tier", {
                "table_id": entry_id,
                "table_name": table_name,
                "message": "湖表已注册但缺少冷热分层策略",
            })

        # 4) 分区键已确认：向数据目录登记分区同步回执（重复提交只记录一次）
        if str(entry.get("分区键") or "").strip():
            from app.services import service_for

            sync_svc = service_for("catalog_partition_sync")
            if sync_svc is not None:
                sync_svc.submit_receipt(
                    table_name,
                    entry["分区键"],
                    source_module="lake_table",
                    source_note=f"湖表注册确认（湖表 {entry_id}）",
                    lake_table_id=entry_id,
                )

        store.log_event("lake_table_registered", "lake_table", "lake_table", {
            "table_id": entry_id,
            "table_name": table_name,
        })

        return entry, f"湖表 {table_name} 已注册入湖"

    def offline_table(self, entry_id: int) -> tuple[dict[str, Any] | None, str]:
        """下线湖表：逐模块检查依赖后方可下线。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return None, "湖表不存在"
        if entry.get("status") != "已注册":
            return None, f"湖表状态为{entry.get('status')}，不可下线"

        table_name = entry.get("表名", "")
        blockers: list[str] = []

        # 检查各模块的活跃依赖
        compactions = store.find_by_field("file_compaction", "目标湖表", table_name)
        if any(c.get("status") in ("待调度", "执行中") for c in compactions):
            blockers.append("小文件合并")

        retentions = store.find_by_field("retention_mgr", "适用湖表", table_name)
        if any(r.get("status") == "已生效" for r in retentions):
            blockers.append("保留策略")

        tiers = store.find_by_field("storage_tier", "生效湖表", table_name)
        if any(t.get("status") == "已生效" for t in tiers):
            blockers.append("冷热分层")

        pipelines = store.find_by_field("ingest_pipeline", "目标湖表", table_name)
        if any(p.get("status") in ("待启动", "运行中") for p in pipelines):
            blockers.append("入湖管道")

        if blockers:
            return None, f"湖表 {table_name} 仍有活跃依赖：{'、'.join(blockers)}"

        entry["status"] = "已下线"
        entry["pending"] = False
        entry["abnormal"] = True
        store.log_event("lake_table_offline", "lake_table", "lake_table", {
            "table_id": entry_id,
            "table_name": table_name,
        })
        return entry, f"湖表 {table_name} 已下线"

    def tables_with_storage_format(self, fmt: str) -> list[dict[str, Any]]:
        """查询使用指定存储格式的湖表列表。"""
        return store.find_by_field(self.MODULE, "存储格式", fmt)

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        if action == "注册入湖":
            return self.register_table(entry_id)
        if action == "下线湖表":
            return self.offline_table(entry_id)
        return super().run_action(entry_id, action)