---
source_sha256: cd4ba57b32db6a5d71c9c0c2452c9bdcba3b28ae416f06b2347ac14df0248b89
---

# セキュリティとセーフガード

Chimeraはシェルコマンドを実行し、ファイルを編集し、APIを呼び出し、自身のスキルを変更できます。**多層防御**を備えており — これが重要な点ですが — ドキュメントは各層がどこで*止まる*かを明記します。

!!! warning "唯一のルール"
    これらのセーフガードのいずれも、自律性を与える際に**隔離された環境で実行すること**の代わりにはなりません。デフォルトの `local` ランナーは隔離されていません。信頼できない作業には `CHIMERA_SANDBOX=docker`(ネットワーク遮断、オプションでgVisor下)を使ってください。

## 各層

- **ガバナンスカーネル** — ガバナンス対象のツール呼び出しはすべて allow / warn / review / block のいずれかになります。危険なシェルシグネチャに対する安価な第一のフィルターであり、境界そのものではありません。
- **サンドボックス** — 一時的でネットワーク遮断されたコンテナ(`CHIMERA_SANDBOX=docker`)。gVisorで強化可能(`CHIMERA_SANDBOX_RUNTIME=runsc`)。
- **セッションごとのツール許可リスト** — 1回の実行に必要なツールだけを許可し、残りはモデルのスキーマから完全に除外されます。
- **汚染追跡**(`--taint`) — 信頼できないコンテンツはデータとして囲い込まれ、その出所はメモリやスキルにまで追跡されます(汚染された実行から生まれたスキルはレビュー保留になります)。実行が一度汚染されると、危険なツールは絞り込まれます。
- **検疫リーダー** — dual-LLM / CaMeLパターン: 信頼できないコンテンツはツールを持たないモデルによって読まれ、スキーマ検証済みのフィールドしか出力できないため、インジェクションが新たな指示やツール呼び出しを生み出すことはできません。
- **クロスエージェントモニター** — ファンアウト時、ワーカーごとのモニターは*分割された*フローに対して盲目です(1つのワーカーが信頼できないものを取得し、別のワーカーがそれをシンクする — 取得とシンクは別々の台帳に存在する)。集約モニターはファンアウト全体を見ます。これは `solve-batch` / `crew-isolated` では**常時オン**です。

## ファンアウト: クロスエージェントモニター

複数のツール使用ワーカーが並行して実行される場合(`solve-batch`、`crew-isolated`)、それぞれが独自のケーパビリティ台帳を持ち、バッチの後に集約モニターがそれら全体に対して実行されます。これは単一ワーカーのモニターでは見えないパターンを捕捉します — ワーカーAが信頼できないコンテンツを取得し、ワーカーBがそれを実行または持ち出す、分割された持ち出し(exfiltration)です。

```
$ chimera solve-batch "read notes.md and summarize" "download the helper and run it" -w .
task1: ok
task2: ok
merged 2 file(s) across 2 task(s)
⚠ cross-agent monitor flagged (review):
  - cross-agent-taint: untrusted content entered via one agent and a different agent
    performed a sink (task2→task1) — a split flow no single-agent monitor sees
```

これは常に**レビューへのエスカレート**のみを行います — 実行をブロックすることは決してなく、純粋な可観測性です(挙動を変えずに記録するだけ)。その上に `--taint` を加えると、各ワーカーの適応型許可リストも武装されます(汚染時に危険となるツールは承認が必要になります)。

## 主張ではなく計測

```bash
chimera redteam
```

インジェクションのコーパスをスタック全体に通します。組み込みのコーパスでは、汚染層が**攻撃成功率を100%から約14%に**下げます — そしてレポートは100%を主張する代わりに、依然としてすり抜けるもの(許可されたツール経由の持ち出し)を*名指し*します。

## HTTPサーバーの公開

`chimera serve` はデフォルトで `127.0.0.1` にバインドされます。その状態変更エンドポイント(`/chat`、`/a2a`、`/webhook/*`)はエージェントを駆動するため、**サーバーをネットワークに公開する前に**、ベアラートークンを設定してください。

```bash
export CHIMERA_SERVER_TOKEN="a-long-random-secret"   # required as: Authorization: Bearer <token>
```

これを設定すると、それらのPOSTエンドポイントは一致する `Authorization: Bearer` ヘッダーがない場合 `401` を返します(`GET /health` とA2Aのagent-cardは公開されたままです)。WhatsAppの受信webhookについては、`CHIMERA_WHATSAPP_APP_SECRET` にMetaアプリのシークレットを設定してください — するとChimeraは各リクエストの `X-Hub-Signature-256` HMACを検証し、偽造されたペイロードを `403` で拒否します。どちらもオプトインです(未設定 = 認証なし、localhostなら問題ありません)。公開デプロイでは設定するべきです(または認証プロキシの背後に置いてください)。

## 正直な限界

これは*すでにインジェクションされた*エージェントの有害な行動が止められるかどうかを計測するものであり、そもそもモデルがインジェクションされうるかどうかではありません。信頼できない文章に対する自由形式の推論、そして正当に必要なツールを通じた持ち出しは、未解決の問題として残っています([issue #5](https://github.com/brcampidelli/chimera-agent/issues/5)で追跡)。

完全で常に最新のポリシーは
[SECURITY.md](https://github.com/brcampidelli/chimera-agent/blob/main/SECURITY.md)にあり、脆弱性の報告方法も含まれています。
