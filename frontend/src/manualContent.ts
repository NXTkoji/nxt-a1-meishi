/**
 * User manual content, one copy per UI language.
 *
 * Kept out of i18n.ts because it is long-form prose, not UI labels. Button and
 * field names quoted here must match each language's labels in i18n.ts, so
 * update both when a label changes.
 */
import type { Lang } from './i18n'

/** One block of manual content. Rendered in order inside its section. */
export type ManualBlock =
  | { p: string }          // paragraph
  | { steps: string[] }    // numbered steps
  | { list: string[] }     // bullet list
  | { tip: string }        // blue callout
  | { warning: string }    // amber callout

export interface ManualSection {
  id: string               // anchor id, identical across languages
  title: string
  blocks: ManualBlock[]
}

export interface ManualContent {
  title: string
  intro: string
  tocTitle: string
  sections: ManualSection[]
}

// The memo warning appears in two sections (person page and Google Contacts),
// so each language defines it once.
const MEMO_WARNING_EN =
  'Clearing a person\'s memo does NOT remove it from Google Contacts. The old text stays in the contact\'s Notes in Google. To remove it, delete it in Google Contacts as well, or replace the memo with new text instead of leaving it empty.'
const MEMO_WARNING_JA =
  '人物のメモを空にしても、Google Contacts からは削除されません。古い内容は Google の連絡先の「メモ」に残ります。削除したい場合は Google Contacts 側でも削除するか、空にせず新しい内容に書き換えてください。'
const MEMO_WARNING_ZH =
  '清除人物的備忘錄，並不會從 Google Contacts 中刪除。舊的內容仍會留在 Google 聯絡人的「備註」中。若要刪除，請同時在 Google Contacts 中刪除，或改為輸入新的內容，而不要留白。'

