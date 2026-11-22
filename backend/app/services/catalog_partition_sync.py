"""目录分区同步记录领域服务：承接湖表分区键确认回执，向数据目录同步分区字段。

湖表注册确认或新分区策略生效后，会把当次确认的分区键登记为一条
目录分区同步记录（回执）。数据目录按记录把某一版本的分区键整体写入
目录条目：同一条目任一时刻只生效一个版本，不拼用两份字段；
同一张表的两个版本各自留有记录，可按来源注册号回查；
重复提交的回执只记录一次。
"""
from __future__ import annotations

import datetime
import re
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


def _normalize_keys(partition_keys: Any) -> list[str]:
    """把分区键拆成有序去重的键列表，支持中文逗号、英文逗号、顿号、斜杠分隔。"""
    if isinstance(partition_keys, (list, tuple)):
        parts = [str(item) for item in partition_keys]
    else:
        parts = re.split(r"[,，、/]", str(partition_keys or ""))
    keys: list[str] = []
    for part in parts:
        key = part.strip()
        if key and key not in keys:
            keys.append(key)
    return keys


def _canonical_keys(partition_keys: Any) -> str:
    """分区键的规范文本形式，用于存储与比对。"""
    return "，".join(_normalize_keys(partition_keys))


def _version_number(version: Any) -> int:
    """从 v2 / 2 这类写法里取出版本序号，取不到按 0 计。"""
    match = re.search(r"\d+", str(version or ""))
    return int(match.group(0)) if match else 0


