/**
 * ManualPage — the in-app user manual, in the current UI language.
 *
 * Content lives in manualContent.ts; this page only lays it out: a table of
 * contents linking to each section, then the sections themselves.
 */
import { useLang } from '../LangContext'
import { manualContent, type ManualBlock } from '../manualContent'

export function ManualPage() {
  const { lang } = useLang()
  const content = manualContent[lang]

  return (
    <div className="max-w-3xl mx-auto py-6 px-4 space-y-6">
      <header className="space-y-2">
        <h1 className="text-lg font-semibold text-gray-900">{content.title}</h1>
        <p className="text-sm text-gray-600">{content.intro}</p>
      </header>

      {/* Table of contents */}
      <nav className="rounded-lg border border-gray-200 bg-white px-4 py-3">
        <h2 className="text-xs font-medium text-gray-500 mb-2">{content.tocTitle}</h2>
        <ol className="grid sm:grid-cols-2 gap-x-6 gap-y-1 text-sm">
          {content.sections.map(s => (
            <li key={s.id}>
              <a href={`#${s.id}`} className="text-blue-600 hover:underline">{s.title}</a>
            </li>
          ))}
        </ol>
      </nav>

      {content.sections.map(s => (
        // scroll-mt keeps the heading clear of the sticky top bar when jumped to.
        <section key={s.id} id={s.id} className="scroll-mt-16 rounded-lg border border-gray-200 bg-white px-4 py-4 space-y-3">
          <h2 className="text-base font-semibold text-gray-900">{s.title}</h2>
          {s.blocks.map((b, i) => <Block key={i} block={b} />)}
        </section>
      ))}
    </div>
  )
}

/** Render one content block according to its kind. */
function Block({ block }: { block: ManualBlock }) {
  if ('p' in block) return <p className="text-sm text-gray-700 leading-relaxed">{block.p}</p>
  if ('steps' in block) {
    return (
      <ol className="list-decimal pl-5 space-y-1.5 text-sm text-gray-700 leading-relaxed">
        {block.steps.map((step, i) => <li key={i}>{step}</li>)}
      </ol>
    )
  }
  if ('list' in block) {
    return (
      <ul className="list-disc pl-5 space-y-1.5 text-sm text-gray-700 leading-relaxed">
        {block.list.map((item, i) => <li key={i}>{item}</li>)}
      </ul>
    )
  }
  if ('tip' in block) {
    return (
      <p className="rounded border border-blue-200 bg-blue-50 px-3 py-2 text-sm text-blue-800">
        {block.tip}
      </p>
    )
  }
  return (
    <p role="note" className="rounded border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900">
      ⚠️ {block.warning}
    </p>
  )
}
