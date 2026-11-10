import { CHUNKER_OPTIONS, OCR_OPTIONS, VALIDITY_LABELS, type MetadataForm } from '../lib/ingestion'
import type { ParseOptions, ValidityStatus } from '../lib/types'

const VALIDITY_VALUES = Object.keys(VALIDITY_LABELS) as ValidityStatus[]

export function Field({
  id,
  label,
  hint,
  value,
  onChange,
  type = 'text',
  required,
  maxLength = 255,
  placeholder,
}: {
  id: string
  label: string
  hint?: string
  value: string
  onChange: (value: string) => void
  type?: string
  required?: boolean
  maxLength?: number
  placeholder?: string
}) {
  return (
    <div className="ingest-field">
      <label htmlFor={id}>
        {label}
        {required && <em> 必填</em>}
      </label>
      <input
        id={id}
        type={type}
        value={value}
        required={required}
        maxLength={type === 'date' || type === 'number' ? undefined : maxLength}
        placeholder={placeholder}
        onChange={(event) => onChange(event.target.value)}
      />
      {hint && <small>{hint}</small>}
    </div>
  )
}

export function MetadataFields({
  idPrefix,
  value,
  onChange,
}: {
  idPrefix: string
  value: MetadataForm
  onChange: (value: MetadataForm) => void
}) {
  const bind = (key: keyof MetadataForm) => ({
    id: `${idPrefix}-${key}`,
    value: value[key],
    onChange: (next: string) => onChange({ ...value, [key]: next }),
  })
  return (
    <div className="ingest-grid">
      <Field {...bind('document_number')} label="文号" />
      <Field {...bind('version_note')} label="版本说明" placeholder="如：2023年第二次修订" />
      <Field {...bind('published_date')} label="发布日期" type="date" />
      <Field {...bind('effective_date')} label="生效日期" type="date" />
      <div className="ingest-field">
        <label htmlFor={`${idPrefix}-validity_status`}>时效状态</label>
        <select
          id={`${idPrefix}-validity_status`}
          value={value.validity_status}
          onChange={(event) => onChange({ ...value, validity_status: event.target.value })}
        >
          {VALIDITY_VALUES.map((status) => (
            <option key={status} value={status}>
              {VALIDITY_LABELS[status]}
            </option>
          ))}
        </select>
        <small>未核实时保持“时效待核验”，不会显示为现行有效。</small>
      </div>
      <Field {...bind('validity_checked_at')} label="时效核查日期" type="date" />
      <Field {...bind('source_url')} label="发布依据网址" type="url" maxLength={2048} />
      <Field {...bind('download_url')} label="下载网址" type="url" maxLength={2048} />
      <Field
        {...bind('expected_articles')}
        label="应有条数"
        type="number"
        hint="用于校对切片数量，可不填。"
      />
    </div>
  )
}

export function ParseOptionFields({
  idPrefix,
  value,
  onChange,
}: {
  idPrefix: string
  value: ParseOptions
  onChange: (value: ParseOptions) => void
}) {
  return (
    <div className="ingest-grid">
      <div className="ingest-field">
        <label htmlFor={`${idPrefix}-ocr`}>OCR 方式</label>
        <select
          id={`${idPrefix}-ocr`}
          value={value.ocr}
          onChange={(event) => onChange({ ...value, ocr: event.target.value })}
        >
          {OCR_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      </div>
      <div className="ingest-field">
        <label htmlFor={`${idPrefix}-chunker`}>切片方式</label>
        <select
          id={`${idPrefix}-chunker`}
          value={value.chunker}
          onChange={(event) => onChange({ ...value, chunker: event.target.value })}
        >
          {CHUNKER_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      </div>
      <Field
        id={`${idPrefix}-max`}
        label="单个切片最大字数"
        type="number"
        value={String(value.max_chunk_chars)}
        onChange={(next) => onChange({ ...value, max_chunk_chars: Number(next) })}
      />
      <Field
        id={`${idPrefix}-overlap`}
        label="按长度切片的重叠字数"
        type="number"
        value={String(value.chunk_overlap)}
        onChange={(next) => onChange({ ...value, chunk_overlap: Number(next) })}
      />
    </div>
  )
}
