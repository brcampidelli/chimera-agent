---
source_sha256: 40077b594c1ec7a80eb5e6b35149255085c30cea4844b4598de1932f2f5de836
---

# MCPサーバーの接続

MCP(Model Context Protocol)は外部ツールをエージェントに接続する標準的な方法です — GitHub、ファイルシステム、Notion、データベース、その他数百のサーバーがこれを話します。Chimeraはファーストクラスの MCPクライアントを持ちます: どのサーバーのツールも通常のChimeraツールとなり、組み込みツールと同じレジストリに配置され、同じ許可リスト/カーネル/台帳の各層によって統治されます。

## リモートのstreamable HTTP

streamable HTTPを話すMCPエンドポイントには、`command` の代わりに `url` を設定します。CLIは `chimera mcp add NAME --url https://host.example/mcp` を受け付けます。`--token-env ENVIRONMENT_VARIABLE` で認証すると、bearerトークンを実行時に解決し、その値を `mcp.json` に保存しません。

OAuthのauthorization-code + PKCEには、`mcp.json` で `oauth_authorization_url`、`oauth_token_url`、`oauth_client_id` を設定します(または対応する `chimera mcp add` のオプションを使います)。Chimeraは認可ページを開き、ループバックでコールバックを受け取り、自身のPKCEベリファイアでコードを交換して、得られたトークンをOSの認証情報ボールトに保存します。保存されたトークンは `Authorization: Bearer` ヘッダーとしてのみ送信され、ログに記録されることはありません。OSボールトのサポートにはオプションの `secrets` エクストラをインストールしてください。利用できるボールトがない場合、OAuthの設定はフェイルクローズします。

サインインは明示的なTest(`chimera mcp test NAME` または画面のTestボタン)からのみ実行されます。起動時、プールとオートロードがブラウザを開くことはありません: 保存済みトークンのないサーバーはスキップされ、ログがTestを実行するよう伝えます。認証情報(bearerトークンまたはOAuth交換)はhttps、またはループバック宛ての平文httpでのみ送信されます。トークン付きのリモート `http://` URLは拒否されます。

リモートサーバーは、stdioサーバーと同じ設定済みMCPツールインターフェース、レジストリ名前空間、長寿命プール、プローブコマンド、エラー処理、観測フェンスを通ります。リモートツールのメタデータと結果は、信頼できないサーバーのコンテンツとして扱ってください。

## クライアントのエクストラをインストールする

MCPクライアントはオプションのエクストラの背後にあり、コアを軽量に保っています。

```bash
uv sync --extra mcp
```

ほとんどのサーバーはNodeパッケージなので、`npx`(Node.jsに同梱)も必要です。

## 60秒スモークテスト(認証情報不要)

リファレンスのファイルシステムサーバーはトークンを一切必要としません — 選択したディレクトリ上で読み書きツールを公開するだけです。

```python
from chimera.integrations import connect_stdio
from chimera.tools import default_registry

connector = connect_stdio(
    "fs",
    "npx", ["-y", "@modelcontextprotocol/server-filesystem", "./sandbox_dir"],
    name_prefix="fs_",   # avoid clashes with built-in tool names
)

registry = default_registry()
for tool in connector.tools():
    registry.register(tool)

print(registry.names())  # built-ins + fs_read_file, fs_write_file, fs_list_directory...
```

そのレジストリを `Agent` に渡せば(または完全なループについては `examples/mcp_github.py` を参照)、モデルはそのサーバーのツールを他のツールと同じように呼び出せるようになります。

## 実際のサーバー: GitHub

```python
import os
from chimera.integrations import connect_stdio

connector = connect_stdio(
    "github",
    "npx", ["-y", "@modelcontextprotocol/server-github"],
    env={"GITHUB_PERSONAL_ACCESS_TOKEN": os.environ["GITHUB_PERSONAL_ACCESS_TOKEN"]},
    name_prefix="gh_",
)
```

