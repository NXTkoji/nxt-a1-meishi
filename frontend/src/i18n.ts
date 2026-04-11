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
    myCompanyLabel: '自社',
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

    // ParsedCardEditor
    nameSection: (n: number) => `個人 ${n}`,
    orgSection: (n: number) => `組織 ${n}`,
    personalContactsLabel: '個人連絡先',
    workContactsLabel: '勤務先連絡先',
    addFieldLabel: '＋ フィールド追加',
    addOrgLabel: '＋ 組織追加',
    addNameLabel: '＋ 個人追加',
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
    myCompaniesTitle: '自社一覧',
    addCompanyPlaceholder: '会社名を入力…',
    addCompanyBtn: '＋ 追加',
    deleteBtn: '削除',
    confirmDelete: '削除してよろしいですか？',
    occasionsTitle: '場面一覧',
    addOccasionPlaceholder: '場面名を入力…',

    // Person detail
    viewPerson: '人物を見る',
    personNotFound: '人物が見つかりません',
    linkedCards: '名刺',
    deletePersonBtn: '人物を削除',
    confirmDeletePerson: 'この人物とそのデータをすべて削除しますか？\n\n※ 関連する名刺には影響しません。',
    personDeleted: '人物を削除しました',

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
    myCompanyLabel: 'My Company',
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

    // ParsedCardEditor
    nameSection: (n: number) => `Personal ${n}`,
    orgSection: (n: number) => `Organization ${n}`,
    personalContactsLabel: 'Personal Contacts',
    workContactsLabel: 'Work Contacts',
    addFieldLabel: '+ Add Field',
    addOrgLabel: '+ Add Organization',
    addNameLabel: '+ Add Personal',
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
    myCompaniesTitle: 'My Companies',
    addCompanyPlaceholder: 'Company name…',
    addCompanyBtn: '+ Add',
    deleteBtn: 'Delete',
    confirmDelete: 'Are you sure you want to delete this?',
    occasionsTitle: 'Occasions',
    addOccasionPlaceholder: 'Occasion name…',

    // Person detail
    viewPerson: 'View person',
    personNotFound: 'Person not found',
    linkedCards: 'Business Cards',
    deletePersonBtn: 'Delete person',
    confirmDeletePerson: 'Delete this person and all their data?\n\nNote: their business cards will not be deleted.',
    personDeleted: 'Person deleted',

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
  },
} as const

// Recursively widen string literals so both ja and en satisfy the type
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type _Widen<T> = T extends string ? string : T extends readonly (infer U)[] ? readonly _Widen<U>[] : T extends (...args: infer A) => infer R ? (...args: A) => R : T extends object ? { [K in keyof T]: _Widen<T[K]> } : T
export type Translations = _Widen<typeof translations.ja>
export { translations }
