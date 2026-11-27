/**
 * useAppSettings — the Settings page preferences, shared by every component that
 * depends on them (one cached query, refreshed when the Settings page saves).
 */
import { useQuery } from '@tanstack/react-query'
import { getAppSettings } from '../api'

export const APP_SETTINGS_KEY = ['app-settings'] as const

export function useAppSettings() {
  return useQuery({ queryKey: APP_SETTINGS_KEY, queryFn: getAppSettings, staleTime: 60_000 })
}

/** True when the Project Management module is switched on. While loading this is
 *  false, so the module's controls never flash in and out on a page that has it off. */
export function useProjectManagementEnabled(): boolean {
  const { data } = useAppSettings()
  return data?.project_management_enabled ?? false
}
