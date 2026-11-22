/** 纯前端数据层的公共类型：与全栈版后端返回的结构保持一致，换回后端时页面不用改。 */

export type EntryRow = {
  id: number
  status: string
  pending: boolean
  abnormal: boolean
  [field: string]: string | number | boolean
}

export type ModuleMeta = {
  key: string
  name: string
  entity: string
  desc: string
  fields: string[]
  statuses: string[]
  actions: string[]
  actionTargets: Record<string, string>
  metrics: string[]
}

export type PageResult = {
  items: EntryRow[]
  total: number
  page: number
  size: number
}

export type ActionResult = {
  ok: boolean
  message: string
}

export type OverviewResult = {
  cards: { label: string; value: number }[]
  modules: { name: string; created: number; pending: number; abnormal: number }[]
}

/** 目录条目分区字段溯源：当前生效版本 + 该湖表全部同步记录 + 湖表注册。 */
export type CatalogPartitionTrace = {
  entry_id: number
  条目名称: string
  所属湖表: string
  effective: {
    分区字段?: string
    生效注册号?: string
    生效分区版本?: string
    分区同步来源?: string
    分区同步时间?: string
  } | null
  sync_records: EntryRow[]
  registrations: EntryRow[]
}