それが統合のすべてです: 約26個のGitHubツール(リポジトリの検索、ファイルの読み取り、Issueの一覧、PRの作成など)がレジストリに現れます。エンドツーエンドで実行可能なバージョン:
[`examples/mcp_github.py`](https://github.com/brcampidelli/chimera-agent/blob/main/examples/mcp_github.py)。

## 安全層への組み込まれ方

MCPツールは通常の `Tool` オブジェクトなので、すべてが組み合わさります。

- **セッションごとの許可リスト** — `restrict_registry(registry, allow=["gh_search_repositories", ...])` は、この実行に必要なMCPツールだけを許可します。許可されていないものはモデルに一切届きません。
- **ガバナンスカーネル** — `govern_registry(...)` は、他のシェルコマンドと同様にMCP呼び出しを allow/warn/review/block でゲートします。
- **汚染台帳** — `ledger_registry(...)` でラップすると、MCPの取得が記録されます。ただし、`FETCH_TOOLS` に名前があるツールだけが現時点で自動分類されることに注意してください。そのため、MCPのコンテンツは信頼できないものとして扱い、サーバーが外部データを取得する場合は `--taint --guard` セマンティクスでの実行を優先してください。
- **サーバーの `instructions`** — サーバーが `initialize` で返すテキストは意図して破棄されます。信頼できないサーバーのテキストであり、フェンスで囲んだ取得結果のようにデータとして印を付ける仕組みがないためです。その代償として、サーバーの使い方の案内はモデルに届きません。ただし、それを渡すホストもそれに頼るべきではありません(arXiv 2608.08467: 検索ツールがあると、24モデル中9モデルがサーバー指示内の参照で15%未満に落ちた)。テイントの下でフェンス付きデータとして渡すことは未対応です。これを破棄しても、サーバーが書いたテキストに対する境界には**なりません**。同じサーバーのツール名と説明は、サーバーが書いたまま、フェンスなしでモデルに届くからです。接続したサーバーは、その言葉をモデルが読むサーバーです。
- **マニフェストの固定** — サーバーを初めてマウントしたとき、そのツールの名前・説明・input schema が `mcp.json` の隣の `mcp_pins.json` に記録されます。後のマウントで一覧が少しでも違えば、そのサーバーは**保留**されます。アプリ、`chimera serve` とそのボット(共有 MCP プールを通してマウントするすべて)は、`chimera mcp approve NAME` または MCP 画面で、旧テキストと新テキストを見たうえで変更を承認するまで、そのサーバーをマウントしません。`chimera mcp list` は保留中のサーバーを示します。マウントされるツールは確認された一覧そのものなので、サーバーが確認には一つのテキストを、モデルには別のテキストを返すことはできません。`chimera mcp add/remove` またはアプリでサーバーを追加・削除すると、その固定は忘れられます(次のマウントは再び初回扱い)。固定は初回利用時の信頼です。**変わった**説明は捕まえますが、最初から敵対的だった説明は捕まえません。
  **対象外:** 上の Python API。`connect_stdio` はサーバーが一覧に出したものをそのままマウントし、`autoload_into_registry` も、固定を確認すべきファイルである `mcp_path` を渡さない限り同じです。
- **選択の誘導表現** — `chimera mcp test` と MCP 画面は、モデルがどのツールを選ぶかを誘導しようとする表現("always use this tool"、"do not use other tools"、"ignore previous instructions"、`<IMPORTANT>`)をツールに注記します。説明とすべてのパラメーター説明を読みます。注記にすぎず、何も拒否しません。誠実なサーバーでどれだけ反応するかは測定されていません。

## MCPサーバー*としての*Chimera

上記のクライアントはChimeraが他のツールを呼び出せるようにするものです。逆も可能です: ChimeraをMCPサーバー**として**実行し、どのMCPクライアント — Claude Desktop、IDE、別のエージェント — もエンジン全体を3つのツールとして呼び出せるようにします。

```bash
uv sync --extra mcp
chimera serve --mcp        # speaks MCP over stdio
```

これは以下を公開します。

| ツール | 内容 |
| --- | --- |
| `chimera_solve` | 計画+検証または差し戻しでタスクを自律的に解決し、答えを返す。 |
| `chimera_fuse` | LLM-Fusionエンジン(パネル → ジャッジ → シンセサイザー)を通じてプロンプトに答える。 |
| `chimera_memory_search` | Chimeraの長期記憶を検索し、上位の事実を返す。 |

MCPクライアントをstdioサーバーとしてこれに向けてください。Claude Desktopの場合、その設定に追加します。

```json
{
  "mcpServers": {
    "chimera": { "command": "chimera", "args": ["serve", "--mcp"] }
  }
}
```

`--mcp` は `chimera_solve`/`chimera_fuse` にプロバイダーキーを必要とします(メモリ検索はキーなしで動作します)。`--fuse` を追加すると、ソルバーの深い応答をフュージョン経由でルーティングします。`--no-memory` は想起をスキップします。ワイヤーはstdioなので、すべてのログはstderrに出力されます — stdoutはプロトコルのみを運びます。

## Claude にデスクトップアプリを操作させる

`chimera serve --mcp` は独自のエージェントを組み立てます。`chimera mcp desktop` は何も組み立てません。
**すでに開いている**デスクトップアプリのリモコンなので、Claude はあなたと同じ会話・実行・承認を見て、
Claude が始めたことはアプリのガバナンスの下、アプリの画面で動きます。

1. アプリで **設定 → Claude** を開き、**Claude にこのアプリの操作を許可する** をオンにします。
2. Claude Code にサーバーを登録します（または同じコマンドを Claude Desktop の設定に追加します）:

   ```bash
   claude mcp add chimera-desktop -- chimera mcp desktop
   ```

最初のスイッチがオンなら、Claude は会話（`desktop_send`）、実行、バッチ、ボード、cron ジョブを
読んだり始めたりでき、メモリを検索・編集し、ファイルと git の状態を読めます。Claude が始めた実行には
あなたが設定した姿勢が付き、それを広げようとする要求——`verify` コマンド、ホストでの実行、別の
エージェント、自動承認——は拒否されます。承認はあなたのものです: `desktop_approvals` は一覧を返す
だけです。ターンが承認で止まると、`desktop_send` はあなたを待っていることをすぐに返し、ターンは
アプリ内で続きます。どう終わったかは `desktop_job` が伝えます。

2 つ目のスイッチ **フルコントロール** は `desktop_approve`（承認とゲート付きステップへの回答）と
`desktop_settings`（設定とエージェントの ID の編集）を加えます。オンにすると Claude はあなた抜きで
操作を承認でき、エージェントが読んだプロンプトインジェクション入りのページやメッセージがそれを
引き起こす可能性があります。オフの間はこの 2 つのツールは一覧に出ず、呼ばれてもアプリが拒否します。

どちらのスイッチがオンでも、あなたの判断として残るものがあります。どのモデルが答えるか（各モデル
設定、フォールバックチェーン、フュージョンのパネル・審査役・統合役、コストモード、カスケード、
検証済み回答）と、アプリが予定ジョブを実行するかどうかは、Claude は *提案* しかできません。何も
書き込まれず、アプリが各設定の現在の値と提案された値を示すカードを出し、あなたがそこで承認または
拒否します。Claude はどの経路からもこのカードに答えられません。承認前に設定が変わっていれば、何も
適用されません。また、フォルダーへのコマンド許可、Runner でのコマンド実行、メッセージングボットの
起動、ツール権限付きエージェントの保存は、ブリッジ経由では一切拒否されます。これらはアプリで
あなたが行います。

また、どちらのスイッチでも、Claude が開始する実行は、あなたが設定したモデルで動き、あなたが設定した
姿勢より先には届きません。モデル、ロール計画、プロファイル、フュージョンのパネル、別のエージェントを
指定する要求は拒否されます。より広い姿勢（より広い到達範囲、より緩い承認、シェルを許可していない
場所でのホスト実行や `verify` コマンド、自動承認）も同様です。実行により少ないこと（読み取りのみ、
または常に承認）を求めるのは許可されます。

どちらのスイッチでも許されないこと: API キー、トークン、webhook の読み書き。設定の編集は認証情報の
名前を拒否し、キーや共有リンクを運ぶルートには届かず、認証情報のファイル（`.env`、秘密鍵）は読み・
書き・検索のいずれもできず、すべての結果から認証情報の値が除かれます。ワークスペースをアプリの
データフォルダや、それを含むフォルダ（たとえばホームディレクトリ）に向けることもできません。
承認への回答はそこに保存されており、そこに書かれたファイルは承認に答えてしまうからです。

接続の仕組み: スイッチがオンの間、アプリは `~/.chimera/desktop-bridge.json`（ループバック API の URL
とランダムなトークン。POSIX ではあなただけが読め、Windows ではプロファイル内）を書きます。スイッチを
オフにするかアプリを閉じるとファイルは削除され、トークンは無効になります。アプリが閉じていると、
どのツールも "Chimera desktop is not running, or 'Allow Claude to operate this app' is off in
Settings." と答えます。Claude は接続時にツール一覧を取得するので、**フルコントロール** を切り替えた
後はサーバーを再接続（Claude Code の `/mcp`）して新しい一覧を見てください。

## A2A(エージェント間通信)を話す

MCPはエージェントを*ツール*に接続します。**A2A**(Agent2Agent、Linux Foundation)はエージェントを*互いに*接続します — LangGraph、CrewAI、AutoGenではネイティブです。Chimeraもこれを話すので、LangGraph/CrewAIオーケストレーターはChimeraにタスクを委任し、完了した結果を受け取ることができます。

```bash
chimera a2a-card                       # print the Agent Card JSON
chimera serve --a2a                    # HTTP gateway + A2A endpoint
```

`serve --a2a` はHTTPサーバーに2つのルートを追加します。

| ルート | 目的 |
| --- | --- |
| `GET /.well-known/agent.json` | Agent Card — アイデンティティ+公開されたスキル(solve、fuse)。 |
| `POST /a2a` | JSON-RPC 2.0のタスクライフサイクル: `message/send`、`message/stream`、`tasks/get`、`tasks/cancel`。 |

クライアントはテキストパートを含む `message/send` を送信します。Chimeraは自律エージェントを実行し、答えをエージェントメッセージとして運ぶ `completed`(または `failed`)タスクを返します。あるいは `message/stream` を送信すると**Server-Sent Events**ストリームを受け取ります: 最初に `working` 状態のタスク、実行が完了すると `completed`/`failed` タスクが続きます — つまりオーケストレーターはポーリングなしで進捗を確認できます。エージェントカードは `capabilities.streaming: true` を公開します。

**正直なスコープ:** ストリームは現在、ステップごとのトークン差分ではなく2つのイベント(working → final)を発行します。プッシュ通知は実装されていません。それでも準拠した、ポーリング不要なストリームです — LangGraph/CrewAIアプリのファーストクラスなストリーム可能ノードとしては十分です。

## トラブルシューティング

- `TimeoutError: MCP server ... did not become ready` — コマンドが起動しませんでした。ターミナルで同じ `npx ...` の行を手動で実行し、そのエラーを確認してください(トークンの欠落、Nodeの欠落、初回実行時のパッケージダウンロードが遅い場合は `connect_timeout` を増やしてください)。
- `ModuleNotFoundError: mcp` — エクストラをインストールしてください: `uv sync --extra mcp`。
- ツール名の衝突 — 常に `name_prefix` を渡してください。
- セッションはスクリプトの生存期間中、サーバーをサブプロセスとして実行します。`connector` のセッションの `close()` を呼ぶ(またはプロセスを終了させる)ことで解体してください。
