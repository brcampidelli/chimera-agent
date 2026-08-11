---
source_sha256: fe1348e242b1851c75bb1525ecf723afe068c499ed372335aca5e281cc72ba04
---

# Chimera

オープンソース(Apache-2.0)で自己進化するAIエージェント。その推論コアはコスト意識型ルーターの背後で**複数のモデルを融合**する(パネル → ジャッジ → シンセサイザー)。さらにガバナンスカーネル、サンドボックス、学習するメモリを備える。

このサイトはタスク志向です。やりたいことを選んでください。

<div class="grid cards" markdown>

- **:material-rocket-launch: はじめる**
  インストールし、キーを追加し、5分で最初のタスクを実行する。
  [インストール&初回実行 →](usage.md)

- **:material-toolbox: 実際に何かをする**
  実行可能なレシピ: メールトリアージ、日次リサーチブリーフ、リポジトリのウォッチドッグ。
  [レシピ →](recipes.md)

- **:material-power-plug: ツールを接続する**
  任意のMCPサーバー(GitHub、ファイルシステムなど)を接続する。
  [MCPサーバー →](mcp.md)

- **:material-server: 運用する**
  小さなサーバーで24時間365日稼働させ、ジョブをスケジュールし、チャットに配信する。
  [デプロイ →](deploy.md)

- **:material-shield-lock: セキュリティ**
  ガバナンス、サンドボックス、汚染追跡 — そしてその正直な限界。
  [セキュリティ →](security.md)

- **:material-sitemap: 理解する**
  融合コア、進化、安全層がどう組み合わさっているか。
  [アーキテクチャ →](architecture.md)

</div>

## ワンライナー

```bash
uv sync --extra dev && uv run chimera init
```

その後、`chimera run "..."` を試すか、実際のレシピを試してください。

```bash
chimera workflow examples/email_triage/triage.yaml -w ./triage_workspace
```

## デフォルトで正直

Chimeraは**アルファ版**です。多層防御を備えていますが、ドキュメントは各セーフガードがどこで止まるかをはっきりと述べます — インジェクション対策でさえ、測定された数値を公開しています(`chimera redteam`)。[セキュリティ](security.md)を参照してください。