class CatalogPartitionSyncService(BaseModuleService):
    MODULE = "catalog_partition_sync"
    ENTITY = "目录分区同步记录"
    OPERATION_TERM = "同步"
    LIST_FIELDS = ["同步编号", "所属湖表", "分区键", "分区版本", "来源注册号"]
    REQUIRED_FIELDS = ["所属湖表", "分区键"]
    STATUS_ORDER = ["待同步", "已同步", "已作废"]
    ACTION_RULES = {"同步到目录": "已同步", "作废记录": "已作废"}
    NEGATIVE_ACTIONS = ["作废记录"]

    # ---- 回执登记（幂等） ----

    def records_for_table(self, table_name: str) -> list[dict[str, Any]]:
        """按分区版本升序返回某张湖表的全部同步记录。"""
        rows = [
            row for row in store.rows(self.MODULE)
            if str(row.get("所属湖表", "")).casefold() == table_name.casefold()
        ]
        return sorted(rows, key=lambda row: _version_number(row.get("分区版本")))

    def _next_receipt_seq(self) -> int:
        """全局回执序号：扫描现有同步编号与来源注册号取最大后加一。"""
        seq = 0
        for row in store.rows(self.MODULE):
            for field in ("同步编号", "来源注册号"):
                seq = max(seq, _version_number(row.get(field)))
        return seq + 1

    def find_receipt(
        self,
        *,
        receipt_no: str | None = None,
        table_name: str | None = None,
        registration_no: str | None = None,
    ) -> dict[str, Any] | None:
        """按同步编号或（所属湖表 + 来源注册号）定位已登记的回执。"""
        for row in store.rows(self.MODULE):
            if receipt_no and str(row.get("同步编号", "")).casefold() == receipt_no.casefold():
                return row
            if (
                table_name
                and registration_no
                and str(row.get("所属湖表", "")).casefold() == table_name.casefold()
                and str(row.get("来源注册号", "")).casefold() == registration_no.casefold()
            ):
                return row
        return None

    def submit_receipt(
        self,
        table_name: str,
        partition_keys: Any,
        *,
        registration_no: str | None = None,
        receipt_no: str | None = None,
        source_module: str = "lake_table",
        source_note: str = "",
        lake_table_id: int | None = None,
    ) -> tuple[dict[str, Any] | None, bool, str]:
        """登记一条分区同步回执。

        返回 (记录, 是否新建, 消息)。同一张回执（同步编号相同，或同一
        湖表的同一来源注册号）重复提交时只保留首次记录，返回已有记录。
        """
        table_name = str(table_name or "").strip()
        canonical = _canonical_keys(partition_keys)
        if not table_name or not canonical:
            return None, False, "所属湖表或分区键为空，无法生成目录分区同步记录"

        duplicate = self.find_receipt(
            receipt_no=receipt_no,
            table_name=table_name,
            registration_no=registration_no,
        )
        if duplicate is None and not receipt_no and not registration_no:
            # 自动确认通道（未带编号）：与最新有效记录键相同视为同一次确认的重发
            existing = self.records_for_table(table_name)
            if existing:
                latest = existing[-1]
                if (
                    latest.get("status") != "已作废"
                    and _canonical_keys(latest.get("分区键")) == canonical
                ):
                    duplicate = latest
        if duplicate is not None:
            store.log_event("catalog_sync_receipt_duplicate", source_module, self.MODULE, {
                "receipt_no": duplicate.get("同步编号"),
                "table_name": table_name,
                "registration_no": duplicate.get("来源注册号"),
                "message": "重复提交的回执已忽略，仅保留首次记录",
            })
            return duplicate, False, (
                f"回执 {duplicate.get('同步编号')} 已登记过，重复提交只记录一次"
            )

        seq = self._next_receipt_seq()
        receipt_no = receipt_no or f"CPS-{seq:04d}"
        registration_no = registration_no or f"REG-{seq:04d}"
        version = f"v{len(self.records_for_table(table_name)) + 1}"
        rows = store.rows(self.MODULE)
        record = {
            "id": max((int(row.get("id", 0)) for row in rows), default=0) + 1,
            "同步编号": receipt_no,
            "所属湖表": table_name,
            "分区键": canonical,
            "分区版本": version,
            "来源注册号": registration_no,
            "status": self.STATUS_ORDER[0],
            "pending": True,
            "abnormal": False,
            "分区键列表": _normalize_keys(partition_keys),
            "来源模块": source_module,
            "来源说明": source_note,
            "湖表注册ID": lake_table_id,
            "登记时间": datetime.datetime.now().isoformat(timespec="seconds"),
        }
        rows.append(record)
        store.log_event("catalog_sync_receipt", source_module, self.MODULE, {
            "receipt_no": receipt_no,
            "table_name": table_name,
            "partition_keys": canonical,
            "partition_version": version,
            "registration_no": registration_no,
            "lake_table_id": lake_table_id,
        })
        return record, True, (
            f"回执 {receipt_no} 已登记：{table_name} 分区键 {canonical} "
            f"（{version} / {registration_no}）"
        )

    # ---- 版本查询 ----

    def latest_pending_for_table(self, table_name: str) -> dict[str, Any] | None:
        """某张湖表版本最高的待同步记录。"""
        pending = [
            row for row in self.records_for_table(table_name)
            if row.get("status") == "待同步"
        ]
        return pending[-1] if pending else None

    # ---- 同步到数据目录 ----

    def apply_record_to_catalog(self, sync_id: int) -> tuple[dict[str, Any] | None, str]:
        """把一条同步记录的分区键整体写入该湖表的全部目录条目。

        目录条目的分区字段来自且仅来自这一条记录的版本，不与其它版本拼用；
        已同步的记录允许再次应用（整版替换是确定性的，可用于回放到旧版本）。
        """
        record = store.find(self.MODULE, sync_id)
        if record is None:
            return None, f"目录分区同步记录 {sync_id} 不存在"
        if record.get("status") == "已作废":
            return None, f"同步记录 {record.get('同步编号')} 已作废，不可同步"
        # 已同步的记录允许再次应用：整版替换是确定性的，可用于回放到旧版本
        reapply = record.get("status") == "已同步"

        table_name = str(record.get("所属湖表", ""))
        canonical = _canonical_keys(record.get("分区键"))
        keys = _normalize_keys(record.get("分区键"))
        synced_at = datetime.datetime.now().isoformat(timespec="seconds")

        entries = store.find_by_field("data_catalog", "所属湖表", table_name)
        if not entries:
            return None, f"湖表 {table_name} 暂无目录条目，同步记录保持待同步"

        for entry in entries:
            # 字段定义只保留一个分区字段段落：先剔除旧段落，再写入本次版本的键
            base = re.sub(r"分区字段：[^；;]*[；;]?", "", str(entry.get("字段定义") or ""))
            base = base.strip("；; ")
            definition = f"分区字段：{canonical}"
            entry["字段定义"] = f"{base}；{definition}" if base else definition
            # 可查询的分区字段与溯源信息：整版替换，不拼用其它版本
            entry["分区字段"] = canonical
            entry["分区字段列表"] = keys
            entry["生效注册号"] = record.get("来源注册号")
            entry["生效分区版本"] = record.get("分区版本")
            entry["分区同步来源"] = record.get("同步编号")
            entry["分区同步时间"] = synced_at

        record["status"] = "已同步"
        record["pending"] = False
        record["同步时间"] = synced_at
        store.log_event("catalog_partition_synced", self.MODULE, "data_catalog", {
            "receipt_no": record.get("同步编号"),
            "table_name": table_name,
            "partition_keys": canonical,
            "partition_version": record.get("分区版本"),
            "registration_no": record.get("来源注册号"),
            "synced_entries": len(entries),
            "reapplied": reapply,
        })
        verb = "重新应用" if reapply else "已写入"
        return record, (
            f"同步记录 {record.get('同步编号')} {verb} {len(entries)} 条目录条目，"
            f"生效 {record.get('分区版本')}（{record.get('来源注册号')}）：{canonical}"
        )

    # ---- 通用入口 ----

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        """登记走回执通道：自动补同步编号/来源注册号/分区版本，重复提交幂等。"""
        missing = [
            field for field in self.REQUIRED_FIELDS
            if not str(values.get(field) or "").strip()
        ]
        if missing:
            return None, missing
        record, _created, _msg = self.submit_receipt(
            str(values.get("所属湖表") or ""),
            values.get("分区键"),
            registration_no=str(values.get("来源注册号") or "").strip() or None,
            receipt_no=str(values.get("同步编号") or "").strip() or None,
            source_module=self.MODULE,
            source_note="目录同步台手工登记",
        )
        return record, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        if action == "同步到目录":
            return self.apply_record_to_catalog(entry_id)
        return super().run_action(entry_id, action)
