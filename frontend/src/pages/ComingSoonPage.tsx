import { ArrowUpRight, BookOpen, History, Search, Star } from 'lucide-react'
import { Link } from 'react-router-dom'

const modules = {
  library: {
    label: '制度库',
    english: 'DOCUMENT LIBRARY',
    icon: BookOpen,
    description: '制度收录开放后，你可以在这里按名称、发布机构和主题查找原文。',
  },
  search: {
    label: '全文检索',
    english: 'FULL-TEXT SEARCH',
    icon: Search,
    description: '全文检索开放后，你可以按关键词和条号，快速定位制度中的相关依据。',
  },
  history: {
    label: '会话历史',
    english: 'CONVERSATION HISTORY',
    icon: History,
    description: '问答功能开放后，你的会话会在这里汇集，方便回到尚未完成的探索。',
  },
  favorites: {
    label: '我的收藏',
    english: 'YOUR COLLECTION',
    icon: Star,
    description: '收藏功能开放后，你可以在这里整理常用制度、条文和有帮助的回答。',
  },
}
export function ComingSoonPage({ module }: { module: keyof typeof modules }) {
  const item = modules[module]
  const Icon = item.icon
  return (
    <section className="empty-page">
      <div className="empty-symbol">
        <Icon size={36} strokeWidth={1.1} aria-hidden="true" />
      </div>
      <span className="eyebrow">// {item.english}</span>
      <h1>{item.label}，正在准备。</h1>
      <p>
        {item.description}
        <br />
        现在，你可以先整理问题，或管理自己的账号。
      </p>
      <Link className="cut-button button-dark" to="/chat">
        先整理一个问题
        <ArrowUpRight size={15} />
      </Link>
      <span className="readiness-badge">即将开放</span>
    </section>
  )
}
