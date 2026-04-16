import { createContext, useContext, useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { translations } from './i18n'
import type { Lang, Translations } from './i18n'

interface LangContextValue {
  lang: Lang
  t: Translations
  setLang: (l: Lang) => void
}

const LangContext = createContext<LangContextValue>({
  lang: 'en',
  t: translations.en,
  setLang: () => {},
})

export function LangProvider({ children }: { children: ReactNode }) {
  const [lang, setLang] = useState<Lang>(() => {
    const stored = localStorage.getItem('lang')
    return (stored === 'en' || stored === 'ja') ? stored : 'en'
  })

  useEffect(() => {
    document.documentElement.lang = lang
  }, [lang])

  const handleSetLang = (l: Lang) => {
    localStorage.setItem('lang', l)
    setLang(l)
  }

  return (
    <LangContext.Provider value={{ lang, t: translations[lang], setLang: handleSetLang }}>
      {children}
    </LangContext.Provider>
  )
}

export function useLang() {
  return useContext(LangContext)
}
