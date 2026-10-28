import { useRef, useState, type FormEvent } from 'react'
import {
  ArrowRight,
  ArrowUpRight,
  Check,
  ChevronDown,
  CircleHelp,
  FolderOpen,
  Info,
} from 'lucide-react'
import { useAuth } from '../auth/AuthProvider'
import { OracleGraphic } from '../components/OracleGraphic'

function readDraft(key: string) {
  try {
    const saved = JSON.parse(sessionStorage.getItem(key) || 'null') as {
      text?: unknown
      savedAt?: unknown
    } | null
    return {
      text: typeof saved?.text === 'string' ? saved.text.slice(0, 2000) : '',
      savedAt: typeof saved?.savedAt === 'number' ? saved.savedAt : null,
    }
  } catch {
    return { text: '', savedAt: null }
  }
}

export function WorkspacePage() {
  const { user } = useAuth()
  const draftKey = `finoracle.draft.v1:${user!.id}`
  const [initial] = useState(() => readDraft(draftKey))
  const [question, setQuestion] = useState(initial.text)
  const [savedAt, setSavedAt] = useState(initial.savedAt)
  const [dirty, setDirty] = useState(false)
  const [feedback, setFeedback] = useState('')
  const textarea = useRef<HTMLTextAreaElement>(null)
  const starters = [
    {
      title: '先厘清制度范围',
      description: '业务背景 / 适用对象 / 待确认事项',
      question: '我想了解【业务场景】下适用的制度要求，重点关注【待确认事项】。',
    },
    {
      title: '从具体条款出发',
      description: '制度名称 / 条文编号 / 理解难点',
      question: '关于【制度名称】中的【条文编号】，我希望进一步理解【具体问题】。',
    },
  ]

  function updateQuestion(text: string) {
    setQuestion(text)
    setDirty(true)
    setFeedback('')
  }
  function chooseStarter(text: string) {
    updateQuestion(text)
    textarea.current?.focus()
  }
  function save(event: FormEvent) {
    event.preventDefault()
    if (!question.trim()) {
      textarea.current?.focus()
      return
    }
    const timestamp = Date.now()
    try {
      sessionStorage.setItem(draftKey, JSON.stringify({ text: question, savedAt: timestamp }))
      setSavedAt(timestamp)
      setDirty(false)
      setFeedback('草稿已保存在当前标签页。问答开放后，可继续使用。')
    } catch {
      setFeedback('浏览器未允许保存草稿，请先保留当前页面或自行复制问题。')
    }
  }

  return (
    <>
      <div className="welcome-label">
        <span aria-hidden="true" />
        <strong title={user!.username}>你好，{user!.username}</strong>
        <span>欢迎回到你的知识空间</span>
      </div>
      <section className="workspace-hero">
        <div className="hero-copy">
          <span className="eyebrow">// YOUR FINANCIAL KNOWLEDGE WORKSPACE</span>
          <h1>
            让每一个问题，
            <br />
            都有<em>据可循。</em>
          </h1>
          <p>
            从业务场景出发，整理你的制度疑问。
            <br />
            理解要求，厘清边界，回到原始依据。
          </p>
        </div>
        <OracleGraphic small />
      </section>
      <section aria-labelledby="question-heading">
        <h2 className="section-title" id="question-heading">
          <span>01 /</span>从一个问题开始
        </h2>
        <form className="composer" onSubmit={save}>
          <div className="composer-top">
            <label htmlFor="question">你想了解什么？</label>
            <span>{question.length} / 2000</span>
          </div>
          <textarea
            ref={textarea}
            id="question"
            placeholder="描述你的业务场景，或写下想进一步理解的制度问题……"
            value={question}
            onChange={(event) => updateQuestion(event.target.value)}
            maxLength={2000}
            aria-describedby="question-help"
          />
          <div className="composer-bottom">
            <span className="composer-scope">
              <FolderOpen size={13} aria-hidden="true" />
              制度范围 · 即将开放
              <ChevronDown size={11} aria-hidden="true" />
            </span>
            <button className="cut-button button-dark" type="submit" disabled={!question.trim()}>
              保存问题草稿
              <ArrowUpRight size={15} aria-hidden="true" />
            </button>
          </div>
        </form>
        <div className="composer-caption">
          <span id="question-help">
            <Info size={12} aria-hidden="true" />
            问答功能正在准备。草稿仅保存在当前标签页，不会提交给服务端。
          </span>
          {savedAt && (
            <span>
              {dirty
                ? '有尚未保存的修改'
                : `已保存于 ${new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit' }).format(savedAt)}`}
            </span>
          )}
        </div>
        {feedback && (
          <div className="draft-feedback" role="status">
            <Check size={13} aria-hidden="true" />
            {feedback}
          </div>
        )}
      </section>
      <section className="starter-section" aria-labelledby="starter-heading">
        <div className="starter-header">
          <h2 className="section-title" id="starter-heading">
            <CircleHelp size={15} strokeWidth={1.6} aria-hidden="true" />
            不知道如何开始？
          </h2>
          <small>START WITH A CLEAR QUESTION</small>
        </div>
        <div className="starter-grid">
          {starters.map((starter, index) => (
            <button
              className="starter-card"
              key={starter.title}
              onClick={() => chooseStarter(starter.question)}
            >
              <span className="starter-number">0{index + 1}</span>
              <span>
                <strong>{starter.title}</strong>
                <small>{starter.description}</small>
              </span>
              <ArrowUpRight size={16} aria-hidden="true" />
            </button>
          ))}
        </div>
      </section>
      <div className="workspace-flow" aria-label="知识探索流程">
        <div className="flow-item current">
          <span className="flow-square">01</span>提出问题
        </div>
        <span className="flow-line" />
        <div className="flow-item">
          <span className="flow-square">02</span>理解制度
        </div>
        <span className="flow-line" />
        <div className="flow-item">
          <span className="flow-square">03</span>回溯原文
        </div>
        <ArrowRight size={12} color="var(--rule)" aria-hidden="true" />
      </div>
    </>
  )
}
