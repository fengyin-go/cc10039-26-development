"""数据目录领域服务：维护目录条目，并消费目录分区同步记录。

目录条目通过「同步分区字段」动作读取所属湖表版本最高的待同步记录，
把该版本的分区键整体写入条目（不拼用其它版本），并记下生效的
来源注册号、分区版本和同步编号，供查字段的人回溯到实际生效的
湖表注册。刷新或重新进入后，溯源信息随条目一起返回。
"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class DataCatalogService(BaseModuleService):
    MODULE = "data_catalog"
    ENTITY = "目录条目"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["条目名称", "数据层级", "所属湖表", "字段定义", "安全等级"]
    REQUIRED_FIELDS = ["条目名称", "数据层级", "所属湖表"]
    STATUS_ORDER = ["待发布", "已发布", "已过期"]
    ACTION_RULES = {"发布条目": "已发布", "标记过期": "已过期"}
    NEGATIVE_ACTIONS = []

    def _sync_service(self):
        """延迟取目录分区同步记录服务，避免模块加载期循环依赖。"""
        from app.services import service_for

        return service_for("catalog_partition_sync")

    def sync_partition_fields(self, entry_id: int) -> tuple[dict[str, Any] | None, str]:
        """同步分区字段：应用所属湖表版本最高的待同步记录。

        没有待同步记录时保持幂等：已生效过的条目回报当前生效版本，
        便于值班员确认看到的分区字段适用于哪次注册。
        """
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return None, f"目录条目 {entry_id} 不存在或已归档"
        sync_svc = self._sync_service()
        if sync_svc is None:
            return None, "目录分区同步记录模块未启用"

        table_name = str(entry.get("所属湖表", ""))
        record = sync_svc.latest_pending_for_table(table_name)
        if record is None:
            if entry.get("生效注册号"):
                return entry, (
                    f"湖表 {table_name} 无待同步记录；当前分区字段生效 "
                    f"{entry.get('生效分区版本')}（{entry.get('生效注册号')}），"
                    f"来自 {entry.get('分区同步来源')}"
                )
            return None, f"湖表 {table_name} 暂无待同步的目录分区同步记录"

        applied, message = sync_svc.apply_record_to_catalog(int(record["id"]))
        if applied is None:
            return None, message
        return store.find(self.MODULE, entry_id), message

    def partition_field_trace(self, entry_id: int) -> dict[str, Any] | None:
        """分区字段溯源：当前生效版本 + 该湖表全部版本记录 + 湖表注册。

        值班员在注册确认与新策略并存的窗口期查询目录时，可据此确认
        看到的分区字段适用于哪次注册，并对照两个注册号各自的键。
        """
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return None
        sync_svc = self._sync_service()
        table_name = str(entry.get("所属湖表", ""))

        effective = None
        if entry.get("生效注册号"):
            effective = {
                "分区字段": entry.get("分区字段"),
                "生效注册号": entry.get("生效注册号"),
                "生效分区版本": entry.get("生效分区版本"),
                "分区同步来源": entry.get("分区同步来源"),
                "分区同步时间": entry.get("分区同步时间"),
            }

        records = sync_svc.records_for_table(table_name) if sync_svc is not None else []
        registrations = [
            {
                "湖表注册ID": lt.get("id"),
                "表名": lt.get("表名"),
                "status": lt.get("status"),
                "分区键": lt.get("分区键"),
            }
            for lt in store.find_by_field("lake_table", "表名", table_name)
        ]
        return {
            "entry_id": entry_id,
            "条目名称": entry.get("条目名称"),
            "所属湖表": table_name,
            "effective": effective,
            "sync_records": records,
            "registrations": registrations,
        }

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        if action == "同步分区字段":
            return self.sync_partition_fields(entry_id)
        return super().run_action(entry_id, action)
