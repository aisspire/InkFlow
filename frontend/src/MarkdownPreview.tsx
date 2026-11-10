import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

export default function MarkdownPreview({ text, className = '' }: { text: string; className?: string }) {
  return <div className={`markdown-preview ${className}`}>
    <ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml
      urlTransform={url => /^(https?:|mailto:|#)/i.test(url) ? url : ''}
      components={{
        img: ({ alt }) => <span className="image-placeholder">[图片：{alt || '未命名图片'}]</span>,
        a: ({ href, children }) => href ? <a href={href} target={href.startsWith('#') ? undefined : '_blank'} rel="noreferrer">{children}</a> : <span>{children}</span>,
      }}>{text}</ReactMarkdown>
  </div>;
}
