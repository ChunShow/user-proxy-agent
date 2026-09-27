export default function Brand() {
  return <div className="brand" aria-label="User Proxy Agent">
    <svg className="brand-mark" width="30" height="30" viewBox="0 0 30 30" fill="none" aria-hidden="true">
      <path d="M5 6v11a6 6 0 0 0 12 0V6" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" />
      <path d="M17 23V6h3a5.5 5.5 0 0 1 0 11h-3" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
    <span className="brand-wordmark" aria-hidden="true"><strong>User Proxy</strong><span>Agent</span></span>
  </div>
}
