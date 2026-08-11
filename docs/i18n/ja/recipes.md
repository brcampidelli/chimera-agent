---
source_sha256: a88090fec9fcabd118b65cf8d40ddecefd47fbb2da49dfa195a66fd57e85c4c1
---

# レシピ

組み込みツールでエンドツーエンドに何か有用なことをする、実際に動くワークフローです。どれでも `chimera workflow <file> -w <workspace>` で実行できます。完全なソースは[`examples/`](https://github.com/brcampidelli/chimera-agent/tree/main/examples)フォルダにあります。

## 完全にローカル、APIキー不要(Ollama)

自分のマシン上のモデルに対してChimeraを実行します — キー不要、何もマシンの外に出ません。[Ollama](https://ollama.com)をインストールし、モデルをpullして、Chimeraをそれに向けます。

```bash
ollama pull llama3.1                     # or qwen2.5, mistral, phi3, …
export CHIMERA_MODEL=ollama/llama3.1     # the `ollama/` prefix = local, keyless
chimera run "Summarise this file in 3 bullets" -w .
```

それだけです — `OPENROUTER_API_KEY` もクラウドも不要です。認証情報ゲートは `ollama/…`(および `ollama_chat/…`)をローカルランタイムとして認識し、通過させます。Ollamaを別の場所で実行している場合は、`CHIMERA_OLLAMA_BASE_URL=http://host:11434` を設定してください(デフォルトは `http://localhost:11434`)。

ローカルモデルは小さいため、これは[goldilocks](../bench/local_lift/RESULTS.md)レンジの*弱い*側です — `chimera solve`(計画+検証または差し戻しが弱いモデルを助けます)やオフラインでのプライバシーには適していますが、一発勝負のフロンティア級推論にはあまり向きません。組み合わせてください: ローカルをデフォルトにしつつ、難しい呼び出し用にクラウドの `CHIMERA_FALLBACK_MODELS` を設定するといった具合です。

## メールトリアージ

受信箱を読み、`URGENT / PERSONAL / NEWSLETTER / COLD-SALES` に分類し、10秒で読めるダイジェストを書きます。読み取り専用です — 何も削除・移動・送信されません。

```bash
chimera workflow examples/email_triage/triage.yaml -w ./triage_workspace
```

IMAP認証情報が必要です。セットアップと日次スケジューリング:
[examples/email_triage/README.md](https://github.com/brcampidelli/chimera-agent/blob/main/examples/email_triage/README.md)。

## 日次リサーチブリーフ

トピックを入力すると、出典付きの5項目ブリーフ+3行ダイジェストが出力されます(arxivは常時、Tavilyキーが設定されていればウェブ検索も)。

```bash
chimera workflow examples/research_brief/brief.yaml -w ./brief_workspace
```

## リポジトリウォッチドッグ

リポジトリのテストスイートを実行し、失敗したテストを名指しするヘルスレポートを書きます。レポート以外は読み取り専用です。

```bash
chimera workflow examples/repo_watchdog/watch.yaml -w /path/to/your/repo
```

## ドキュメントを読む(PDF、DOCX、XLSXなど)

エージェントは標準でプレーンテキストを読みます。実際のドキュメント — PDF、Word、PowerPoint、Excel、HTML、CSV、EPUB — については、オプションのエクストラをインストールすると、それらすべてをMarkdownに変換する `read_document` ツールが手に入ります。

```bash
uv sync --extra documents      # or: pip install 'chimera-agent[documents]'
```

そして、タスクをファイルに向けてください: *「report.pdfを5項目に要約して」*。エクストラなしでは、`read_document` は失敗する代わりに1行のインストールヒントを返します。

## ウェブを閲覧する(ナビゲート、読み取り+操作)

`browser` ツールは**組み込み**です — 実際のChromiumを駆動するため(プレーンな `http_get` では見えないJavaScriptでレンダリングされたページも見えます)。PlaywrightはChimeraに同梱されており、約150MBのChromiumバイナリは**ブラウザを初めて使う時に自動的にダウンロード**されます(pipではできない一度限りのステップです)。インストール手順は不要です。

```bash
# nothing to install — just use it. To turn the auto-download off and fetch it yourself:
#   CHIMERA_BROWSER_AUTO_INSTALL=0  +  playwright install chromium
# For clean Markdown out of read_text (instead of plain text), also add the documents extra:
uv sync --extra documents        # or: pip install 'chimera-agent[documents]'
```

`browser` ツールには以下のアクションがあります。

- **`navigate` / `read`** — URLを開き、ページの*インタラクティブな*要素を `[ref] role: name` として一覧表示します(リンク、ボタン、フィールド)。これにより、エージェントはピクセルではなく `ref` によってクリック/入力します。
- **`read_text`** — ページの**完全なレンダリング済みテキスト**。記事やドキュメント、検索結果の読み取り/リサーチ用です。`documents` エクストラがあれば、クリーンな**Markdown**(見出し、リンク、リストがMarkItDownによって保持されます)になります。なければプレーンな可視テキストです。オプションの `url` を渡すと、開くことと読むことを1ステップで行えます。
- **`find`** — レンダリングされたテキストをクエリで検索し、一致した行を返します。
- **`click` / `type` / `back`** — `ref` によってページを操作します。

`CHIMERA_BROWSER_HEADLESS=false` はデバッグ用にChromiumをヘッドフルで実行します。

ページのコンテンツは**信頼できません**: すべての結果はデータフェンスされ、ツールは実行を汚染するため、閲覧時には `solve --taint --guard` を優先し、生のページテキストに対して行動するのではなく、検疫リーダーを通じて構造化されたフィールドを取り出してください。`documents` エクストラがなくても `read_text` は動作します — Markdownではなくプレーンテキストになるだけです。

## トピックをリサーチする(検索+読み取り)

ウェブ検索とブラウザの `read_text` を組み合わせて何かをリサーチし、出典付きのブリーフを得ます — `web_search`(`CHIMERA_TAVILY_API_KEY` が必要)がページを見つけ、`browser read_text` が(JS重視のサイトを含め)それぞれを読み、`deliver` がブリーフを書きます。

```bash
uv run chimera solve "Research 'on-device small language models 2026': web_search for sources, \
  open the top 3 with the browser and read_text each, then write a 5-bullet sourced brief to brief.md" \
  --taint --verify "test -s brief.md"
```

各ステップに実行可能なチェックが付いた既製バージョンについては、[`examples/research_brief`](https://github.com/brcampidelli/chimera-agent/tree/main/examples/research_brief)を参照してください — 標準で `arxiv_search` + `web_search` を使用し、`browser` エクストラをインストールすれば、エージェントは検索スニペットで止まらず、フルページを `read_text` することもできます。

## スクレイピングと安全な構造化抽出

2つの組み込みツールが、どんなページもクリーンでLLMにすぐ使えるデータに変えます — 追加インストールは不要です。

- **`scrape`** — URLを取得し、クリーンな**Markdown+メタデータ**を返します。これはコスト意識型のカスケードを辿ります: 最初はプレーンなHTTP GET、ページが空で返ってきた場合は組み込みの**browser**(JSレンダリング)へエスカレート、そして — `FIRECRAWL_API_KEY` を設定した場合*のみ* — 重いアンチボット対策のページには**Firecrawl**にフォールバックします。`render=http|browser|firecrawl` は特定のバックエンドを強制します。`include_links` はページのリンクも返します。
- **`extract`** — 特定のフィールドを**検証済みJSON**として、安全に取り出します。`url`(または `content`)と `fields` のリスト(例: `["title", "price", "author"]`)を与えると、*それらのフィールドだけ*を返します。重要なのは、これがChimeraの**検疫リーダー**を通じてページを読むことです — 出力がスキーマ検証された、ツールを持たないモデルです — そのため、**ページに隠された指示がエージェントを乗っ取ることはできません**。これはFirecrawl/ScrapeGraphAIが与えてくれない安全保証です: 悪意のあるページが最悪でも返せるのは間違った値であり、新たな指示ではありません。大きなページはチャンク化されマージされ、すべてのフィールドが埋まった時点で早期に停止してコストを抑えます。**既知のページテンプレート**については、`selectors`(フィールド → CSS、例: `{"price": ".price", "link": "a.more::attr(href)"}`)を渡せば、それらのフィールドは**決定論的に抽出されます — 無料でLLM不要**。安全なLLMは、セレクターが埋めなかったフィールドにのみ使われます。

```bash
uv run chimera run "scrape https://news.ycombinator.com and summarize the top 5 stories"
uv run chimera run "extract the fields title, price, availability from https://example.com/product --taint"
```

サイト全体については、さらに2つの動詞があります。

- **`map`** — サイトのURLを安価に一覧表示します(サイトマップがあればそれを読み、なければページのリンクをスキャンします)。オプションの `search` キーワードフィルターがあります。クロールする前にサイトの範囲を把握するために実行してください。
- **`crawl`** — シードURLからリンクを辿り、各ページのクリーンなMarkdownを返します。`limit` と `max_depth` によって制限され、デフォルトでは同一ドメインのみで、**robots.txtに準拠**します(`Disallow` と `Crawl-delay` に従います)。`include`/`exclude` はURLのglobパターンです。長いクロールは**再開可能**です: フロンティアはページごとにディスクへチェックポイントされるため、ページNで中断されたクロールは次回の実行でN+1から続行されます(デフォルトで `resume=true`)。

```bash
uv run chimera run "map https://docs.example.com then crawl the /guide section (max 20 pages) and summarize it"
```

すべてはデータフェンスされ、実行を汚染します(信頼できないウェブコンテンツのため)。そのため `solve --taint --guard` がそれに対して行動する安全な方法です。オプションのFirecrawlフォールバックは、組み込みエンジンがページを取得できず、かつキーが設定されている場合*のみ*使われます — Chimeraはウェブの大部分を、外部サービスなしに自力でスクレイピングします。

## 音声: 音声からテキストへ(文字起こし)

Chimeraは音声をテキストに変換できます — 画像生成やテキスト読み上げツールと対をなす存在です。これは**Whisperモデルをオーケストレーションします**(訓練はしません): `transcribe_audio` ツールは、`stt` エクストラをインストールしていればローカルの**faster-whisper**を使い(オフライン/プライベート)、そうでなければホスト型のOpenAI Whisper API(OpenAIキーが必要)を使います。

```bash
uv sync --extra stt      # optional: local, offline transcription (heavier — downloads a model)
uv run chimera run "transcribe meeting.m4a and give me 5 bullet-point action items"
```

> このプロジェクトの正直な精神に則ったスコープについての注記: Chimeraは**エージェント**であり、モデルではありません。APIを呼ぶかコードサンドボックスでライブラリを実行することで、音声からテキストへの変換、画像生成、コンピュータビジョン、古典的なMLを*使う*ことはできますが、Whisper、Stable Diffusion、PyTorch、OpenCVを*再実装*することはしません(そして分別を持ってそうすることもできません)。データサイエンス/MLについては、`execute_code` サンドボックスがすでに、エージェントがscikit-learn、pandas、OpenCVなどに対してPythonを書いて実行することを可能にしています。オーケストレーションはエージェントを増強します。再実装は遅いコピーを生むだけです。

## 動画またはその音声をダウンロードする

`download_media` ツールは、YouTubeおよびその他1000以上のサイトから動画(またはその音声だけ)をワークスペースに取り込みます。これは**yt-dlp**(活発にメンテナンスされ、pytubeのような単一サイト向けスクレイパーを沈める暗号/フォーマット/年齢制限の変動を処理する)をラップします。オプトインであり、音声抽出にはPATH上に `ffmpeg` も必要です。

```bash
uv sync --extra media-dl
uv run chimera run "download the audio of https://youtu.be/… then transcribe it and summarize"
```

上記の `transcribe_audio` と自然に組み合わさります: ダウンロード → 文字起こし → 要約、すべて1回の実行で。

## データ分析 / ML(`data_analysis` スキル)

Chimeraはscikit-learnを再実装しません — `execute_code` サンドボックスで**正しいpandas/sklearnコードを書いて実行します**。`data_analysis` スキルはそのケーパビリティに名前を付けたものです: タスクとデータセットを与えると、それ自体で完結するスクリプト(読み込み → 探索 → モデリング → 評価)を出力し、それをエージェントが実行します。

```bash
uv sync --extra data     # pandas + scikit-learn for the generated code
uv run chimera run "use the data_analysis skill: predict churn from customers.csv and report accuracy"
```

## 画像生成(ホスト型または完全ローカル)

`generate_image` はデフォルトでOpenAIの画像APIを使用します。**オフライン/プライベート**なセットアップには、`CHIMERA_IMAGE_BACKEND=local` を設定し、(重い、GPU依存の)`imagegen-local` エクストラをインストールしてください — Chimeraはその後、`diffusers` 経由でローカルに**FLUX.1-schnell**(Apache-2.0)を実行します。`auto`(デフォルト)は、OpenAIキーが存在しない場合にのみローカルを使います。

```bash
uv sync --extra imagegen-local     # pulls torch + diffusers; downloads multi-GB weights on first use
CHIMERA_IMAGE_BACKEND=local uv run chimera run "generate an image of a fox in a snowy forest"
```

> 上記と同じ正直なスコープです: Chimeraはここで拡散モデルを*実行します*が、訓練はしません。動画生成(CogVideoなど)は意図的に組み込まれ**ていません** — それはヘビー級の訓練済みモデルであり、エージェントがベースに抱えるべきものではありません。必要な場合はホスト型APIを利用してください。コンピュータビジョン(OpenCV)は専用のツールを必要としません — エージェントはすでにコードサンドボックスで `import cv2` を行えます。

## チャートとデータ可視化

チャートを作るための2つの補完的な方法です — どちらもスコープについて正直です(Chimeraはプロッティングライブラリを*使い*、matplotlib/plotly/bokehを再実装することはありません)。

**1. `data_visualization` スキル — チャートのコードを書き、サンドボックスで実行する。** *あらゆること*(カスタム/出版品質の図、3D、その他何でも)をカバーします: このスキルは、ヘッドレスバックエンド(`matplotlib.use("Agg")`)とワークスペースへの保存の作法を組み込んだ、matplotlib/seaborn(静的なPNG/SVG)またはplotly(インタラクティブなHTML)を使うそれ自体で完結するスクリプトを出力します。

```bash
uv sync --extra viz     # matplotlib + seaborn + plotly for the generated code
uv run chimera run "use data_visualization: line chart of revenue.csv over time, save revenue.png"
```

**2. `render_chart` ツール — 安全で宣言的なVega-Liteの仕様。** Vega-Liteの仕様は**コードではなく、不活性なJSONデータ**です: 検査可能で、スキーマ形状を持ち、再レンダリング可能です — Vega-Liteがカバーする標準的なチャート(棒/線/散布図/ヒストグラム/ヒートマップ/ファセット表示など)については、生成されたコードを実行するよりも強力なガバナンスの物語です。**HTML出力はエクストラ不要**です(仕様+Vega CDNを埋め込みます)。PNG/SVGはオプションの `viz-vega` エクストラ(`vl-convert-python`)を使います。

```bash
uv run chimera run "build a Vega-Lite bar chart of {A:5,B:8,C:3} and render_chart it to chart.html"
uv sync --extra viz-vega   # optional: static PNG/SVG rendering (heavy — Rust+V8 binary)
```

> 正直なスコープ: plotlyはplotly.jsをラップし、bokehは約半分がTypeScriptで、matplotlibのレンダラーはC++で、seabornはmatplotlibの上の薄い層です — すべて、エージェントが*呼び出す*べきものであり、書き直すべきものではありません。コードサンドボックスはすでにそれらをインポートしています。このスキルはそのケーパビリティに名前を付け、ヘッドレスに関する落とし穴を処理するだけです。Vega-Liteは、その成果物が安全な宣言的データであるため、専用のツールに値する例外です。

## それらをスケジュールする

どのレシピもcronで実行し、チャットに配信できます。

```bash
chimera cron add "morning brief" "0 7 * * *" "Research X; write a 5-bullet brief."
chimera serve   # runs jobs; with a bot configured, delivers to Discord/Telegram/Slack
```

メッセージングゲートウェイと24時間365日のセットアップについては、[デプロイ](deploy.md)を参照してください。
