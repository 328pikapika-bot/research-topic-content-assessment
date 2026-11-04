/** One destination policy for evidence cards, citations and downloads. */
export function resolveEvidenceUrl(evidence: { nstl_url?: string; source_url?: string; url?: string; doi?: string; paper_id?: string; id?: string }): string {
  const valid = (value?: string) => {
    const text = String(value || '').trim()
    try {
      const url = new URL(text)
      if (!['http:', 'https:'].includes(url.protocol) || url.username) return ''
      const host = url.hostname.toLowerCase().replace(/\.$/, '')
      const path = url.pathname.replace(/\/$/, '').toLowerCase()
      if ((host === 'nstl.gov.cn' || host.endsWith('.nstl.gov.cn')) &&
          ['', '/index.html', '/index.htm', '/home', '/homepage'].includes(path)) return ''
      return url.href
    } catch { return '' }
  }
  const destination = valid(evidence.nstl_url) || valid(evidence.source_url) || valid(evidence.url)
  if (destination) return destination
  const paperId = String(evidence.paper_id || evidence.id || '').trim()
  if (paperId && paperId.length <= 256) return `https://www.nstl.gov.cn/paper_detail.html?id=${encodeURIComponent(paperId)}`
  const doi = String(evidence.doi || '').trim().replace(/^https?:\/\/(?:dx\.)?doi\.org\//i, '').replace(/^doi:\s*/i, '')
  return /^10\.\d{4,9}\/\S+$/i.test(doi) ? `https://doi.org/${doi}` : ''
}
