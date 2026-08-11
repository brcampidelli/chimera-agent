---
source_sha256: 32a7d80a9e508e738b930dbf71e8edcc9a15e2366ad7c15ba6033a2ff5833b56
---

# MCPサーバーの接続

MCP(Model Context Protocol)は外部ツールをエージェントに接続する標準的な方法です — GitHub、ファイルシステム、Notion、データベース、その他数百のサーバーがこれを話します。Chimeraはファーストクラスの MCPクライアントを持ちます: どのサーバーのツールも通常のChimeraツールとなり、組み込みツールと同じレジストリに配置され、同じ許可リスト/カーネル/台帳の各層によって統治されます。

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
