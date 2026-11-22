"""目录分区同步记录：回执登记、版本并存、不拼用、幂等与溯源的端到端验证。

场景对应值班窗口：先登记日键，目录装载期间再登记日键加地区，
并把日键回执重发；重开目录后按两个注册号核对分区字段与同步来源。

注意：store 是全局单例，测试用独立湖表名避免相互干扰。
"""
from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.store import store
from app.services.partition_policy import PartitionPolicyService
from app.services.lake_table import LakeTableService
from app.services.data_catalog import DataCatalogService
from app.services.catalog_partition_sync import CatalogPartitionSyncService

_table_seq = 0


def _unique_table(base: str) -> str:
    global _table_seq
    _table_seq += 1
    return f"{base}_{_table_seq}"


def _register_table_with_keys(table_name: str, keys: str) -> dict:
    """建湖表 + 建分区策略并启用 + 注册入湖，返回湖表条目。"""
    pp_svc = PartitionPolicyService()
    lake_svc = LakeTableService()
    lt, missing = lake_svc.create_entry({
        "表名": table_name, "所属业务域": "测试域", "存储格式": "iceberg",
    })
    assert not missing
    pp, missing = pp_svc.create_entry({
        "策略名称": f"{table_name}策略", "分区维度": keys, "分区粒度": "1d",
        "目标湖表": table_name, "生命周期": "30d",
    })
    assert not missing
    enabled, msg = pp_svc.enable_policy(pp["id"])
    assert enabled is not None, msg
    entry, msg = lake_svc.register_table(lt["id"])
    assert entry is not None, msg
    return entry


def _create_catalog_entry(cat_svc: DataCatalogService, table_name: str) -> dict:
    entry, missing = cat_svc.create_entry({
        "条目名称": f"{table_name}目录", "数据层级": "dwd", "所属湖表": table_name,
        "字段定义": "订单号，金额", "安全等级": "内部",
    })
    assert not missing
    return entry


class TestReceiptRegistration:
    """湖表分区键确认后生成目录分区同步记录。"""

    def setup_method(self):
        self.sync_svc = CatalogPartitionSyncService()

    def test_register_confirms_receipt(self):
        table = _unique_table("回执登记")
        lt = _register_table_with_keys(table, "日")
        records = self.sync_svc.records_for_table(table)
        assert len(records) == 1
        rec = records[0]
        assert rec["分区键"] == "日"
        assert rec["分区版本"] == "v1"
        assert rec["status"] == "待同步"
        assert rec["来源注册号"].startswith("REG-")
        assert rec["同步编号"].startswith("CPS-")
        assert rec["湖表注册ID"] == lt["id"]

    def test_receipt_event_logged(self):
        table = _unique_table("回执事件")
        _register_table_with_keys(table, "日")
        events = store.events_between("lake_table", "catalog_partition_sync")
        assert any(e["payload"].get("table_name") == table for e in events)


class TestCatalogSyncApply:
    """数据目录读取同步记录并更新可查询的分区字段。"""

    def setup_method(self):
        self.cat_svc = DataCatalogService()
        self.sync_svc = CatalogPartitionSyncService()

    def test_sync_applies_exact_version_fields(self):
        table = _unique_table("目录同步")
        _register_table_with_keys(table, "日")
        entry = _create_catalog_entry(self.cat_svc, table)
        entry, msg = self.cat_svc.sync_partition_fields(entry["id"])
        assert entry is not None, msg
        rec = self.sync_svc.records_for_table(table)[0]
        # 可查询的分区字段与字段定义来自这一版
        assert entry["分区字段"] == "日"
        assert "分区字段：日" in entry["字段定义"]
        assert "订单号" in entry["字段定义"], "非分区字段定义应保留"
        # 溯源信息：能追到实际生效的湖表注册和分区版本
        assert entry["生效注册号"] == rec["来源注册号"]
        assert entry["生效分区版本"] == "v1"
        assert entry["分区同步来源"] == rec["同步编号"]
        assert rec["status"] == "已同步"

    def test_sync_without_pending_is_idempotent(self):
        table = _unique_table("幂等同步")
        _register_table_with_keys(table, "日")
        entry = _create_catalog_entry(self.cat_svc, table)
        self.cat_svc.sync_partition_fields(entry["id"])
        again, msg = self.cat_svc.sync_partition_fields(entry["id"])
        assert again is not None
        assert "v1" in msg and again["生效分区版本"] == "v1"

    def test_sync_without_receipt_fails_clearly(self):
        table = _unique_table("无回执")
        entry = _create_catalog_entry(self.cat_svc, table)
        result, msg = self.cat_svc.sync_partition_fields(entry["id"])
        assert result is None
        assert table in msg


