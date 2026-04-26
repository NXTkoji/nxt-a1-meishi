export type Lang = 'ja' | 'en'

const translations = {
  ja: {
    // Nav
    appName: '名片整理器',
    navCollection: 'コレクション',
    navScan: 'スキャン',
    navSettings: '設定',
    langToggle: 'English',

    // ScanPage — stages
    scanTitle: '名刺スキャン',
    stageUpload: 'アップロード',
    stageGroup: 'グループ',
    stageAnalyze: '解析',
    stageReview: '確認',
    stageDone: '完了',

    // ScanPage — upload zone
    dropPrompt: '名刺の写真をドロップ',
    dropOr: 'または クリックしてファイルを選択',

    // ScanPage — grouping
    ungroupedN: (n: number) => `未グループ (${n}枚)`,
    autoGroup1: '1枚ずつ（片面）',
    autoGroup2: '2枚ペア（両面）',
    autoPairByPos: '位置でペア',
    splitCards: '名刺を分割',
    splitting: '分割中…',
    splitDone: (n: number) => `${n}枚に分割しました`,
    splitNone: '名刺は1枚のみ検出されました',
    cardGroupsN: (n: number) => `名刺グループ (${n}枚)`,
    addGroup: '＋グループ追加',
    startAnalysis: '解析開始 →',

    // ScanPage — card group card
    cardN: (n: number) => `名刺 #${n}`,
    analyzing: '解析中…',
    existingMatch: (name: string, pct: number) => `既存: ${name} (${pct}%)`,
    sideLabels: ['表', '裏'],
    sideN: (n: number) => `面${n}`,
    emptySlot: '空',

    // ScanPage — review metadata
    myCompanyLabel: '対面時の立場',
    occasionLabel: '場面',
    occasionAddNew: '＋ 新しい場面',
    occasionNewPlaceholder: '場面名を入力…',
    receivedDateLabel: '受取日',
    existingPersonLabel: '既存人物',
    createNew: '✕ 新規作成',
    noneOption: 'なし',

    // ScanPage — actions
    retryAnalysis: '再解析 ↺',
    saveN: (n: number) => `${n} 枚を保存する ✓`,
    saving: '保存中…',
    savedN: (n: number) => `${n} 枚の名刺を保存しました`,
    newScan: '新しいスキャン',
    viewCollection: 'コレクションを見る',

    // CollectionPage
    collectionTitle: '名刺コレクション',
    newScanBtn: '＋ 新しいスキャン',
    tabCards: '名刺',
    tabPersons: '人物',
    searchPlaceholder: '名前で検索…',
    loading: '読み込み中…',
    emptyMessage: 'まだ名刺がありません',
    emptyCta: '最初の名刺をスキャン',
    noName: '(名前なし)',
    unknownCountry: '国不明',

    // ParsedCardEditor
    nameSection: (n: number) => `個人 ${n}`,
    orgSection: (n: number) => `組織 ${n}`,
    personalContactsLabel: '個人連絡先',
    workContactsLabel: '勤務先連絡先',
    addFieldLabel: '＋ フィールド追加',
    addOrgLabel: '＋ 組織追加',
    addNameLabel: '＋ 個人追加',
    addTitleLabel: '＋ 役職追加',
    removeLabel: '削除',
    fieldFullName: 'フルネーム',
    fieldFamilyName: '姓',
    fieldGivenName: '名',
    fieldHonorific: '敬称',
    fieldCompany: (lang: string) => `会社名 (${lang})`,
    fieldTitle: (lang: string) => `役職 (${lang})`,
    fieldDept: (lang: string) => `部署 (${lang})`,
    emptyField: '空白',
    addImageLabel: '画像追加',
    swapSidesLabel: '表面に設定',
    notesLabel: 'メモ',
    notesPlaceholder: 'メモを追加…',
    addPhotoLabel: '写真追加',
    saveBtn: '保存',
    cancelBtn: '取消',
    contactLabels: {
      phone_work: '電話(勤務先)',
      phone_mobile: '携帯',
      phone_fax: 'FAX',
      email_work: 'メール(勤務先)',
      email_personal: 'メール(個人)',
      address_work: '住所(勤務先)',
      address_home: '住所(自宅)',
      url_website: 'ウェブサイト',
      gui_number: '統一編號',
      social_wechat: 'WeChat',
      social_line: 'LINE',
      social_linkedin: 'LinkedIn',
      social_other: 'SNS',
      relationship: '関係性',
      personal_title: '称号',
      introducer: '介紹人',
    },

    // Settings page
    settingsTitle: '設定',
    myCompaniesTitle: '対面時の立場一覧',
    addCompanyPlaceholder: '立場名を入力…',
    addCompanyBtn: '＋ 追加',
    deleteBtn: '削除',
    confirmDelete: '削除してよろしいですか？',
    occasionsTitle: '場面一覧',
    addOccasionPlaceholder: '場面名を入力…',

    countriesTitle: '国一覧',
    addCountryCodePlaceholder: 'JP',
    addCountryNamePlaceholder: '国名を入力…',
    countryUnset: '国を選択…',
    countryClear: 'クリア',
    countryNoneRegistered: '登録された国がありません。設定から追加してください。',

    // Person detail
    viewPerson: '人物を見る',
    personNotFound: '人物が見つかりません',
    linkedCards: '名刺',
    deletePersonBtn: '人物を削除',
    confirmDeletePerson: 'この人物とそのデータをすべて削除しますか？\\n\\n※ 関連する名刺には影響しません。',
    personDeleted: '人物を削除しました',

    thumbnailNameLabel: 'サムネイル表示名',
    thumbnailNameAuto: '自動',

    // Feedback toasts
    savedChanges: '保存しました',
    saveError: '保存に失敗しました',
    deleteConfirmed: '削除しました',

    // Scan — cancel
    cancelAnalysis: 'キャンセル',

    // Scan — back to grouping / start over
    backToGrouping: '← グループ編集に戻る',
    noCardDataHint: '名刺データが取得できませんでした。写真に複数の名刺が含まれている場合は「← グループ編集に戻る」を押して、✂️ で分割してから再解析してください。',
    startOver: '最初からやり直す',

    // Export flow
    navExport: 'エクスポート',
    enterManuallyBtn: '＋ 手動入力',
    manualEntryTitle: '人物情報を入力',
    exportBtn: 'エクスポート',
    exportTitle: 'カードをエクスポート',
    exportSearchPlaceholder: '名前・会社・メール・電話で検索…',
    exportFilterYear: '年',
    exportFilterMonth: '月',
    exportFilterDate: '日付',
    exportFilterOccasion: '場面',
    exportFilterMetAs: '対面時の立場',
    exportFilterNotExported: '未エクスポートのみ',
    exportClearFilter: '✕',
    exportSelectAll: (n: number) => `${n} 件を全選択`,
    exportDeselectAll: '選択解除',
    exportNextBtn: (n: number) => `次へ: 宛先を選択 (${n} 件) →`,
    exportDestTitle: '宛先を選択',
    exportDestOdoo: 'Odoo',
    exportDestGoogle: 'Google Contacts',
    exportDestNotConfigured: '未設定',
    exportDestSetup: '設定 →',
    exportRunBtn: (n: number, dest: string) => `${n} 件を ${dest} にエクスポート`,
    exportResultCreated: '✓ 作成',
    exportResultUpdated: '✓ 更新',
    exportResultError: '✗ エラー',
    exportBackToList: '← カード一覧に戻る',
    exportAlreadySynced: '同期済み',

    // Duplicate check panel
    dupPanelTitle: '既存の連絡先が見つかりました',
    dupExisting: '既存',
    dupNewCard: '新しい名刺',
    dupDragHint: '右のフィールドを左にドラッグして取り込む',
    dupNotDuplicate: '別人として保存 →',
    dupDiscard: '新しい名刺を破棄',
    dupConfirmMerge: 'マージして保存',

    // ScanPage — move/delete card group
    moveToCard: (n: number) => `→ #${n}`,
    deleteGroupLabel: 'カードを削除',
    deleteGroupDisabledHint: '画像をすべて移動してから削除できます',
  },

  en: {
    // Nav
    appName: 'Card Manager',
    navCollection: 'Collection',
    navScan: 'Scan',
    navSettings: 'Settings',
    langToggle: '日本語',

    // ScanPage — stages
    scanTitle: 'Business Card Scan',
    stageUpload: 'Upload',
    stageGroup: 'Group',
    stageAnalyze: 'Analyze',
    stageReview: 'Review',
    stageDone: 'Done',

    // ScanPage — upload zone
    dropPrompt: 'Drop card photos here',
    dropOr: 'or click to select files',

    // ScanPage — grouping
    ungroupedN: (n: number) => `Ungrouped (${n})`,
    autoGroup1: '1 per card (single-sided)',
    autoGroup2: 'Pairs of 2 (double-sided)',
    autoPairByPos: 'Pair by position',
    splitCards: 'Split Cards',
    splitting: 'Detecting…',
    splitDone: (n: number) => `Split into ${n} cards`,
    splitNone: 'Only 1 card detected',
    cardGroupsN: (n: number) => `Card Groups (${n})`,
    addGroup: '+ Add Group',
    startAnalysis: 'Start Analysis →',

    // ScanPage — card group card
    cardN: (n: number) => `Card #${n}`,
    analyzing: 'Analyzing…',
    existingMatch: (name: string, pct: number) => `Existing: ${name} (${pct}%)`,
    sideLabels: ['Front', 'Back'],
    sideN: (n: number) => `Side ${n}`,
    emptySlot: 'Empty',

    // ScanPage — review metadata
    myCompanyLabel: 'Met As',
    occasionLabel: 'Occasion',
    occasionAddNew: '+ New Occasion',
    occasionNewPlaceholder: 'Occasion name…',
    receivedDateLabel: 'Received',
    existingPersonLabel: 'Existing Person',
    createNew: '✕ Create New',
    noneOption: 'None',

    // ScanPage — actions
    retryAnalysis: 'Retry Analysis ↺',
    saveN: (n: number) => `Save ${n} card${n !== 1 ? 's' : ''} ✓`,
    saving: 'Saving…',
    savedN: (n: number) => `Saved ${n} business card${n !== 1 ? 's' : ''}`,
    newScan: 'New Scan',
    viewCollection: 'View Collection',

    // CollectionPage
    collectionTitle: 'Business Card Collection',
    newScanBtn: '+ New Scan',
    tabCards: 'Cards',
    tabPersons: 'Persons',
    searchPlaceholder: 'Search by name…',
    loading: 'Loading…',
    emptyMessage: 'No business cards yet',
    emptyCta: 'Scan your first card',
    noName: '(No name)',
    unknownCountry: 'Unknown Country',

    // ParsedCardEditor
    nameSection: (n: number) => `Personal ${n}`,
    orgSection: (n: number) => `Organization ${n}`,
    personalContactsLabel: 'Personal Contacts',
    workContactsLabel: 'Work Contacts',
    addFieldLabel: '+ Add Field',
    addOrgLabel: '+ Add Organization',
    addNameLabel: '+ Add Personal',
    addTitleLabel: '+ Add Title',
    removeLabel: 'Remove',
    fieldFullName: 'Full Name',
    fieldFamilyName: 'Family Name',
    fieldGivenName: 'Given Name',
    fieldHonorific: 'Honorific',
    fieldCompany: (lang: string) => `Company (${lang})`,
    fieldTitle: (lang: string) => `Title (${lang})`,
    fieldDept: (lang: string) => `Dept (${lang})`,
    emptyField: 'empty',
    addImageLabel: 'Add Image',
    swapSidesLabel: 'Promote to Front',
    notesLabel: 'Notes',
    notesPlaceholder: 'Add a note...',
    addPhotoLabel: 'Add Photo',
    saveBtn: 'Save',
    cancelBtn: 'Cancel',
    contactLabels: {
      phone_work: 'Phone (Work)',
      phone_mobile: 'Mobile',
      phone_fax: 'Fax',
      email_work: 'Email (Work)',
      email_personal: 'Email (Personal)',
      address_work: 'Address (Work)',
      address_home: 'Address (Home)',
      url_website: 'Website',
      gui_number: 'GUI Number',
      social_wechat: 'WeChat',
      social_line: 'LINE',
      social_linkedin: 'LinkedIn',
      social_other: 'Social',
      relationship: 'Relationship',
      personal_title: 'Title',
      introducer: 'Introducer',
    },

    // Settings page
    settingsTitle: 'Settings',
    myCompaniesTitle: 'Met As',
    addCompanyPlaceholder: 'Name…',
    addCompanyBtn: '+ Add',
    deleteBtn: 'Delete',
    confirmDelete: 'Are you sure you want to delete this?',
    occasionsTitle: 'Occasions',
    addOccasionPlaceholder: 'Occasion name…',

    countriesTitle: 'Countries',
    addCountryCodePlaceholder: 'JP',
    addCountryNamePlaceholder: 'Country name…',
    countryUnset: 'set country…',
    countryClear: 'clear',
    countryNoneRegistered: 'No countries registered. Add them in Settings.',

    // Person detail
    viewPerson: 'View person',
    personNotFound: 'Person not found',
    linkedCards: 'Business Cards',
    deletePersonBtn: 'Delete person',
    confirmDeletePerson: 'Delete this person and all their data?\\n\\nNote: their business cards will not be deleted.',
    personDeleted: 'Person deleted',

    thumbnailNameLabel: 'Thumbnail name',
    thumbnailNameAuto: 'Auto',

    // Feedback toasts
    savedChanges: 'Saved',
    saveError: 'Failed to save',
    deleteConfirmed: 'Deleted',

    // Scan — cancel
    cancelAnalysis: 'Cancel',

    // Scan — back to grouping / start over
    backToGrouping: '← Back to Grouping',
    noCardDataHint: 'No card data found. If your photo contains multiple cards, click "← Back to Grouping" then use ✂️ to split them before re-analyzing.',
    startOver: 'Start over',

    // Export flow
    navExport: 'Export',
    enterManuallyBtn: '+ Enter Manually',
    manualEntryTitle: 'Enter Person Details',
    exportBtn: 'Export',
    exportTitle: 'Export Cards',
    exportSearchPlaceholder: 'Search by name, company, email, phone…',
    exportFilterYear: 'Year',
    exportFilterMonth: 'Month',
    exportFilterDate: 'Date',
    exportFilterOccasion: 'Occasion',
    exportFilterMetAs: 'Met As',
    exportFilterNotExported: 'Not yet exported',
    exportClearFilter: '✕',
    exportSelectAll: (n: number) => `Select all ${n}`,
    exportDeselectAll: 'Deselect all',
    exportNextBtn: (n: number) => `Next: Choose destinations (${n} cards) →`,
    exportDestTitle: 'Choose destinations',
    exportDestOdoo: 'Odoo',
    exportDestGoogle: 'Google Contacts',
    exportDestNotConfigured: 'Not configured',
    exportDestSetup: 'Set up →',
    exportRunBtn: (n: number, dest: string) => `Export ${n} cards to ${dest}`,
    exportResultCreated: '✓ Created',
    exportResultUpdated: '✓ Updated',
    exportResultError: '✗ Error',
    exportBackToList: '← Back to card list',
    exportAlreadySynced: 'already synced',

    // Duplicate check panel
    dupPanelTitle: 'Existing contact found',
    dupExisting: 'Existing',
    dupNewCard: 'New card',
    dupDragHint: 'Drag fields from right column into the left to apply them',
    dupNotDuplicate: 'Not a duplicate →',
    dupDiscard: 'Discard new card',
    dupConfirmMerge: 'Confirm merge',

    // ScanPage — move/delete card group
    moveToCard: (n: number) => `→ #${n}`,
    deleteGroupLabel: 'Delete Card',
    deleteGroupDisabledHint: 'Move all images out first to delete',
  },
} as const

// Recursively widen string literals so both ja and en satisfy the type
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type _Widen<T> = T extends string ? string : T extends readonly (infer U)[] ? readonly _Widen<U>[] : T extends (...args: infer A) => infer R ? (...args: A) => R : T extends object ? { [K in keyof T]: _Widen<T[K]> } : T
export type Translations = _Widen<typeof translations.ja>
export { translations }
