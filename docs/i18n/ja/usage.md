---
source_sha256: 526c672c483707638cf41fadd79070ec382735ca905ac0c6534720fca5c4c3ee
---

# Chimera — 利用ガイド

ChimeraはCLIファーストで、自己進化するエージェントであり、LLM-Fusion推論コアを持ちます。
このガイドではインストール、設定、そしてすべてのコマンドを例とともに扱います。

> このプロジェクトが初めてですか? まず[アーキテクチャ概要](architecture.md)をお読みください。

---

## インストール

Chimeraは[uv](https://docs.astral.sh/uv/)を使用します。

```bash
git clone https://github.com/brcampidelli/chimera-agent
cd chimera-agent
uv sync --extra dev      # install runtime + dev deps
uv run chimera --help    # verify the CLI
```

以下のすべてのコマンドは `uv run chimera <command>` として実行されます(プロジェクトの
virtualenvがPATHに入っていれば、単に `chimera …` でも構いません)。

---

## 設定する

Chimeraは[LiteLLM](https://docs.litellm.ai/)を介してプロバイダーに依存しません。キーと
モデルの選択をローカルの `.env`(git管理外です — 決してコミットしないでください)に置きます。

```dotenv
# At least one provider key. OpenRouter unlocks 100+ models behind one key.
OPENROUTER_API_KEY=sk-or-...
# OPENAI_API_KEY=...
# ANTHROPIC_API_KEY=...

# Tier-1/2 default model (single, cheap, must support tool-calling for Tier-2)
CHIMERA_DEFAULT_MODEL=openrouter/deepseek/deepseek-chat-v3.1

# LLM-Fusion: a diverse panel -> judge -> synthesizer
CHIMERA_FUSION_PANEL=openrouter/deepseek/deepseek-chat-v3.1,openrouter/openai/gpt-4o-mini,openrouter/meta-llama/llama-3.3-70b-instruct
CHIMERA_FUSION_JUDGE=openrouter/deepseek/deepseek-chat-v3.1
CHIMERA_FUSION_SYNTHESIZER=openrouter/openai/gpt-4o-mini
```

その他のつまみ: `CHIMERA_HOME`(状態ディレクトリ、デフォルト `.chimera`)、`CHIMERA_LOG_LEVEL`
(`INFO` / `DEBUG`)、`CHIMERA_CACHE`(`on`/`off`、デフォルトはoff — ツール不要な同一の
completionをキャッシュし、繰り返しのAPI呼び出しを省きます)、そして `CHIMERA_AUTO_FUSE`
(`on`/`off`、デフォルトはoff — 明示的な `--fuse` なしに、`solve`/`crew` 内の深い、または
**エラーに敏感な**ターンを自動的にフュージョンします。コスト意識型ルーターは、それでも安価な/
ツールを使うターンは単一モデルのままにします)。ルーターはプロジェクトの主要言語
(en/pt/es/de/fr/zh/ja)における厳密解を求めるプロンプト(算術、カウント、桁の操作)を認識するため、
重要な短いステップは、長さのゲートを引っかけるには短すぎる場合でもフュージョンの保護を受けます。

**プロバイダー、フェイルオーバー、セルフホスト。** LiteLLMの `provider/model` スラッグは
どれでも動作します(`openai/…`、`anthropic/…`、`gemini/…`、`ollama_chat/…`、`openrouter/…`
など)。セルフホスト/OpenAI互換サーバー(Ollama、vLLM)には `CHIMERA_API_BASE` を設定して
ください(例: `CHIMERA_DEFAULT_MODEL=ollama_chat/llama3` とともに `http://127.0.0.1:11434`)。
`ollama/` ではなく `ollama_chat/` を使ってください: `ollama/` プレフィックスは Ollama の generate エンドポイントを経由するため、ツールを呼び出せません。
プライマリがエラーになった場合に別のモデルへフェイルオーバーするには `CHIMERA_FALLBACK_MODELS`
(カンマ区切り)を設定してください。`chat`/`tui` では、`/model <slug>` がセッション途中で
モデルを切り替えます。

**認証情報プール。** `CHIMERA_<PROVIDER>_KEYS`(例: `CHIMERA_OPENROUTER_KEYS=key1,key2,key3`)
でプロバイダーに複数のキーを与えられます。ゲートウェイは呼び出しをまたいでラウンドロビンで
それらをローテーションし(負荷/レート制限の分散)、1回の呼び出し内でも、あるキーがエラーに
なれば次のキーにフェイルオーバーします。プールはそのプロバイダーの単一の `*_API_KEY` を
置き換えます。*(OAuth/サブスクリプションログイン — Copilot、Claude Maxなど — はまだ配線
されていません。APIキーとLiteLLMがサポートする任意のエンドポイントは配線されています。)*

すべてが正しく配線されているか確認してください。

```bash
uv run chimera doctor    # shows version, default model, configured providers
uv run chimera models    # shows the fusion panel / judge / synthesizer
uv run chimera features  # optional capabilities + what each needs (key/dep)
```

**オプション機能。** 視覚(Vision)、Deliverable Mode、Petは組み込みです。残り(ウェブ検索、
X検索、画像生成、TTS/音声、Spotify、ブラウザ)はあらかじめ用意されたスロットです:
`.env` に対応する認証情報を埋める(または依存関係をインストールする)と、その機能が有効に
なります。`chimera features` がライブのチェックリストです。`web_search` ツール(Tavily)は
`TAVILY_API_KEY` が設定された瞬間に自動登録されます — これが他のものを追加する際の
テンプレートです(あるいはMCPクライアント/OpenAPI→toolインポーターを使ってください)。

> **無料モデル vs 有料モデル。** OpenRouterの `:free` モデルは無料ですが、上流でレート
> 制限されます — 手早い `run` には問題ありませんが、`fuse`/`solve` のような複数回呼び出す
> コマンドには不安定です。実運用には、安価な有料モデル(例: `deepseek/deepseek-chat-v3.1`、
> 1回の呼び出しにつき1セントの何分の1)の方がはるかに信頼できます。

---

## コマンド

### ステータス — `version` · `doctor` · `models`

```bash
uv run chimera version
uv run chimera doctor
uv run chimera models
```

### `chat` — インタラクティブなマルチターンアシスタント(あなたの右腕)

会話メモリとツール利用を備えたインタラクティブなREPL — 日常の主力です。関連する長期記憶を
呼び出し、ターンをまたいで会話をつなげ、**毎ターンのあとにスレッドを** `<home>/sessions`
**へ保存**するので、次回の実行は中断したところから続きます。`chimera sessions` は保存された
スレッドを一覧し、`chimera sessions --delete <id>` は1つを削除します。

```bash
uv run chimera chat                        # resume the newest thread; /exit to quit
uv run chimera chat --new                  # start a fresh thread instead of resuming
uv run chimera chat --session standup      # -s: resume, or name, one thread by id
uv run chimera chat --no-memory            # don't recall long-term memory
uv run chimera chat --cascade              # tiered routing: weak -> gate -> mid -> gate -> fusion
uv run chimera chat --fuse                 # fusion routing for tool-free turns (read the note)
uv run chimera chat --max-usd 0.50         # ceiling for the WHOLE thread, not for one turn
uv run chimera chat --write-region 'src/**,*.py'   # the only paths the file-writers may touch
uv run chimera chat --model MODEL --workspace DIR --max-steps 8
```

`--workspace`/`-w` はツールの起点であり、`AGENTS.md` が読まれる場所でもあります。
`--max-steps` は1つのメッセージ内でのツール呼び出しステップ数を制限します。`--model`/`-m` は
モデルのslugを上書きしますが、後述のルーティングの注記も読んでください。

コマンド: `/help` · `/new`(新しいスレッド — 現在のものはディスクに残ります) ·
`/reset`(`/new` と同じ) · `/model <slug>`(引数なしでデフォルトに戻る) ·
`/solve <タスク>`(検証付きループに渡す) · `/exit`(`/quit`、`/q` も可)。

**これはガバナンス下にあり、あなたに尋ねます。** `chat` と `assist` は、APIの経路が組み立てる
のと同じスタックを組み立てます: あなた自身のメッセージを伝えられた汚染台帳、信頼できない
ツール出力を囲む `<<external-data>>` のフェンス、トラストカーネル、`--write-region`、所有者の
リーチの下限(`CHIMERA_REACH`)、そして `agent.json` にある所有者の指示です。承認者は
**あなたのターミナルで尋ねます** — 答えられる人がいることが保証されている唯一の面だからです。
インジェクションのコーパスで計測しました(`bench/right_hand_governance/RESULTS.md`、
2026-09-08): ブロックした攻撃は7件中0件から7件中7件へ、フェンスの内側で返ってきた外部読み取り
は12件中0件から12件中12件へ、そして人が答える場合の過剰ブロック(over-block)は0.000 —
その代償は、8件の正当な行に対する5つの質問です。パイプ経由(`chimera chat < script.txt`)では
尋ねる相手がいないため、質問は黙認ではなく記録された拒否になります。

正直な注記:

- **拒否されたツール呼び出しは返信の下に表示されます** — `✗ run_shell did not succeed: …`。
  モデルは拒否の周りを取り繕って語ります: 計測された事例では、ホスト実行のゲートが拒否した
  コマンドについて *"The command printed exactly: marker-42"* と答えていました。承認にも独自の
  行があり(`governance: 1 approved this turn`)、ターンの途中で打った `y` は、返信が流れて
  いったあとも痕跡として残ります。
- **`--fuse` はツールを伴うターンを融合しません。そしてREPLのターンは常にツールを伴います。**
  ルーターはツールを伴うターンをすべて単一モデルへ送るので、ここでの `--fuse` のターンは実際
  には単一モデルのターンです。さらに両方を指定した場合は `--cascade` が `--fuse` に勝ちます。
  ターミナルで本当に融合する唯一の経路は `assist` の `/task` です。
- **モデルを名指しするとそれに固定され、Tierのはしごは脇へ退きます。** `--cascade` の下では
  ターンごとにモデルを選ぶのははしごであり、以前はあなたが名指ししたslugを何も言わずに飲み
  込んでいました。今はどちらかが名指しされているあいだ `--model` と `/model <slug>` が勝ち —
  はしごが切れていることを1行が伝えます — そして引数なしの `/model` が仕事をはしごへ返します。
- 各ターンはトークン数と価格を表示し(モデルのリスト価格が不明なときは `cost: unavailable`。
  推測したゼロは決して出しません)、`<home>/usage.jsonl` に1行を追記します。デスクトップアプリ
  のコスト画面が読むのはこのファイルです。
- **`--max-usd` が縛るのはスレッドであって、ターンではありません。** 最初のメッセージから
  `/exit` まで1つのメーターが走ります。`/solve` も同じ財布から引き出します。そして使い切ると、
  残りがないと知るために1回の呼び出しを払うのではなく、次のメッセージは送られる前に拒否され
  ます。途中で切られた返信は — 上限によって、`--max-steps` によって、あるいは収まらなくなった
  コンテキストによって — そのことを自分の行で伝えます。切られた答えは、そうでなければ完結した
  答えとまったく同じに読めてしまうからです。
- **復元されたターンはそう告げます。** スレッドがディスクから戻ってくると、返信の下の淡い1行
  が、復元された再生ターンの数と、そのうち来歴が一度も記録されなかったものの数を数えます。
  それらはモデル自身の言葉としてではなくデータのフェンスの内側で再生されますが、これまで
  フェンスは、それが守る当人からは見えていませんでした。
- **`/solve <タスク>` は会話を検証付きループへ渡します** — 計画し、編集し、検証し、失敗したら
  その試みを差し戻します。デスクトップのコード画面にある2つのボタンのうちの2つめです。自分
  から始まることは決してなく、走る前にタスクと上限を表示し、ループ自身の答えはスレッドに
  記録されます。引数なしなら、あなたが最後に尋ねたことを取ります。
- **MCPサーバーがターミナルに届きます。** `CHIMERA_MCP_AUTOLOAD=1` を使うと `mcp.json` の
  サーバーがフェンスの手前でマウントされるので、拒否リスト、カーネル、汚染台帳がそれらを覆い、
  サーバーの出力は他の外部読み取りと同じくデータのフェンスの内側に届きます。プロセスごとに
  一度だけ接続してアプリと共有するので、二重に起動されるものはありません。
- `/reset` は**新しいスレッドを開始**します。現在のスレッドを消しはしません。ディスクに何も
  なかった頃はメモリ上の記録を消していましたが、スレッドがファイルになった今、その場で消すのは
  作業を破棄することになります。(何も永続化しない `assist` と `tui` では、`/reset` は今も
  コンテキストをクリアします。)
- `chimera doctor` は、エージェントのコマンドがどこで実行されるか、そして先に尋ねるかどうかを
  報告します: 設定されたサンドボックス、OSのサンドボックスが実際に利用できるか、そしてホスト
  実行の姿勢です。

### `assist` — 同じ右腕、既定で安価

`assist` はセカンドブレインの既定値をオンにした `chat` です: Tierのカスケードが雑談を安価な
モデルへ回し、難しい依頼を上位へエスカレートします。永続的なプロフィール(`chimera profile`)が
安定した前置きになり、メモリ、提案、セッション終了時の統合が有効です。終了時にはセッションの
レシート — Tierの分布と実測トークン — を表示するので、「既定で安価」が数字になります。

```bash
uv run chimera assist                      # cascade, profile and memory on
uv run chimera assist --no-cascade         # one default model instead of the ladder
uv run chimera assist --no-memory          # don't recall long-term memory
uv run chimera assist --max-usd 0.25       # ceiling for the WHOLE run, not for one turn
uv run chimera assist --write-region 'src/**'      # the only paths the file-writers may touch
uv run chimera assist --model MODEL --workspace DIR --max-steps 8
```

コマンド: `/help` · `/task <難しい依頼>`(フルパワーの融合、一発勝負) ·
`/solve <タスク>`(検証付きループに渡す) · `/profile <種類>: <事実>`(あなたについて覚える —
種類: `preference`、`project`、`context`、`name`) · `/model <slug>` ·
`/reset`(会話のコンテキストをクリア。何も削除されません) · `/exit`(`/quit`、`/q` も可)。

ガバナンスは `chat` とまったく同じです — 同じレジストリ、同じ尋ねる承認者、同じ拒否・ガバナンス
・コストの行、同じ `usage.jsonl` の行、同じMCPサーバー、実行全体にかかる同じ `--max-usd` の
メーター、そして同じ `/solve`。知っておくべき違いは2つあります: **`assist` はスレッドを
持ちません**(終了時に忘れます — 取り戻したい会話には `chat` を使ってください)。そしてモデルを
名指しするとそれに固定され、固定されているあいだTierのはしごは切れます — モデルを選ぶのは
はしごであり、以前はslugを受け取っては無視していました。

`/task` は依頼だけに対して1回の強制融合を走らせます: 会話はパネルへ送られません。これは意図的
で、スレッドをパネルとジャッジとシンセサイザーに食わせると、控えめに使うために存在する経路の
コストが何倍にもなるからです。その答えは今や次のターンのコンテキストに*入ります*。そして他の
どのターンとも同じく価格を表示し、`usage.jsonl` に1行を書きます — ターミナルで最も高価な経路が、
コスト画面には見えない唯一の経路だったのです。

### `tui` — フルスクリーンのターミナルアプリ

同じ会話コアの上に構築された、Textualによるフルスクリーンのターミナルアプリです。2つの
ペイン: 返信をMarkdownとしてレンダリングする(フェンス付きコードはシンタックスハイライトされる)
**会話ログ**で、モデルのトークンは届いた端から**ライブでストリーミング**されます。もう1つは、
このターンでエージェントが何をしたか — 呼び出したツール、トークン数とコスト、何件のメモリの
事実が想起されたか — を示す**アクティビティパネル**です。

```bash
uv run chimera tui
uv run chimera tui --no-stream        # answers render at the end instead of streaming
uv run chimera tui --fuse --no-memory # fusion routing (no token stream — the panel says so)
uv run chimera tui --model MODEL --workspace DIR --max-steps 8
uv run chimera tui --max-usd 2.00     # a ceiling for the whole session, shown in the panel
```

REPLと同じフラグではありません。`tui` には `--stream`/`--no-stream` があり、REPLにはありません。
そして `--max-usd` があり、その分だけ使うとセッションを止めます。このフラグは、それを表示する場
所を待っていました: アクティビティパネルは今、残りを示す `budget` の行を備えています。誰にも見
えない上限は、お金で止まったターンを、目に見える理由もなく止まったターンに変えてしまうからです。

`chimera chat` の `--cascade`、`--session`、`--new`、`--write-region` に相当するものはここに
はありません。

コマンド: `/model <slug>` · `/reset`(コンテキストをクリア) · `/clear`(画面をクリア) ·
`/stream`(ライブトークンの切り替え) · `/help` · `/exit`(`/quit`、`/q` も可)。キー:
`Ctrl+R` リセット · `Ctrl+L` クリア · `Ctrl+P` コマンドパレット · `PgUp`/`PgDn` スクロール ·
`Ctrl+C` 終了。スラッシュコマンドは入力中にオートコンプリートされます。

正直な注記:

- **これはガバナンス下に置かれており、その問いかけは入力されるのではなく描画されます。** REPL
  が組み立てるのと同じスタックです: あなた自身のメッセージを伝えられた汚染台帳、信頼できないツー
  ル出力を囲む `<<external-data>>` のフェンス、トラストカーネル、所有者のリーチの下限、そして
  接続されたMCPサーバーです。2026-09-09までこの面を外に置いていたのはスタックではなく問いかけ
  のほうでした — ターミナルはTextualが所有しているため、stdinへ書かれた問いかけは誰も見られな
  い場所へ書かれます。ptyで計測すると、そうしたターンは120秒のタイムアウトに対して123.8秒ブロッ
  クし、説明のない `✗ run_shell` として返ってきました(`bench/right_hand_governance/RESULTS.md`
  パート2)。今は**両方**のゲートが代わりにモーダルを開きます: ホスト実行の確認と、ガバナンスの
  承認者です。`y` か `n`、Escapeで拒否、Noボタンがフォーカスを保持するのでEnterで誤って承認す
  ることはなく、カウントダウンが沈黙に残された時間を告げます — 沈黙は今も拒否で、今はそう告げ
  ます。同じコーパスと同じ計測器で、ブロックした攻撃は7件中0件から7件中7件へ、フェンスの内側で
  返ってきた外部読み取りは15件中0件から15件中12件へ変わりました(外に残る3件はあなた自身のリポ
  ジトリで、これは外部ではありません)。
- **拒否された呼び出しは今、その理由を返信の下で伝えます。** アクティビティパネルはすでに `✗`
  の下にツール自身の一文を示していました。会話ログも `✗ run_shell did not succeed: …` を示しま
  す。そこは、走らなかったコマンドについてのモデルの説明があるのと同じ場所です。承認にも1行があ
  り(`governance: 1 approved this turn`)、ターンの途中で押した `y` は痕跡を残します。
- 何も永続化しません: TUIを閉じると会話は終わります。スレッドがあるのは `chat` です。
- トークンストリーミングは単一モデルの経路のみです — `--fuse`(パネル→ジャッジ→シンセサイザー
  のターン)の下では逐次トークンはないため、パネルは偽のカーソルではなく「合成中」というステー
  タスを表示します。このラベルは経路ではなくフラグに従います: `chat` と同じく、ツールを伴う
  ターンは融合せず、REPLのターンは常にツールを伴います。
- モデルのリスト価格が不明な場合、コストは「unavailable」と表示されます(決して推測しません)。
  そして各ターンはREPLと同じく `<home>/usage.jsonl` に追記されます。
- ここには検証/差し戻しはなく、`/solve` もありません: それらは `chat` と `assist` へ行きました。
  MCPサーバーは以前はマウントされていませんでしたが、今はマウントされます。差し止められていたの
  とまったく同じ理由です — MCPの出力は定義からして信頼できないコンテンツであり、今はそれを囲む
  場所があるからです。検証または差し戻しは `chimera solve` と `chimera project` でも
  実行されます。
- Textualがインストールされていない場合、`tui` はプレーンな `chat` REPLにフォールバックします。
  引数はすべて明示的に渡されるため、そのフォールバックは最初のターンを生き延びます。そこでは
  ストリーミングに意味はなく、スレッドは他の `chat` のスレッドと同じように保存されます。

### `serve` — メッセージングゲートウェイ(HTTPまたはDiscord)

**チャットごとに**1つの会話(とそのメモリ)を伴ってエージェントを公開します。ルーティング
コアはトランスポート非依存で、アダプターが差し込まれます。

```bash
uv run chimera serve --port 8765          # HTTP transport
# GET  /health           -> {"status":"ok","active_chats":N}
# POST /chat  {"text":"...", "chat_id":"alice"}  -> {"reply":"...","chat_id":"alice"}
```

各 `chat_id` は独自のコンテキストを保持するため、異なるユーザー/スレッドが混ざることは
ありません。

**無人での運用(webhook)。** 受信HTTP POSTで発火するジョブを登録し、誰も入力しなくても
Chimeraが実行されるようにします — GitHubのpush、Stripeのイベント、cron-as-a-serviceの
ping。

```bash
chimera cron add "on push" gh-push "Summarize the pushed commits" --webhook
chimera serve                              # then POST to the hook:
# curl -X POST localhost:8765/webhook/gh-push -d '{"ref":"refs/heads/main"}'
```

POSTのボディはジョブのタスクにコンテキストとして渡され、そのフックに登録されたすべての
ジョブが実行されます。`GET /health` と `POST /chat` はそれと並行して引き続き動作します。

**ネイティブDiscord。** Chimeraを Discordボットとして実行します — 各チャンネルが1つの
セッションであり、エージェントは `send_message` ツールを介してメッセージを送信することも
できます。

```bash
uv sync --extra messaging                 # installs discord.py
export CHIMERA_DISCORD_BOT_TOKEN=...       # bot token (Message Content intent enabled)
uv run chimera serve --discord
```

<https://discord.com/developers>でボットを作成し、**Message Content**インテントを有効にし、
サーバーに招待してください。見えるすべてのチャンネルで返信します(自身と他のボットの
メッセージは無視するようフィルタリングされます)。トークンは環境変数から読まれます —
決してハードコードされません。

**ネイティブTelegram。** 同じアダプターパターンで、**追加の依存関係は不要**です
(Telegram Bot APIはプレーンなHTTPです)。

```bash
export CHIMERA_TELEGRAM_BOT_TOKEN=...      # from @BotFather
uv run chimera serve --telegram
```

**ネイティブSlack。** Socket Mode(`messaging` エクストラが必要)経由で受信し、Web API
経由で送信します。Slackアプリで Socket Modeを有効にすると、アプリレベルのトークンが得られます。

```bash
uv sync --extra messaging
export CHIMERA_SLACK_BOT_TOKEN=xoxb-...     # bot token
export CHIMERA_SLACK_APP_TOKEN=xapp-...     # app-level token (Socket Mode)
uv run chimera serve --slack
```

**WhatsApp(送信)。** WhatsAppは*プッシュベース*です(メッセージはあなたがホストする
Meta webhookに届きます)。そのため他とは異なり、開くべき接続はありません。Cloud APIの
認証情報を設定すれば、エージェントはどの `serve` モードでも `send_message` ツール経由で
WhatsAppメッセージを**送信**できます。

```bash
export CHIMERA_WHATSAPP_ACCESS_TOKEN=...
export CHIMERA_WHATSAPP_PHONE_NUMBER_ID=...
# in a chat: send_message(platform="whatsapp", chat_id="<E.164 number>", text="done ✅")
```

**双方向WhatsApp。** Metaアプリのwebhookを `https://<your-host>/whatsapp` に向け、
`CHIMERA_WHATSAPP_VERIFY_TOKEN`(アプリの設定に一致する任意の文字列)を設定してください。
すると `chimera serve` はサブスクリプションを検証し(`GET /whatsapp`)、受信メッセージを
(`POST /whatsapp`)ゲートウェイ経由でルーティングし、Cloud API経由で返信します。WhatsApp
はwebhookのために公開URLを依然として必要とします — それがChimeraの外にある唯一の部分です。

**ネイティブSignal(双方向)。** Signalには公式APIがないため、Chimeraはあなたが実行し
(Docker)、自分の番号にリンクする[`signal-cli-rest-api`](https://github.com/bbernhard/signal-cli-rest-api)
ブリッジと話します — プレーンなHTTPで、Pythonの依存関係はありません。

```bash
docker run -d -p 8080:8080 -v signal-cli:/home/.local/share/signal-cli bbernhard/signal-cli-rest-api
export CHIMERA_SIGNAL_API_URL=http://localhost:8080
export CHIMERA_SIGNAL_NUMBER=+15550000000     # this bot's registered number
uv run chimera serve --signal
```

### `run` — Tier-1、一発完結の応答

ツールなし、フュージョンなしの単一モデル呼び出し。最も安価な経路です。

```bash
uv run chimera run "In one sentence, what is an AI agent?"
uv run chimera run "Summarize this error" --model openrouter/openai/gpt-4o-mini
```

**視覚(Vision)/画像の貼り付け。** `--image`(パスまたはURL、繰り返し可能)で画像を
添付します — vision対応のモデルが必要です。

```bash
uv run chimera run "What's in this chart?" --image chart.png -m openrouter/google/gemini-2.5-flash
```

### `deliver` — Deliverable Mode(成果物を生成する)

`run`/`chat` が会話的に答えるのに対し、`deliver` は完全で自己完結したドキュメント(レポート、
計画、仕様、READMEなど)を生成し、ファイルに書き出します。

```bash
uv run chimera deliver "A one-page launch plan for a URL shortener" --out plan.md
uv run chimera deliver "An HTML status page" --format html -o status.html --fuse
```

### `agent` — 生のReActツール呼び出しループ

思考 → 行動(ツール) → 観察を、最終的な答えが出るまで繰り返します。ツールは
ワークスペースにスコープされます。

```bash
uv run chimera agent "Create a file hello.txt containing 'Hello Chimera'" -w ./scratch
```

### `fuse` — LLM-Fusion(差別化要因)

モデルの*パネル*を実行し、*ジャッジ*がそれらの答えを分析し(合意点/矛盾点/盲点)、
*シンセサイザー*が最終的な答えを書きます。完全なトレースを見るには `--show-panel` を
使ってください。

```bash
uv run chimera fuse "Name three concrete ways to prevent SQL injection in Python."
uv run chimera fuse "Compare REST vs gRPC for a mobile backend." --show-panel
```

フュージョンは単一呼び出しの約2〜3倍のコストなので、難しい推論のために取っておいてください。
`fuse` はステージごとのトークンコスト(パネル/ジャッジ/シンセサイザー)も表示するため、
1回の実行のトークンが実際どこに使われているかが分かります。

**選択的フュージョン(デフォルトでON、トークンを節約)。** エンジンは最初の
`CHIMERA_FUSION_PROBE_K` 個のパネルモデル(デフォルト2)を試し、それらの答えが近く一致する
場合は、残りのパネル*と*ジャッジをスキップし、一致した答えから直接合成します。この一致
チェックは安価なローカルのテキスト比較です(追加のモデル呼び出しなし)。そのため、
*不一致な*ターンはフルパイプラインにエスカレートし、フルフュージョンとまったく同じコストが
かかりますが、*一致する*ターンはより安くなります。基準は `CHIMERA_FUSION_AGREEMENT`
(0〜1、デフォルト0.8)で調整するか、`CHIMERA_FUSION_MODE=full` を設定する(または `--full`
を渡す)と常にパネル全体+ジャッジを実行します。

これがデフォルトである理由: `chimera fusion-bench --tasks hard` の3回の実行(有料の
3モデルパネル)にわたって、これはトークンを**約20〜28%**削減し、実際に短絡した*すべての*
ターンで正解でした(16/16)。全体の精度は実行間で0から−8.3ポイントの間で揺れましたが、
その分散はすべて*エスカレートした*バケットに現れます — そこでは選択的フュージョンはフルと
同一のパイプラインを実行するので、これは早期停止のコストではなく、モデルの非決定性です。
自分のワークロードでベンチを実行し、あなたのパネルとタスクにとってのトレードオフを
確認してください。

```bash
uv run chimera fuse "What is 12 * 12?" --show-panel   # likely early-stops
uv run chimera fusion-bench --tasks hard              # full vs selective, tokens + accuracy
```

> **信頼できるパネルモデルを選んでください。** フュージョンは、すべてのパネルメンバーが
> 実際に答えを返す場合にのみ見合います。`CHIMERA_FUSION_PANEL` にOpenRouterの `:free`
> モデルのスラッグを使うのは避けてください — 実運用の負荷下ではレート制限され(HTTP 429)、
> パネルは残っている有料モデルへと静かに縮小します。安価で信頼できるトリオ:
> `openrouter/deepseek/deepseek-chat`、`openrouter/openai/gpt-4o-mini`、
> `openrouter/meta-llama/llama-3.3-70b-instruct`。

### スキルカード(TRS推論カード、実験的)

エージェントは学んだことを**推論カード** — Trigger / Do / Avoid / Check / Risk の5つの
フィールド(+検索用キーワード) — に蒸留します。これは成功(*パターン*カード)と繰り返す
失敗(助言的な*アンチパターン*カード)の両方から作られます。`CHIMERA_SKILL_CARDS=on` のとき、
`solve` は上位k件の関連カードを取得し(名前+説明+トリガーに対するBM25)、それらをワーカーの
推論コンテキストに注入します。そのためエージェントは、うまくいったことを再利用し、既知の
失敗モードを避けられます。これはループを閉じます — 以前は、学習されたスキルは保存される
だけで、読み戻されることはありませんでした。

デフォルトではオフです: カードの注入はプロンプトトークンを増やし、TRSの*トークン*節約は
長い推論トレースを短くすることから来るため、短い回答のタスクでは上振れは精度であって
コストではありません。これは仮説ではありません — `hard` 短答スイート(有料の
deepseek-v3.1)では、`skillcard-bench` はカードなしと比べて、カードが**トークン+290%**、
**精度−8ポイント**であることを計測しました: 天井に近いモデルで、短縮すべき長いトレースが
ない場合、汎用的なカードは純粋なオーバーヘッドであり、気を散らせる可能性があります。カードは
(数学/コーディングのように長いトレースを持つ)**長い推論**のワークロードに有効にしてください
— そこではトークンの計算が逆転します。そして常に、まず正解データによるチェックで自分自身の
トレードオフを測定してください。

```bash
uv run chimera skillcard-bench --tasks hard          # demo cards vs no cards
uv run chimera skillcard-bench --use-store --tasks hard   # bench your own learned cards
export CHIMERA_SKILL_CARDS=on CHIMERA_SKILL_CARDS_K=3      # enable, once it earns its place
```

このベンチは、カードあり/なしの精度、トークンの差分、カードのヒット率、ヒット/ミスで
分けた精度を報告し、カードの精度がカードなしのベースラインから1ポイント以内に収まる場合に
PASS判定を出します。

### コンパクトなツールスキーマ(実験的)

ツールのスキーマ — 特にMCPサーバーやOpenAPI仕様からインポートされたもの — は、注釈の
ノイズ(例、タイトル、デフォルト値、複数文にわたるパラメータの説明文、ネストしたリクエスト
ボディ)を持ち、それは**すべての**ReActステップでモデルに再送信されます。
`CHIMERA_COMPACT_SCHEMAS=on` にすると、そのノイズが取り除かれ、パラメータの説明が
アドバタイズ時に短縮されます。呼び出しに影響するものには**一切**触れません(関数名と
説明、そしてすべてのスキーマの `type` / `properties` / `required` / `enum` は保持されます)。
正典のスキーマは無変更のままです — モデルに送られるコピーだけが小さくなります。

節約が最も大きいのは冗長なMCP/OpenAPIのツールセットで、すべてのステップで積み重なります。
ネイティブツールはすでに簡潔なので、その削減は小さくなります。まず自分のツールセットを
計測してください(モデル呼び出しなし — トークンを数えるだけです)。

```bash
uv run chimera schema-bench --demo                   # synthetic verbose tools, to see the effect
uv run chimera schema-bench --openapi ./openapi.json # your real spec's tools
```

デフォルトではオフです。圧縮は注釈のノイズだけを取り除き(構造には決して触れない)ため、
唯一のリスクはモデルがツールを選ぶための説明文がわずかに少なくなることです — そのため
これは保守的なままであり、有効にする前に自分のワークロードでツール呼び出しの挙動を
確認する必要があります。

### `solve` — Tier-2の自律性(計画+検証または差し戻し)

タスクを計画し、エージェントループで実行し、その後**実行可能なコマンドで検証**します。
検証が失敗した場合、ワークスペースを差し戻し、フィードバックとともに再試行します。
検証者(終了コード0 = 成功)がグラウンドトゥルースです。

```bash
uv run chimera solve \
  "Create solution.py with add(a,b) and is_prime(n)." \
  --workspace ./work \
  --verify "python -c \"import solution; assert solution.is_prime(7)\""
```

便利なフラグ:

| フラグ | 意味 |
|------|---------|
| `--verify "<cmd>"` | 終了コード0を返さなければならないコマンド(テスト、ビルド、リンター) |
| `--workspace`、`-w` | エージェントが読み書きする場所(デフォルト `.`) |
| `--max-attempts N` | 検証または差し戻しの予算(デフォルト3) |
| `--max-steps N` | 試行ごとのツール呼び出しステップ数(デフォルト8) |
| `--fuse` | フュージョン経由で**計画**を生成する(深い推論) |
| `--guard` | すべてのツール呼び出しをガバナンスカーネルでゲートする |
| `--no-plan` / `--no-manager` | 計画/レビューステージをスキップする |
| `--rubric` | マネージャーが**カスケードルーブリック**(指示追従 → 事実性 → 合理性)で判定する |
| `--no-remember` | 成功時にメモリの事実を自動で書き込まない |
| `--no-evolve-skills` | タスクが再発しても学習スキルを自動提案しない |
| `--isolate` | 使い捨てのgit worktreeで実行し、変更されたファイルは成功時のみコピーバックする |
| `--require-diff` | **どのファイルも変更しなかった**試行は失敗として再試行される — コードタスクでは、説明は修正ではない |
| `--keep-workspace` | 失敗時に、差し戻す代わりに最後の試行の編集をディスクに残す — **外部の**採点者が合否を決める場合向け |
| `--diff-feedback` | 失敗した試行に、取ってはいけない道として、差し戻された自身の差分を見せる |
| `--stagnation-fuzzy` | 繰り返す失敗のシグネチャを近似的に照合し、文言が異なる同一原因の失敗でもアンチストール旋回が発動するようにする |

> **`--max-steps` について。** デフォルトの8は小さなワークスペース向けに調整されています。
> **大きなリポジトリでは、それがモデルではなく拘束条件になります**: SWE-benchの実行1は、
> 250MBのチェックアウトに対して8ステップで正確に0.0ポイントを記録し、同じ設定で
> **30ステップ**にすると、ベースラインのパッチ率が47%から74%に上がりました
> ([`bench/swe_bench/RESULTS.md`](../../../bench/swe_bench/RESULTS.md))。エージェントが探索した
> だけで編集せずに終わる場合は、まずこれを上げてください。

> **`--require-diff` と `--keep-workspace` は外部採点向けです。** `solve` は検証または
> 差し戻しです: *それ自身*が合否の決定を持つ場合、失敗した試行を差し戻すのは正しいことです。
> 何か他のもの — CIジョブ、ベンチマークハーネス、差分をレビューする人間 — がそれを
> 持っている場合、`--keep-workspace` は、その判定者が見る前にエージェントの作業が
> 巻き戻されるのを止め、`--require-diff` は、自信たっぷりの説明が完了した変更として
> 採点されるのを止めます。どちらも**デフォルトではオフ**です。

**`solve` は実行をまたいで学習します。** 各実行は、検証または差し戻しによってすべて
ゲートされる閉じた行動ループに供給されます。そのため検証された作業だけが何らかの効果を
持ちます: (1) 過去の試行からの関連する**教訓**(失敗が優先されます)が計画/プロンプトに
折り込まれ、失敗した試行の**最初の欠陥ステップ**が特定され、再試行に反映されます。
(2) 検証された成功時に、重複排除された**メモリ**の事実が書き込まれます(後で `chat`/`crew`
によって想起されます)。そして (3) タスクのパターンが再発する場合(過去2回以上の成功)、
再利用可能な**スキル**が提案されます — フュージョンパネル全体にわたり、`--fuse` が有効な
場合はモデル横断の**転移可能性**によって保持されます — そして、それがガバナンス検証と
実行可能なスモークテストに合格した場合にのみ保持されます。

### `crew` — Tier-3のマルチエージェント

ロールエージェントのチームが1つのタスクに共同で取り組み、スーパーバイザーが最終的な答えを
合成します。

```bash
uv run chimera crew "Propose a minimal architecture for a URL shortener service."
```

### `lifecycle` — SDLCクルー(計画 → 構築 → テスト → レビュー)

テスト段階で**検証または差し戻し**を伴う、あらかじめ組み立てられたソフトウェアライフ
サイクルパイプラインです: `plan` がタスクを分解し、`build` がそれを実装し、`test` が
検証者を実行し(失敗時にはビルドを差し戻して再試行し)、レビュアーが結果を批評します。

```bash
uv run chimera lifecycle "Add an add(a,b) function to solution.py" \
  --workspace ./scratch --verify "python -c \"import solution; assert solution.add(2,3)==5\""
```

各段階は✓/✗とともに表示されます。実行が `success` になるのは、テスト段階の検証者が
合格した場合のみです。

### `meta` — エージェントがエージェントを構築する

タスクのために専門化されたエージェントの設計図(名前、ツール、ロールプロンプト)を
設計します。

```bash
uv run chimera meta "an agent that triages GitHub issues and routes them to teams"
```

### `guard` — ガバナンスの判定

ある行動に対する信頼カーネルの決定(allow / warn / review / block)を表示します。

```bash
uv run chimera guard "rm -rf /"                       # BLOCK
uv run chimera guard "list the files in this folder"  # ALLOW
```

### `bench` — 継続的進化ベンチマーク

一連のタスクにわたって性能が*保たれる*かどうかを計測します(劣化防止の証明): 全体の
合格率、前半 vs 後半、最長連続成功。

```bash
uv run chimera bench --limit 6           # single-shot task set
uv run chimera bench --chain --limit 6   # stateful chain (error propagation)
uv run chimera bench --fuse              # use fusion as the solver
```

このレポートは**統計的に正直な**劣化フラグも運びます: 前半引く後半の裸の減算をそのまま
信じるのではなく(短いチェーンでは0.2の振れは通常ノイズです)、`degraded_significant` は、
低下に対するWilson信頼区間がゼロを含まない場合にのみ `1.0` になり、サンプルが小さすぎて
判断できない場合は `-1.0`、それ以外は `0.0` になります。加えて `degradation_ci_low/high`
の境界も含まれます。別の話として、`CHIMERA_SKILL_ACCEPT_MODE=wilson` は、モデル横断の
スキル採用の決定を、転移率の信頼区間の*下限*でゲートします(そのため、まぐれの3回中2回
合格はもはやカウントされません)。デフォルトの `point` は生の割合を維持します。Wilson境界は
小さなパネルには厳しすぎるためです。

### `sandbox-bench` — 状態と副作用の採点

テキストベンチはモデルの*答え*を採点します。これはエージェントが**したこと**を採点します。
各タスクは隔離されたサンドボックスディレクトリで実行され、ハーネスは最終的なファイル状態を
目標と比較し(どんなパスでも許可される、結果重視のスタイル)、**さらに**別に、*有害な
副作用* — タスクが宣言した許可集合の外での変更 — を数えます。そのため、無関係なファイルを
壊しながら正しい結果を出すエージェントは、クリーンな合格として採点されるのではなく、
捕捉されます。

```bash
uv run chimera sandbox-bench            # runs the demo stateful tasks (real models + file tools)
```

`pass_rate` と `side_effect_rate` を報告します。大きなタスクスイートではなく、
*方法論*(`goal_check` + `allowed` の変更集合を持つ `StatefulTask`)を提供します —
自分自身のツール向けにタスクを作成してください。既存のテキスト採点器は、純粋なQ&A作業には
引き続き正しく機能します。

### `memory` — 選別された長期記憶

```bash
uv run chimera memory add "Alex prefers TypeScript strict and absolute imports"
uv run chimera memory search "imports"
uv run chimera memory list
uv run chimera memory graph                 # entity-relation graph from memory
uv run chimera memory graph --entity PassaPro   # one entity's relations
uv run chimera memory prune --max 50        # keep the N highest-value memories (multi-factor)
```

想起は**受理ゲート**(信頼境界)を通過します: 想起されたメモリがプロンプトに入るのは、
それが関連性があり、*かつ*上書き/インジェクションのテキストがない場合のみです(メモリ
ベースのジェイルブレイク対策)。`memory prune` は、単一の手がかりではなく、多要素の
**価値**モデル(新しさ、具体性、種類、選別、信頼性)によって予算の範囲内で忘却します。

**グラフ層**は、あなたのメモリから `(source, relation, target)` の三つ組を抽出します
(`PassaPro uses Supabase`、`Alex prefers TypeScript`)。そのため事実は、キーワードだけ
でなくエンティティによっても想起できます。

### `cron` — スケジュールされたジョブとイベントSOP

```bash
uv run chimera cron add daily-report "0 9 * * *" "generate the daily report"
uv run chimera cron list
```

### `kanban` — ワーカーレーン付きのタスクボード

ボード(`backlog → doing → review → done`)で、各カードは、それをエージェントスタックに
ディスパッチする*レーン*を指定します: `solve`(Tier-2の自律、検証または差し戻し)または
`crew`(Tier-3のロールパイプライン)。エージェントがすでに実行しているループの運用ビューです。

```bash
uv run chimera kanban add "Fix the flaky test" -a "make test_login deterministic" \
  --lane solve --verify "pytest -q tests/test_login.py"
uv run chimera kanban add "Compare REST vs gRPC" --lane crew
uv run chimera kanban board                 # show the columns
uv run chimera kanban run -w ./scratch      # dispatch backlog cards through their lanes
uv run chimera kanban move <id> done        # manual move
uv run chimera kanban learn --min 3 --yes   # recurring tasks (experience) -> cards
```

`run` は各カードをbacklog → doing → done(成功)、または → review(要対応)へと進めます。
`learn` はcron-learnerの再発検出器を再利用して、エージェントが繰り返すタスクをキューに
入れます(ボードと重複排除されます) — バックログを自動で埋めるようスケジュールしてください。

### `workflow` — 設計されたループ(Loop Engineering)

その場しのぎのプロンプトの代わりに、自律ループをYAMLとして記述します。各ステップは
ケーパビリティ(`run` / `shell` / `solve` / `crew` / `lifecycle`)を `uses` し、前の
ステップに条件付けでき(`when: prev_succeeded | prev_failed`)、ループできます
(`repeat`、`until: success`)。

```yaml
# examples/workflow.yaml
name: build-and-report
steps:
  - name: build
    uses: solve
    with: { task: "Create greeting.py with greet(name)", verify: "python -c \"import greeting\"" }
    repeat: 2
    until: success
  - name: report
    uses: run
    when: prev_succeeded
    with: { prompt: "One-line changelog for greet()" }
```

```bash
uv run chimera workflow examples/workflow.yaml --workspace ./scratch
```

### `drift` — 仕様⇔コードのドリフトゲート

仕様とコードの整合を保ちます。仕様は要件の小さなYAMLです(シンボルを `defines` する/
正規表現に `contains` する/正規表現が `absent` である/`command` が0で終了する)。
このゲートはドリフトがあれば非ゼロで終了するため、検証者としても機能します。

```bash
uv run chimera drift examples/spec.yaml --workspace ./scratch
# as a verifier inside solve:
uv run chimera solve "..." --verify "chimera drift examples/spec.yaml -w ."
```

### `migrate` — 他のエージェントからのインポート

HermesまたはOpenClawから**設定+スキル**を持ち込み、`--apply` を付けると**長期記憶も
マージ**します(重複排除され、破壊的ではありません)。デフォルトはドライランのプレビュー
です。

```bash
uv run chimera migrate hermes /path/to/hermes/home          # preview
uv run chimera migrate hermes /path/to/hermes/home --apply  # write + merge memory
uv run chimera migrate openclaw /path/to/openclaw/home --apply
```

メモリのマージは `{ADD, UPDATE, NOOP}` のカウントを報告します — 重複は `NOOP` になるため、
再実行しても安全です。

### `evolve` — オプトインのモデル進化(上級)

`chimera solve --collect`(デフォルトでオン)は各実行をトラジェクトリとして記録します。
`evolve` コマンドは、それらを訓練に使える準備の整ったデータセットと、実行可能なLoRA
レシピに変えます。**訓練は外部かつオプトインです** — それはモデルの重みを変更するため、
自動的に起こることは決してありません。Chimeraはデータとスクリプトを準備して止まります。

```bash
chimera evolve status                          # is there enough signal to train?
chimera evolve export --format sft --out d.jsonl --min-steps 5 --diverse   # long-horizon, one example per task
chimera evolve export --format dpo --out d.jsonl   # preference pairs (success vs failure)
chimera evolve recipe --out ./recipe --format dpo  # train.py + README + requirements
chimera evolve tune --rounds 2                  # self-optimize the agent spec (no weights changed)
```

`export` はレシピのつまみを受け付けます: `--min-steps N` は長期にわたるトレースだけを
保持し、`--diverse` はタスクごとに最大1つの例を保持します(タスクの多様性がキュレーション
のボトルネックです)。そして `--min-process P`(SkillCoach)は、*ステップ追従*スコア
(成功して可視の結果を生んだツールステップの割合)がP以上のトレースだけを保持します —
そのため、失敗したツール呼び出しをもがきながらもたまたま成功したケースを訓練に使わずに
済みます。そのスコアの背後にあるステップごとのイベントは、すべての `solve` 実行で
自動的に捕捉されます。このフィルターはデフォルトではオフです(`CHIMERA_SFT_MIN_PROCESS`
がグローバルなデフォルトを設定します)。`evolve tune` は訓練とは異なります — これは
エージェントの*スペック*(モデル、システムプロンプト、ステップ予算、パネル、メモリの
深さ)に対する**メタ探索**を実行し、各候補を日次シナリオで採点し、**非退行**の場合のみ
編集を保持します。これはモデルを呼び出しますが、重みは決して変更しないため、いつでも
安全に実行できます。

その後、実際に訓練するには、GPU上で(またはColabで): `pip install chimera-agent[train]`
(またはレシピの `requirements.txt`)、そして `python recipe/train.py`。配信時には、
`CHIMERA_DEFAULT_MODEL` をベースモデル+アダプターに向けてください。

### `pet` — 仮想のコンパニオン

あなたが離れている間もステータスが変化する、永続する小さなコンパニオンです。キーは
不要です。

```bash
chimera pet new --name Chimi      # adopt one
chimera pet status                # check in (fullness / happiness / energy / mood)
chimera pet feed | play | rest    # interact
```

---

## ヒント

- **ツール vs 推論。** ツール呼び出しのターンは常に単一モデルを使います(フュージョンは
  ツールを呼び出せません)。フュージョンはツール不要な深い推論のために取っておかれます。
- **何が起きたかを調べる。** `CHIMERA_LOG_LEVEL=DEBUG` はルーティングとフュージョン
  関与のログを表面化させます。
- **テストを正直に保つ。** 良い `--verify` コマンド(本物のテストスイート)は `solve` を
  信頼できるものにします — それは、エージェントが従うべき実行可能なグラウンドトゥルース
  です。