class TestTwoVersionsWindow:
    """注册确认后遇到新分区策略：两个版本并存、不拼用、回执重发只记一次。"""

    def setup_method(self):
        self.pp_svc = PartitionPolicyService()
        self.cat_svc = DataCatalogService()
        self.sync_svc = CatalogPartitionSyncService()

    def test_duty_window_scenario(self):
        table = _unique_table("值班窗口")
        # 先登记日键：注册确认后产生 v1 回执
        _register_table_with_keys(table, "日")
        entry = _create_catalog_entry(self.cat_svc, table)
        self.cat_svc.sync_partition_fields(entry["id"])

        # 目录装载期间（值班员正在查询目录）
        rows, total = self.cat_svc.list_entries(filters={"所属湖表": table})
        assert total == 1

        # 再登记日键加地区：新策略生效，产生 v2 回执
        pp2, _ = self.pp_svc.create_entry({
            "策略名称": f"{table}策略二", "分区维度": "日，地区", "分区粒度": "1d",
            "目标湖表": table, "生命周期": "30d",
        })
        enabled, msg = self.pp_svc.enable_policy(pp2["id"])
        assert enabled is not None, msg

        records = self.sync_svc.records_for_table(table)
        assert len(records) == 2
        v1, v2 = records
        assert (v1["分区键"], v1["分区版本"]) == ("日", "v1")
        assert (v2["分区键"], v2["分区版本"]) == ("日，地区", "v2")
        assert v1["来源注册号"] != v2["来源注册号"], "两次注册确认应各有注册号"

        # 窗口期：目录条目仍生效 v1，值班员能看到它适用于哪次注册
        current = store.find("data_catalog", entry["id"])
        assert current["生效分区版本"] == "v1"
        assert current["生效注册号"] == v1["来源注册号"]
        assert current["分区字段"] == "日"

        # 日键回执重发：只记录一次
        dup, created, msg = self.sync_svc.submit_receipt(
            table, "日",
            registration_no=v1["来源注册号"], receipt_no=v1["同步编号"],
            source_module="lake_table",
        )
        assert created is False
        assert dup["id"] == v1["id"]
        assert len(self.sync_svc.records_for_table(table)) == 2

        # 重开目录（重新进入，换服务实例）后原条目生效版本仍可回查
        reopened = DataCatalogService()
        trace = reopened.partition_field_trace(entry["id"])
        assert trace["effective"]["生效注册号"] == v1["来源注册号"]
        assert trace["effective"]["生效分区版本"] == "v1"
        assert len(trace["sync_records"]) == 2
        assert trace["registrations"], "应能追到湖表注册记录"

        # 同步 v2 后：分区字段整版替换为日，地区，来源指向第二次注册
        synced, msg = reopened.sync_partition_fields(entry["id"])
        assert synced is not None, msg
        assert synced["分区字段"] == "日，地区"
        assert synced["生效注册号"] == v2["来源注册号"]
        assert synced["生效分区版本"] == "v2"
        assert synced["分区同步来源"] == v2["同步编号"]

    def test_no_field_mixing_between_versions(self):
        table = _unique_table("不拼用")
        _register_table_with_keys(table, "日")
        entry = _create_catalog_entry(self.cat_svc, table)
        self.cat_svc.sync_partition_fields(entry["id"])
        pp2, _ = self.pp_svc.create_entry({
            "策略名称": f"{table}策略二", "分区维度": "日，地区", "分区粒度": "1d",
            "目标湖表": table, "生命周期": "30d",
        })
        self.pp_svc.enable_policy(pp2["id"])
        self.cat_svc.sync_partition_fields(entry["id"])
        assert store.find("data_catalog", entry["id"])["分区字段"] == "日，地区"

        # 回放 v1：整版替换而不是取并集，字段定义里只保留一个分区段落
        v1 = self.sync_svc.records_for_table(table)[0]
        applied, msg = self.sync_svc.apply_record_to_catalog(v1["id"])
        assert applied is not None, msg
        current = store.find("data_catalog", entry["id"])
        assert current["分区字段"] == "日"
        assert current["生效分区版本"] == "v1"
        assert current["字段定义"].count("分区字段：") == 1
        assert "地区" not in current["字段定义"].split("分区字段：")[1]


class TestReceiptIdempotency:
    """重复提交只记录一次。"""

    def setup_method(self):
        self.sync_svc = CatalogPartitionSyncService()

    def test_duplicate_receipt_by_number(self):
        table = _unique_table("重发编号")
        rec, created, _ = self.sync_svc.submit_receipt(table, "日", source_module="lake_table")
        assert created
        dup, created2, msg = self.sync_svc.submit_receipt(
            table, "日", receipt_no=rec["同步编号"], source_module="lake_table")
        assert created2 is False
        assert dup["id"] == rec["id"]
        assert len(self.sync_svc.records_for_table(table)) == 1

    def test_duplicate_receipt_by_registration(self):
        table = _unique_table("重发注册号")
        rec, created, _ = self.sync_svc.submit_receipt(
            table, "日", registration_no="REG-9001", source_module="lake_table")
        assert created
        dup, created2, _ = self.sync_svc.submit_receipt(
            table, "日", registration_no="REG-9001", source_module="lake_table")
        assert created2 is False
        assert dup["同步编号"] == rec["同步编号"]
        assert len(self.sync_svc.records_for_table(table)) == 1

    def test_create_entry_is_idempotent(self):
        table = _unique_table("登记幂等")
        values = {"所属湖表": table, "分区键": "日", "同步编号": "CPS-9001", "来源注册号": "REG-9002"}
        first, missing = self.sync_svc.create_entry(values)
        assert not missing
        second, missing = self.sync_svc.create_entry(values)
        assert not missing
        assert second["id"] == first["id"]
        assert len(self.sync_svc.records_for_table(table)) == 1

    def test_voided_record_cannot_apply(self):
        table = _unique_table("作废拦截")
        rec, _, _ = self.sync_svc.submit_receipt(table, "日", source_module="lake_table")
        self.sync_svc.run_action(rec["id"], "作废记录")
        applied, msg = self.sync_svc.apply_record_to_catalog(rec["id"])
        assert applied is None
        assert "作废" in msg