const en: ManualContent = {
  title: 'User Manual',
  intro: 'Card Manager scans business cards, reads them with AI, and keeps one record per person with all of their cards, contacts and organizations. This manual walks through every page.',
  tocTitle: 'Contents',
  sections: [
    {
      id: 'overview',
      title: '1. Overview',
      blocks: [
        { p: 'The top bar has five pages:' },
        { list: [
          'Collection — browse and search everything you have saved, by card or by person.',
          'Scan — upload photos of business cards and turn them into records.',
          'Settings — manage "Met As", Occasions and Countries.',
          'Export — send selected cards to Odoo or Google Contacts.',
          'Manual — this page.',
        ] },
        { p: 'The button at the top right switches the language: Japanese → English → Traditional Chinese.' },
        { p: 'Two ideas to keep in mind: a card is one physical business card (with its photos), and a person is one human being. A person can have many cards, many names (e.g. Chinese and English), and several organizations.' },
      ],
    },
    {
      id: 'scan',
      title: '2. Scanning cards',
      blocks: [
        { p: 'Scanning has four stages: Upload → Group → Analyze → Review.' },
        { steps: [
          'Open Scan and drop card photos onto the upload area, or click it to choose files. One photo may contain several cards.',
          'For a photo with several cards, click "Split Cards" (✂️) to cut it into one image per card. Every multi-card photo must be split before analysis. Use ↺ ↻ to rotate and "Crop image" to trim.',
          'Group the images into cards. Use "1 per card (single-sided)", "Pairs of 2 (double-sided)" or "Pair by position", or drag an image onto another card to pair them as front and back. "+ Add Group" adds an empty card.',
          'Make sure the side with the name and contact details is the Front. Use ⇅ Swap to change it.',
          'Click "Start Analysis →". Each card is read and its fields filled in.',
          'Check each card (see the next section), then click "Save N cards ✓".',
        ] },
        { tip: 'If analysis finds no card data, click "← Back to Grouping", split the photo, and analyze again.' },
      ],
    },
    {
      id: 'review',
      title: '3. Reviewing before you save',
      blocks: [
        { p: 'In the Review stage every card is shown with the fields that were read. Click any field to correct it.' },
        { list: [
          'Names, organizations, titles and contacts can all be edited. Use "+ Add Field", "+ Add Organization" and "+ Add name" for anything that was missed.',
          'Drag a contact between Personal Contacts and an organization\'s Work Contacts, or between organizations, to put it in the right place.',
          'Met As: the role you were in when you met (your own companies, set up in Settings).',
          'Occasion: the event or meeting where you received the card. "+ New Occasion" creates one on the spot.',
          'Received: the date you received the card.',
          'Notes: a note about this particular card.',
        ] },
        { p: 'If a card looks like someone already saved, an "Existing contact found" panel appears with the saved person on the left and the new card on the right:' },
        { list: [
          'Drag fields from the right into the left, then click "Confirm merge" — the card is added to the existing person.',
          '"Not a duplicate →" — save the card as a new, separate person.',
          '"Discard new card" — do not save this card.',
        ] },
        { p: 'The "Existing Person" line under the card shows which saved person the card will be added to. "✕ Create New" next to it saves the card as a new person instead. After "Confirm merge" that button is hidden.' },
        { warning: 'Cards in the same scan are never compared with each other. If two cards in one batch belong to the same person, save both, then merge the two people afterwards (see "Merging duplicate people").' },
      ],
    },
    {
      id: 'manual-entry',
      title: '4. Entering a person by hand',
      blocks: [
        { p: 'On Collection, click "+ Enter Manually" to type in a person without scanning a card. You get the same editor as the Review stage.' },
      ],
    },
    {
      id: 'collection',
      title: '5. Collection',
      blocks: [
        { p: 'Collection has two tabs:' },
        { list: [
          'Cards — every saved card, grouped by year and month. The search box finds cards by name, company or occasion. Click a card to open it.',
          'Persons — every person, grouped by country (taken from their home address, otherwise their work address). The search box finds people by name or company. Click a person to open their page.',
        ] },
        { tip: 'Merging happens on the Persons tab, not the Cards tab.' },
      ],
    },
    {
      id: 'person',
      title: '6. The person page',
      blocks: [
        { p: 'A person page collects everything about one person. Click any value to edit it; changes save immediately.' },
        { list: [
          'Personal: name variants (each language), personal contacts (mobile, personal email, LINE, …), birthday and the person\'s memo.',
          'Organization 1, 2, …: company names, titles and departments, the work contacts for that organization, and the organization memo.',
          '"+ Add Field" adds a contact. For an organization this includes the GUI Number (統一編號).',
          'Drag a work contact to another organization to move it there.',
          'The ✕ next to a name or an organization removes it. Removing an organization also removes its work contacts.',
          'Business Cards: every card belonging to this person.',
          '"Delete person" deletes the person and their details. Their business cards are not deleted.',
        ] },
        { p: 'Memos:' },
        { list: [
          'Memo (Personal section): a free-text note about the person. It is also added to the Notes of their Google contact.',
          'Organization memo: a note about the organization itself. It is shared — every person linked to that organization shows the same memo, and editing it on one page changes it for all of them. It stays in this app and is not sent to Google.',
          'A memo saves when you click outside the box.',
        ] },
        { warning: MEMO_WARNING_EN },
      ],
    },
    {
      id: 'merge',
      title: '7. Merging duplicate people',
      blocks: [
        { p: 'When the same person was saved twice, merge the two records into one.' },
        { steps: [
          'Go to Collection → Persons.',
          'Click "Select to merge" (next to the search box).',
          'Tick the people to merge. Searching for the name is the quickest way to find them.',
          'Click "Merge N persons" in the bar at the bottom.',
          'Choose the record to keep. Each option shows the person\'s names and organizations so you can tell them apart. Click "Next →".',
          'Click "Confirm Merge".',
        ] },
        { p: 'All cards, names, contacts, organizations and memos move to the record you kept, and each work contact stays with its own organization. The other records are deleted, and their Google contacts are removed so you do not end up with duplicates there.' },
        { warning: 'A merge cannot be undone. Afterwards, check the person page for repeated contacts (for example the same phone number written two ways) and delete the extras.' },
      ],
    },
    {
      id: 'card',
      title: '8. The card page',
      blocks: [
        { p: 'Click a card on the Cards tab to open it.' },
        { list: [
          'See each side. "Promote to Front" makes a side the front; "Add Image" adds another side.',
          'Change Met As, Occasion and Received date.',
          'Thumbnail name: choose which of the person\'s names is shown on the card thumbnail ("Auto" picks one for you).',
          '"View person" opens the person this card belongs to.',
          '"Delete this card" deletes the card. This cannot be undone.',
        ] },
      ],
    },
    {
      id: 'google',
      title: '9. Google Contacts',
      blocks: [
        { p: 'Each person is kept in step with a Google contact automatically:' },
        { list: [
          'Saving a scanned card, or editing a person, updates their Google contact in the background.',
          'Met As values become Google contact groups (labels).',
          'The contact\'s Notes contain the English title (if any), the card notes, and the person\'s memo.',
          'Organization memos are not sent to Google.',
          'A person with no business card is not sent to Google.',
          'When people are merged, the Google contacts of the removed records are deleted.',
        ] },
        { warning: MEMO_WARNING_EN },
      ],
    },
    {
      id: 'export',
      title: '10. Export',
      blocks: [
        { steps: [
          'Open Export.',
          'Narrow the list with search and the Year, Month, Date, Occasion, Met As and "Not yet exported" filters.',
          'Tick the cards to export (or "Select all").',
          'Click "Next: Choose destinations", pick Odoo and/or Google Contacts, and run the export.',
          'The result shows, for each card, whether it was created, updated or failed.',
        ] },
        { tip: 'A destination marked "Not configured" needs to be set up first.' },
      ],
    },
    {
      id: 'settings',
      title: '11. Settings',
      blocks: [
        { list: [
          'Met As: the roles or companies you represent when meeting people. They are offered when reviewing a card and become Google contact groups.',
          'Occasions: events and meetings. Deleting one keeps its name on existing cards as plain text, but those cards are no longer linked to it.',
          'Countries: the countries offered for addresses. A person\'s country also decides their group on the Persons tab.',
        ] },
      ],
    },
  ],
}

