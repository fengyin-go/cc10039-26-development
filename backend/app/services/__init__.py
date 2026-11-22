"""领域服务注册表：通用资源路由按模块 key 分派到独立业务服务。"""
from __future__ import annotations

from app.services.base import BaseModuleService
from app.services.partition_policy import PartitionPolicyService
from app.services.lake_table import LakeTableService
from app.services.storage_tier import StorageTierService
from app.services.file_compaction import FileCompactionService
from app.services.data_catalog import DataCatalogService
from app.services.catalog_partition_sync import CatalogPartitionSyncService
from app.services.quality_monitor import QualityMonitorService
from app.services.retention_mgr import RetentionMgrService
from app.services.ingest_pipeline import IngestPipelineService
from app.services.snapshot_version import SnapshotVersionService
from app.services.access_audit import AccessAuditService
from app.services.schema_evolution import SchemaEvolutionService
from app.services.load_balance import LoadBalanceService
from app.services.metadata_sync import MetadataSyncService
from app.services.backfill_job import BackfillJobService
from app.services.data_dictionary import DataDictionaryService
from app.services.task_scheduler import TaskSchedulerService
from app.services.alert_rule import AlertRuleService
from app.services.cost_report import CostReportService
from app.services.lineage_graph import LineageGraphService
from app.services.govern_report import GovernReportService

SERVICES: dict[str, BaseModuleService] = {
    "partition_policy": PartitionPolicyService(),
    "lake_table": LakeTableService(),
    "storage_tier": StorageTierService(),
    "file_compaction": FileCompactionService(),
    "data_catalog": DataCatalogService(),
    "catalog_partition_sync": CatalogPartitionSyncService(),
    "quality_monitor": QualityMonitorService(),
    "retention_mgr": RetentionMgrService(),
    "ingest_pipeline": IngestPipelineService(),
    "snapshot_version": SnapshotVersionService(),
    "access_audit": AccessAuditService(),
    "schema_evolution": SchemaEvolutionService(),
    "load_balance": LoadBalanceService(),
    "metadata_sync": MetadataSyncService(),
    "backfill_job": BackfillJobService(),
    "data_dictionary": DataDictionaryService(),
    "task_scheduler": TaskSchedulerService(),
    "alert_rule": AlertRuleService(),
    "cost_report": CostReportService(),
    "lineage_graph": LineageGraphService(),
    "govern_report": GovernReportService(),
}


def service_for(module: str) -> BaseModuleService | None:
    return SERVICES.get(module)
