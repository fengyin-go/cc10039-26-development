<template>
  <div class="app-shell">
    <aside class="app-side">
      <h1 class="app-title">数据湖分区与存储治理中心</h1>
      <nav class="nav-list">
        <RouterLink v-for="item in navItems" :key="item.path" :to="item.path" class="nav-item">
          {{ item.label }}
        </RouterLink>
      </nav>
    </aside>
    <main class="app-main">
      <header class="app-head">
        <span class="head-desc">管理数据湖分区策略、存储优化、冷热分层和小文件合并的数据湖治理平台</span>
        <span class="head-user">当前值班：数据工程组</span>
      </header>
      <RouterView />
    </main>
  </div>
</template>

<script setup lang="ts">
const navItems = [{ label: "运营概览", path: "/" }, { label: "分区策略", path: "/partition_policy" }, { label: "湖表注册", path: "/lake_table" }, { label: "冷热分层", path: "/storage_tier" }, { label: "小文件合并", path: "/file_compaction" }, { label: "数据目录", path: "/data_catalog" }, { label: "目录分区同步", path: "/catalog_partition_sync" }, { label: "湖内质量监控", path: "/quality_monitor" }, { label: "保留策略", path: "/retention_mgr" }, { label: "入湖管道", path: "/ingest_pipeline" }, { label: "快照版本", path: "/snapshot_version" }, { label: "访问审计", path: "/access_audit" }, { label: "Schema演进", path: "/schema_evolution" }, { label: "负载均衡配置", path: "/load_balance" }, { label: "元数据同步", path: "/metadata_sync" }, { label: "数据回填任务", path: "/backfill_job" }, { label: "数据字典管理", path: "/data_dictionary" }, { label: "任务调度", path: "/task_scheduler" }, { label: "存储告警规则", path: "/alert_rule" }, { label: "存储成本分析", path: "/cost_report" }, { label: "湖表血缘", path: "/lineage_graph" }, { label: "治理报告", path: "/govern_report" }]
</script>