const ja: ManualContent = {
  title: 'ユーザーマニュアル',
  intro: '名片整理器は名刺をスキャンして AI で読み取り、人物ごとに名刺・連絡先・所属組織をまとめて管理します。このマニュアルでは各ページの使い方を説明します。',
  tocTitle: '目次',
  sections: [
    {
      id: 'overview',
      title: '1. 概要',
      blocks: [
        { p: '上部のバーには次のページがあります。' },
        { list: [
          'コレクション — 保存した名刺・人物の一覧と検索。',
          'スキャン — 名刺の写真をアップロードして登録します。',
          '設定 — 「対面時の立場」「場面」「国」を管理します。',
          'エクスポート — 選んだ名刺を Odoo や Google Contacts へ送ります。',
          'マニュアル — このページです。',
        ] },
        { p: '右上のボタンで表示言語を切り替えます（日本語 → English → 繁體中文）。' },
        { p: '「名刺」は物理的な 1 枚の名刺（写真を含む）、「人物」は 1 人の人を表します。1 人の人物は複数の名刺・複数の名前（例：中国語と英語）・複数の所属組織を持てます。' },
      ],
    },
    {
      id: 'scan',
      title: '2. 名刺のスキャン',
      blocks: [
        { p: 'スキャンは「アップロード → グループ → 解析 → 確認」の 4 段階です。' },
        { steps: [
          '「スキャン」を開き、名刺の写真をアップロード欄にドロップするか、クリックしてファイルを選びます。1 枚の写真に複数の名刺が写っていても構いません。',
          '複数の名刺が写った写真は「名刺を分割」（✂️）で 1 枚ずつに分けます。解析の前にすべて分割が必要です。↺ ↻ で回転、「画像を切り抜く」でトリミングできます。',
          '画像を名刺ごとにまとめます。「1枚ずつ（片面）」「2枚ペア（両面）」「位置でペア」を使うか、画像を別の名刺へドラッグして表裏のペアにします。「＋グループ追加」で空の名刺を追加できます。',
          '名前と連絡先が載っている面を表面にします。⇅ で表裏を入れ替えられます。',
          '「解析開始 →」をクリックすると、各名刺が読み取られ項目が入力されます。',
          '各名刺を確認し（次の章）、「N 枚を保存する ✓」をクリックします。',
        ] },
        { tip: '名刺データが見つからない場合は「← グループ編集に戻る」で写真を分割し、もう一度解析してください。' },
      ],
    },
    {
      id: 'review',
      title: '3. 保存前の確認',
      blocks: [
        { p: '「確認」段階では、読み取った内容が名刺ごとに表示されます。項目をクリックすると修正できます。' },
        { list: [
          '名前・組織・役職・連絡先はすべて編集できます。足りないものは「＋ フィールド追加」などで追加します。',
          '連絡先を「個人連絡先」と組織の「勤務先連絡先」の間、または組織同士の間でドラッグして、正しい場所へ移せます。',
          '対面時の立場：会ったときの自分の立場（設定で登録した自社など）。',
          '場面：名刺を受け取ったイベントや会合。その場で新しい場面も作れます。',
          '受取日：名刺を受け取った日付。',
          'メモ：この名刺についてのメモ。',
        ] },
        { p: '既に登録済みの人物と似ている場合は、左に登録済みの人物、右に新しい名刺を並べたパネルが表示されます。' },
        { list: [
          '右の項目を左へドラッグしてから「マージして保存」— 既存の人物に名刺を追加します。',
          '「別人として保存 →」— 別の新しい人物として保存します。',
          '「新しい名刺を破棄」— この名刺を保存しません。',
        ] },
        { p: '名刺の下の「既存人物」は、名刺が追加される登録済みの人物です。隣の「✕ 新規作成」を押すと新しい人物として保存されます。「マージして保存」の後はこのボタンは表示されません。' },
        { warning: '同じスキャン内の名刺同士は照合されません。1 回のスキャンに同じ人物の名刺が 2 枚ある場合は、両方を保存してから人物をマージしてください（「重複した人物のマージ」参照）。' },
      ],
    },
    {
      id: 'manual-entry',
      title: '4. 手動で人物を登録する',
      blocks: [
        { p: 'コレクションの「＋ 手動入力」から、名刺をスキャンせずに人物を入力できます。編集画面は「確認」段階と同じです。' },
      ],
    },
    {
      id: 'collection',
      title: '5. コレクション',
      blocks: [
        { p: 'コレクションには 2 つのタブがあります。' },
        { list: [
          '名刺 — 保存したすべての名刺を年・月ごとに表示します。名前・会社・場面で検索できます。名刺をクリックすると詳細が開きます。',
          '人物 — すべての人物を国ごと（自宅住所、なければ勤務先住所の国）に表示します。名前・会社で検索できます。人物をクリックすると人物ページが開きます。',
        ] },
        { tip: 'マージは「名刺」タブではなく「人物」タブで行います。' },
      ],
    },
    {
      id: 'person',
      title: '6. 人物ページ',
      blocks: [
        { p: '人物ページには 1 人の情報がまとまっています。値をクリックすると編集でき、変更はすぐに保存されます。' },
        { list: [
          '個人：言語ごとの名前、個人連絡先（携帯・個人メール・LINE など）、誕生日、人物のメモ。',
          '組織 1、2…：会社名・役職・部署、その組織の勤務先連絡先、組織メモ。',
          '「＋ フィールド追加」で連絡先を追加します。組織では「統一編號」（GUI Number）も追加できます。',
          '勤務先連絡先を別の組織へドラッグすると、その組織へ移動します。',
          '名前や組織の ✕ で削除できます。組織を削除するとその勤務先連絡先も削除されます。',
          '名刺：この人物のすべての名刺。',
          '「人物を削除」で人物と詳細を削除します。名刺は削除されません。',
        ] },
        { p: 'メモについて：' },
        { list: [
          'メモ（個人欄）：人物についての自由記述。Google の連絡先の「メモ」にも追加されます。',
          '組織メモ：組織そのものについてのメモ。共有されるため、その組織に所属する全員に同じメモが表示され、どのページで編集しても全員に反映されます。このアプリ内だけで使われ、Google には送られません。',
          'メモは入力欄の外をクリックすると保存されます。',
        ] },
        { warning: MEMO_WARNING_JA },
      ],
    },
    {
      id: 'merge',
      title: '7. 重複した人物のマージ',
      blocks: [
        { p: '同じ人物が 2 回登録されたときは、1 つにまとめます。' },
        { steps: [
          'コレクション → 「人物」タブを開きます。',
          '検索欄の横の「選択してマージ」をクリックします。',
          'マージする人物にチェックを入れます。名前で検索すると見つけやすくなります。',
          '下のバーの「N 件をマージ」をクリックします。',
          '残すレコードを選びます。各候補に名前と所属組織が表示されるので区別できます。「Next →」をクリックします。',
          '「マージする」をクリックします。',
        ] },
        { p: '名刺・名前・連絡先・組織・メモはすべて残したレコードへ移り、勤務先連絡先はそれぞれ元の組織に付いたままです。他のレコードは削除され、その Google の連絡先も削除されるので、Google 側で重複しません。' },
        { warning: 'マージは元に戻せません。マージ後は人物ページで重複した連絡先（同じ電話番号の表記違いなど）を確認し、不要なものを削除してください。' },
      ],
    },
    {
      id: 'card',
      title: '8. 名刺ページ',
      blocks: [
        { p: '「名刺」タブで名刺をクリックすると開きます。' },
        { list: [
          '各面を確認できます。「表面に設定」で表面を変更、「画像追加」で面を追加します。',
          '対面時の立場・場面・受取日を変更できます。',
          'サムネイル表示名：名刺のサムネイルに表示する名前を選びます（「自動」で自動選択）。',
          '「人物を見る」で、この名刺の人物ページへ移動します。',
          '「この名刺を削除」で名刺を削除します。元に戻せません。',
        ] },
      ],
    },
    {
      id: 'google',
      title: '9. Google Contacts',
      blocks: [
        { p: '人物は Google の連絡先と自動で同期されます。' },
        { list: [
          'スキャンした名刺の保存や人物の編集をすると、Google の連絡先がバックグラウンドで更新されます。',
          '「対面時の立場」は Google の連絡先グループ（ラベル）になります。',
          '連絡先の「メモ」には、英語の役職（あれば）、名刺のメモ、人物のメモが入ります。',
          '組織メモは Google に送られません。',
          '名刺が 1 枚もない人物は Google に送られません。',
          '人物をマージすると、削除されたレコードの Google の連絡先も削除されます。',
        ] },
        { warning: MEMO_WARNING_JA },
      ],
    },
    {
      id: 'export',
      title: '10. エクスポート',
      blocks: [
        { steps: [
          '「エクスポート」を開きます。',
          '検索や、年・月・日付・場面・対面時の立場・「未エクスポートのみ」のフィルターで絞り込みます。',
          'エクスポートする名刺にチェックを入れます（全選択も可）。',
          '次へ進み、Odoo と Google Contacts のどちら（または両方）に送るかを選んで実行します。',
          '結果に名刺ごとの作成・更新・エラーが表示されます。',
        ] },
        { tip: '「未設定」と表示された送信先は、先に設定が必要です。' },
      ],
    },
    {
      id: 'settings',
      title: '11. 設定',
      blocks: [
        { list: [
          '対面時の立場：人と会うときの自分の立場や会社。名刺の確認時に選択でき、Google の連絡先グループになります。',
          '場面：イベントや会合。削除しても既存の名刺には名前が文字として残りますが、場面とのつながりはなくなります。',
          '国：住所で選べる国。人物の国は「人物」タブのグループ分けにも使われます。',
        ] },
      ],
    },
  ],
}

