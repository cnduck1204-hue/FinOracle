import { useState } from 'react'
import { LoaderCircle } from 'lucide-react'
import { useAuth } from '../auth/AuthProvider'
import { Tag } from './IngestionUi'
import {
  PIPELINE_STATUS_LABELS,
  pipelineBuild,
  pipelineStatusTone,
  showsPipelines,
} from '../lib/ingestion'
import type { DocumentVersion } from '../lib/types'
import { useResource } from '../lib/useResource'

/** 版本在各检索方案下的索引状态，并可建立附加方案的索引。 */
export function VersionPipelines({
  version,
  onChanged,
  onError,
}: {
  version: DocumentVersion
  onChanged: (message: string) => void
  onError: (error: unknown) => void
}) {
  const { client } = useAuth()
  const job = version.latest_job
  // 版本列表在任务进行中会轮询，任务状态一变这里随之刷新
  const { data } = useResource(
    () => client.listVersionPipelines(version.id),
    [client, version.id, version.status, job?.id, job?.status],
  )
  const [building, setBuilding] = useState<string | null>(null)

  async function build(name: string, label: string) {
    setBuilding(name)
    try {
      await client.buildVersionPipeline(version.id, name)
      onChanged(`已开始建立“${label}”的索引。`)
    } catch (caught) {
      onError(caught)
    } finally {
      setBuilding(null)
    }
  }

  if (!showsPipelines(version) || !data) return null
  return (
    <div className="version-pipelines">
      <span className="version-pipelines-title">检索方案</span>
      <ul>
        {data.map((item) => {
          const { pipeline, status } = item
          const action = pipelineBuild(item, version)
          const failure =
            status === 'failed' && job?.kind === 'pipeline' && job.ingest === pipeline.ingest
              ? job.error_message
              : null
          return (
            <li key={pipeline.name}>
              <div>
                <strong>{pipeline.label}</strong>
                <Tag tone={pipelineStatusTone(status)}>{PIPELINE_STATUS_LABELS[status]}</Tag>
                {status === 'ready' && <small>{item.unit_count} 个切片</small>}
              </div>
              <div className="version-pipelines-action">
                {(failure || action.note) && <small>{failure || action.note}</small>}
                {action.label && (
                  <button
                    type="button"
                    className="text-button"
                    disabled={building !== null}
                    onClick={() => void build(pipeline.name, pipeline.label)}
                  >
                    {building === pipeline.name && <LoaderCircle size={12} className="spinner" />}
                    {action.label}
                  </button>
                )}
              </div>
            </li>
          )
        })}
      </ul>
    </div>
  )
}
