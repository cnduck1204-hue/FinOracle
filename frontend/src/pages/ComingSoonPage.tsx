import { ArrowUpRight, BookOpen, Star } from 'lucide-react'
import { Link } from 'react-router-dom'

const modules = {
  library: {
    label: '制度库',
    icon: BookOpen,
    description: '制度收录开放后，你可以在这里按名称、发布机构和主题系统浏览与查找原文。',
  },
  favorites: {
    label: '我的收藏',
    icon: Star,
    description: '收藏功能开放后，你可以在这里归档常用制度、重点条文和有价值的问答。',
  },
}

export function ComingSoonPage({ module }: { module: keyof typeof modules }) {
  const item = modules[module]
  const Icon = item.icon
  return (
    <section className="empty-page">
      <div className="empty-symbol">
        <Icon size={26} strokeWidth={1.5} aria-hidden="true" />
      </div>
      <span className="readiness-badge">即将开放</span>
      <h1>{item.label}，正在准备。</h1>
      <p>
        {item.description}
        <br />
        现在，你可以先前往智能问答或全文检索。
      </p>
      <Link className="btn btn-primary" to="/chat">
        先整理一个问题
        <ArrowUpRight size={15} aria-hidden="true" />
      </Link>
    </section>
  )
}
