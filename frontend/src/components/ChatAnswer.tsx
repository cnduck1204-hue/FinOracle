import { memo, useId, useState } from 'react'
import type { Element, Root } from 'hast'
import { ExternalLink, X } from 'lucide-react'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { SKIP, visit } from 'unist-util-visit'
import { useAuth } from '../auth/AuthProvider'
import { errorMessage } from '../lib/api'
import { formatDate } from '../lib/dates'
import { articlePath, pageLabel } from '../lib/ingestion'
import type {
  ChatAnswerContent,
  ChatCitation,
  ChatReference,
  ChatVersionReference,
} from '../lib/types'
import { useResource } from '../lib/useResource'
import { ValidityTag } from './IngestionUi'
import { Notice } from './Notice'

function answerMarkdown(
  answer: string,
  citations: ReadonlyMap<number, ChatCitation>,
  selected: number | undefined,
  panelId: string,
  live: boolean,
) {
  // The server strips model-authored literal markers before appending its own.
  // Mark them before Markdown can decode entities or escapes into lookalikes.
  let tag = 'finoracle-citation'
  while (answer.includes(`<!--${tag}:`)) tag += '-'
  const prefix = `<!--${tag}:`
  const markdown = answer.replace(/\[(\d+)\](?!\()/g, (marker, number: string) =>
    citations.has(Number(number)) ? `${prefix}${number}-->` : marker,
  )
  const encodedMarker = new RegExp(`${prefix}(\\d+)-->`, 'g')
  const button = (number: number): Element => ({
    type: 'element',
    tagName: 'button',
    properties: {
      type: 'button',
      className: ['chat-citation'],
      dataCitationNumber: number,
      ariaLabel: `查看引用 ${number}`,
      ariaExpanded: selected === number ? 'true' : 'false',
      ...(selected === number && { ariaControls: [panelId] }),
    },
    children: [{ type: 'text', value: `[${number}]` }],
  })

  const rehype = () => (tree: Root) => {
    const remaining = new Set(citations.keys())
    visit(tree, (node, index, parent) => {
      if (node.type === 'element') {
        if (node.tagName === 'button' || node.tagName === 'a') return SKIP
        if (node.tagName === 'code') {
          visit(node, 'text', (literal) => {
            literal.value = literal.value.replace(encodedMarker, '[$1]')
          })
          return SKIP
        }
      }
      if (node.type === 'text') {
        node.value = node.value
          .replace(encodedMarker, '')
          .replace(/\[\s*\d+\s*\]|［\s*\d+\s*］/g, '')
      } else if (
        node.type === 'raw' &&
        node.value.startsWith(prefix) &&
        parent &&
        index !== undefined
      ) {
        const number = Number(/^(\d+)-->$/.exec(node.value.slice(prefix.length))?.[1])
        if (!citations.has(number)) return
        remaining.delete(number)
        parent.children[index] = button(number)
        return SKIP
      }
    })
    if (live) {
      const caret: Element = {
        type: 'element',
        tagName: 'span',
        properties: { className: ['chat-reply-caret'], ariaHidden: 'true' },
        children: [],
      }
      const tail = tree.children.at(-1)
      if (tail?.type === 'element' && tail.tagName === 'p') tail.children.push(caret)
      else tree.children.push(caret)
    }
    if (remaining.size) {
      // Markdown can consume a marker, for example after the last cell of a table.
      tree.children.push({
        type: 'element',
        tagName: 'div',
        properties: {},
        children: [...remaining].map(button),
      })
    }
  }
  return { markdown, rehype }
}

function CitationPanel({
  id,
  citation,
  close,
}: {
  id: string
  citation: ChatCitation
  close: () => void
}) {
  const { client } = useAuth()
  const sourceId = citation.kind === 'version' ? citation.version.id : citation.chunk_id
  const { data, error, loading } = useResource<ChatReference | ChatVersionReference>(
    () =>
      citation.kind === 'version'
        ? client.getChatVersionReference(citation.version.id)
        : client.getChatReference(citation.chunk_id),
    [client, citation.kind, sourceId],
  )
  return (
    <aside className="chat-source" id={id} aria-labelledby={`${id}-heading`}>
      <div className="chat-source-header">
        <h3 id={`${id}-heading`}>条文溯源 · 引用 [{citation.number}]</h3>
        <button type="button" className="icon-button" onClick={close} aria-label="关闭引用">
          <X size={16} aria-hidden="true" />
        </button>
      </div>
      {loading && (
        <p className="chat-source-meta" role="status">
          正在读取引用…
        </p>
      )}
      {Boolean(error) && (
        <Notice kind="error" error={error}>
          {errorMessage(error)}
        </Notice>
      )}
      {data && (
        <>
          <h4>{data.document.title}</h4>
          {'content' in data && (
            <p className="chat-source-meta">
              {articlePath(data)} · {pageLabel(data.page_start, data.page_end)}
            </p>
          )}
          <p className="chat-source-meta">
            {[data.version.version_note, data.version.document_number]
              .filter(Boolean)
              .join(' · ') || '已发布版本'}
          </p>
          <div>
            <ValidityTag status={data.version.validity_status} />
          </div>
          {'content' in data ? (
            <blockquote>{data.content}</blockquote>
          ) : (
            <dl className="search-citation">
              <div>
                <dt>生效日期</dt>
                <dd>{formatDate(data.version.effective_date)}</dd>
              </div>
              <div>
                <dt>核验日期</dt>
                <dd>{formatDate(data.version.validity_checked_at)}</dd>
              </div>
            </dl>
          )}
          {data.version.source_url && (
            <a
              className="chat-source-link"
              href={data.version.source_url}
              target="_blank"
              rel="noreferrer noopener"
            >
              查看发布依据 <ExternalLink size={12} aria-hidden="true" />
            </a>
          )}
        </>
      )}
    </aside>
  )
}

function AnswerMarkdown({ body }: { body: ReturnType<typeof answerMarkdown> }) {
  return (
    <Markdown
      skipHtml
      disallowedElements={['img']}
      remarkPlugins={[remarkGfm]}
      rehypePlugins={[body.rehype]}
      components={{ a: ({ children }) => children }}
    >
      {body.markdown}
    </Markdown>
  )
}

export const ChatAnswer = memo(function ChatAnswer({
  outcome,
  live = false,
}: {
  outcome: ChatAnswerContent
  live?: boolean
}) {
  const sourcePanelId = useId()
  const [selected, setSelected] = useState<ChatCitation | null>(null)
  const citations = new Map(outcome.citations.map((citation) => [citation.number, citation]))
  const answer = answerMarkdown(outcome.answer, citations, selected?.number, sourcePanelId, live)

  return (
    <section
      className={`chat-answer-layout${selected ? ' chat-answer-with-source' : ''}`}
      aria-label="制度问答结果"
    >
      <div className="chat-answer">
        <div className="chat-answer-head">
          <span className="chat-answer-badge" aria-hidden="true">
            F
          </span>
          <h3>回答</h3>
        </div>
        <div
          className="chat-answer-body"
          aria-live="polite"
          aria-busy={live}
          onClick={(event) => {
            if (!(event.target instanceof HTMLElement)) return
            const button = event.target.closest<HTMLButtonElement>('button[data-citation-number]')
            const citation = citations.get(Number(button?.dataset.citationNumber))
            if (citation) setSelected(citation)
          }}
        >
          <AnswerMarkdown body={answer} />
        </div>
      </div>
      {selected && (
        <CitationPanel
          id={sourcePanelId}
          key={selected.number}
          citation={selected}
          close={() => setSelected(null)}
        />
      )}
    </section>
  )
})
