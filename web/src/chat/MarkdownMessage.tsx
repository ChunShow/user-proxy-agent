import Markdown, { defaultUrlTransform, type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'

const components: Components = {
  a: ({ href, children }) => href
    ? <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>
    : <span>{children}</span>,
  // Model-provided images must not silently make requests to third-party servers.
  img: ({ alt }) => <span className="markdown-image-label">{alt || '이미지'}</span>,
  table: ({ children }) => <div className="markdown-table-scroll" role="region" aria-label="표" tabIndex={0}>
    <table>{children}</table>
  </div>,
  pre: ({ children }) => <pre tabIndex={0} aria-label="코드">{children}</pre>,
}

function safeUrl(url: string) {
  const safe = defaultUrlTransform(url)
  return /^(https?:\/\/|mailto:|tel:)/i.test(safe) ? safe : ''
}

export default function MarkdownMessage({ text }: { text: string }) {
  return <div className="markdown-message">
    <Markdown remarkPlugins={[remarkGfm]} components={components} urlTransform={safeUrl} skipHtml>{text}</Markdown>
  </div>
}
