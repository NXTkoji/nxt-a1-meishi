/**
 * Toast notification system.
 * Wrap your app with <ToastProvider> and call useToast() anywhere.
 */
import { createContext, useCallback, useContext, useState } from 'react'
import { createPortal } from 'react-dom'

interface ToastItem {
  id: number
  message: string
  type: 'success' | 'error'
}

interface ToastCtx {
  showToast: (message: string, type?: 'success' | 'error') => void
}

const ToastContext = createContext<ToastCtx>({ showToast: () => {} })
export const useToast = () => useContext(ToastContext)

let _id = 1

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([])

  const showToast = useCallback((message: string, type: 'success' | 'error' = 'success') => {
    const id = _id++
    setToasts(prev => [...prev, { id, message, type }])
    setTimeout(() => setToasts(prev => prev.filter(t => t.id !== id)), 3000)
  }, [])

  return (
    <ToastContext.Provider value={{ showToast }}>
      {children}
      {createPortal(
        <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-[100] flex flex-col gap-2 items-center pointer-events-none">
          {toasts.map(t => (
            <div
              key={t.id}
              className={`px-4 py-2 rounded-lg shadow-lg text-sm font-medium text-white pointer-events-auto ${
                t.type === 'success' ? 'bg-green-600' : 'bg-red-500'
              }`}
            >
              {t.message}
            </div>
          ))}
        </div>,
        document.body
      )}
    </ToastContext.Provider>
  )
}