const zhTW: ManualContent = {
  title: '使用手冊',
  intro: '名片整理器會掃描名片並以 AI 辨識，依人物整理所有名片、聯絡方式與所屬組織。本手冊依頁面說明各項功能。',
  tocTitle: '目錄',
  sections: [
    {
      id: 'overview',
      title: '1. 概要',
      blocks: [
        { p: '上方列有以下頁面：' },
        { list: [
          '名片庫 — 瀏覽與搜尋已儲存的名片與人物。',
          '掃描 — 上傳名片照片並建立資料。',
          '設定 — 管理「見面身分」、「場合」與「國家」。',
          '匯出 — 將選取的名片傳送到 Odoo 或 Google Contacts。',
          '手冊 — 本頁。',
        ] },
        { p: '右上角的按鈕可切換介面語言（日本語 → English → 繁體中文）。' },
        { p: '「名片」是一張實體名片（含照片），「人物」是一個人。一個人物可以有多張名片、多個名字（例如中文與英文）及多個所屬組織。' },
      ],
    },
    {
      id: 'scan',
      title: '2. 掃描名片',
      blocks: [
        { p: '掃描分為四個階段：上傳 → 分組 → 解析 → 確認。' },
        { steps: [
          '開啟「掃描」，將名片照片拖放到上傳區，或點擊以選擇檔案。一張照片中可以有多張名片。',
          '含有多張名片的照片，請點擊「分割名片」（✂️）切成每張一個影像。解析前必須全部分割。可用 ↺ ↻ 旋轉，用「裁切影像」修剪。',
          '將影像依名片分組。可使用「每張一組（單面）」、「兩張一組（雙面）」或「依位置配對」，或將影像拖到另一張名片上配成正反面。「＋ 新增群組」可新增空白名片。',
          '請將印有姓名與聯絡方式的一面設為正面。可用 ⇅ 交換正反面。',
          '點擊「開始解析 →」，系統會辨識每張名片並填入欄位。',
          '確認每張名片（見下一章）後，點擊「儲存 N 張名片 ✓」。',
        ] },
        { tip: '若找不到名片資料，請點擊「← 返回分組」，分割照片後再解析一次。' },
      ],
    },
    {
      id: 'review',
      title: '3. 儲存前的確認',
      blocks: [
        { p: '在「確認」階段，每張名片都會顯示辨識出的內容。點擊任一欄位即可修改。' },
        { list: [
          '姓名、組織、職稱與聯絡方式都可以編輯。遺漏的內容可用「＋ 新增欄位」等按鈕補上。',
          '可將聯絡方式在「個人聯絡方式」與組織的「工作聯絡方式」之間，或在組織之間拖曳，放到正確的位置。',
          '見面身分：見面時您的身分（在設定中登錄的自家公司等）。',
          '場合：收到名片的活動或會議，也可以當場新增。',
          '收到日期：收到名片的日期。',
          '備註：關於這張名片的備註。',
        ] },
        { p: '若名片與已儲存的人物相似，會出現左側為既有人物、右側為新名片的面板：' },
        { list: [
          '將右側欄位拖到左側後點擊「確認合併」— 名片會加到既有人物。',
          '「並非重複 →」— 儲存為另一個新人物。',
          '「捨棄新名片」— 不儲存這張名片。',
        ] },
        { p: '名片下方的「既有人物」表示名片將加入的已儲存人物。點擊旁邊的「✕ 建立新的」則會儲存為新人物。「確認合併」後此按鈕會隱藏。' },
        { warning: '同一次掃描中的名片彼此不會比對。若同一批中有兩張名片屬於同一人，請先全部儲存，再合併這兩個人物（見「合併重複的人物」）。' },
      ],
    },
    {
      id: 'manual-entry',
      title: '4. 手動輸入人物',
      blocks: [
        { p: '在名片庫點擊「＋ 手動輸入」，不需掃描名片即可輸入人物。編輯畫面與「確認」階段相同。' },
      ],
    },
    {
      id: 'collection',
      title: '5. 名片庫',
      blocks: [
        { p: '名片庫有兩個分頁：' },
        { list: [
          '名片 — 依年、月顯示所有已儲存的名片。可依姓名、公司或場合搜尋。點擊名片即可開啟。',
          '人物 — 依國家（住家地址，若無則為工作地址的國家）顯示所有人物。可依姓名或公司搜尋。點擊人物即可開啟人物頁面。',
        ] },
        { tip: '合併請在「人物」分頁進行，而非「名片」分頁。' },
      ],
    },
    {
      id: 'person',
      title: '6. 人物頁面',
      blocks: [
        { p: '人物頁面彙整了一個人的所有資料。點擊任一值即可編輯，變更會立即儲存。' },
        { list: [
          '個人：各語言的姓名、個人聯絡方式（手機、個人電子郵件、LINE 等）、生日與人物備忘錄。',
          '組織 1、2…：公司名稱、職稱與部門、該組織的工作聯絡方式，以及組織備忘錄。',
          '「＋ 新增欄位」可新增聯絡方式。組織中也可新增「統一編號」。',
          '將工作聯絡方式拖到另一個組織，即可移到該組織。',
          '姓名或組織旁的 ✕ 可刪除。刪除組織時，其工作聯絡方式也會一併刪除。',
          '名片：此人物的所有名片。',
          '「刪除人物」會刪除人物與其資料，但不會刪除名片。',
        ] },
        { p: '關於備忘錄：' },
        { list: [
          '備忘錄（個人區）：關於此人物的自由文字，也會加到 Google 聯絡人的「備註」中。',
          '組織備忘錄：關於組織本身的備忘錄。此備忘錄為共用 — 該組織的所有人都會顯示相同內容，在任一頁面編輯都會同步變更。僅保存在本應用程式中，不會傳送到 Google。',
          '在輸入框外點擊即會儲存備忘錄。',
        ] },
        { warning: MEMO_WARNING_ZH },
      ],
    },
    {
      id: 'merge',
      title: '7. 合併重複的人物',
      blocks: [
        { p: '同一個人被儲存兩次時，可將兩筆資料合併為一筆。' },
        { steps: [
          '前往名片庫 →「人物」分頁。',
          '點擊搜尋框旁的「選取以合併」。',
          '勾選要合併的人物。以姓名搜尋最容易找到。',
          '點擊下方列的「合併 N 筆」。',
          '選擇要保留的資料。每個選項都會顯示姓名與所屬組織，方便分辨。點擊「Next →」。',
          '點擊「確認合併」。',
        ] },
        { p: '所有名片、姓名、聯絡方式、組織與備忘錄都會移到保留的資料，工作聯絡方式仍會跟著原本的組織。其他資料會被刪除，其 Google 聯絡人也會一併刪除，Google 中不會留下重複資料。' },
        { warning: '合併後無法復原。合併後請在人物頁面檢查重複的聯絡方式（例如同一電話號碼的不同寫法），並刪除多餘的部分。' },
      ],
    },
    {
      id: 'card',
      title: '8. 名片頁面',
      blocks: [
        { p: '在「名片」分頁點擊名片即可開啟。' },
        { list: [
          '可查看每一面。「設為正面」可變更正面，「新增影像」可新增一面。',
          '可變更見面身分、場合與收到日期。',
          '縮圖顯示名稱：選擇名片縮圖上要顯示的姓名（也可選「自動」）。',
          '「查看人物」可前往此名片所屬的人物頁面。',
          '「刪除此名片」會刪除名片，且無法復原。',
        ] },
      ],
    },
    {
      id: 'google',
      title: '9. Google Contacts',
      blocks: [
        { p: '人物會自動與 Google 聯絡人同步：' },
        { list: [
          '儲存掃描的名片或編輯人物時，Google 聯絡人會在背景更新。',
          '「見面身分」會成為 Google 聯絡人群組（標籤）。',
          '聯絡人的「備註」包含英文職稱（若有）、名片備註與人物備忘錄。',
          '組織備忘錄不會傳送到 Google。',
          '沒有任何名片的人物不會傳送到 Google。',
          '合併人物時，被刪除資料的 Google 聯絡人也會一併刪除。',
        ] },
        { warning: MEMO_WARNING_ZH },
      ],
    },
    {
      id: 'export',
      title: '10. 匯出',
      blocks: [
        { steps: [
          '開啟「匯出」。',
          '用搜尋以及年、月、日期、場合、見面身分、「僅顯示未匯出」等篩選條件縮小範圍。',
          '勾選要匯出的名片（也可全選）。',
          '前往下一步，選擇 Odoo 和／或 Google Contacts，然後執行匯出。',
          '結果會顯示每張名片的建立、更新或錯誤狀態。',
        ] },
        { tip: '標示「尚未設定」的匯出目的地需要先完成設定。' },
      ],
    },
    {
      id: 'settings',
      title: '11. 設定',
      blocks: [
        { list: [
          '見面身分：與人見面時您所代表的身分或公司。確認名片時可選擇，並會成為 Google 聯絡人群組。',
          '場合：活動與會議。刪除後，既有名片仍會以文字保留其名稱，但不再與場合連結。',
          '國家：地址可選擇的國家。人物的國家也決定其在「人物」分頁中的分組。',
        ] },
      ],
    },
  ],
}

export const manualContent: Record<Lang, ManualContent> = { ja, en, 'zh-TW': zhTW }
