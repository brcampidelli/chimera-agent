---
source_sha256: 472844a34ca189775b1f61de23b6ed2a36820a935b8465106a9f283a134caa33
---

# サーバー(VPS)へのChimeraのデプロイ

Chimeraは長時間稼働する**ゲートウェイ**プロセスとして動作します。`--cron` を追加すると、実際の時計に基づいてスケジュールされたジョブも発火するようになり、(メッセージを受け取った時だけでなく)*時間通りに行動*します。このガイドでは、5ドルのVPSデプロイを2通りの方法で扱います: **Docker Compose**(推奨)または **systemd**。

状態 — 長期記憶、cronジョブ、トラジェクトリ、監査ログ — は `CHIMERA_HOME`(ディレクトリ)に存在します。これを永続化すれば(Dockerボリュームまたは実際のパス)、エージェントは再起動を生き延びます。

---

## 0. 前提条件

- Linux VPS(単一エージェントには1 vCPU / 1 GB RAMで十分です)。
- 少なくとも1つのプロバイダーキー。最も安く始めるならOpenRouterキーです。
- パブリックな受信webhook(WhatsApp Cloud API、`POST /webhook/<hook>`)には、ドメイン + TLS付きのリバースプロキシ(CaddyまたはNginx)が必要です。Discord/Telegram/Slack/Signalは送信接続なので不要です。

テンプレートから環境ファイルを作成し、キーを入力してください。

```bash
cp .env.example .env
# edit .env — e.g. set CHIMERA_OPENROUTER_KEYS=sk-or-...
```

---

## 1. Docker Compose(推奨)

```bash
docker compose up -d       # build + run; restarts on crash and on reboot
docker compose logs -f     # watch it
docker compose ps          # status + health
```

これは `chimera serve --host 0.0.0.0 --cron` を実行します: HTTPゲートウェイ(`/chat`、`/webhook/<hook>`、`/health`)**に加えて** cronデーモンです。状態は `chimera-data` ボリュームに永続化されます。

**チャットプラットフォームを配信する**(Discordの例) — `.env` にトークンを設定し、`docker-compose.yml` のコマンドを上書きします。

```yaml
    command: ["serve", "--host", "0.0.0.0", "--cron", "--discord"]
```

そして再度 `docker compose up -d` します。(Telegram/Slack/Signalも各フラグで同様に動作します。それぞれに対応する `CHIMERA_*` トークンが必要です — `.env.example` を参照してください。)

**新しいバージョンへの更新:**

```bash
git pull && docker compose up -d --build
```

---

## 2. systemd(Dockerなし)

ホスト上のvirtualenvにインストールします。

```bash
git clone https://github.com/brcampidelli/chimera-agent.git /opt/chimera
cd /opt/chimera
python3 -m venv .venv && . .venv/bin/activate
pip install '.[messaging,mcp]'
cp .env.example .env   # then edit it
```

`/etc/systemd/system/chimera.service` を作成します。

```ini
[Unit]
Description=Chimera Agent gateway + cron daemon
After=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/chimera
EnvironmentFile=/opt/chimera/.env
Environment=CHIMERA_HOME=/opt/chimera/state
ExecStart=/opt/chimera/.venv/bin/chimera serve --host 0.0.0.0 --cron
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now chimera
sudo systemctl status chimera
journalctl -u chimera -f
```

---

## 3. プロアクティブな作業をスケジュールする(`--cron` デーモン)

`--cron` はスケジュールした**ジョブを実行するだけ**です。CLIで追加してください(`CHIMERA_HOME` に永続化されます)。

```bash
chimera cron add "morning-brief" "0 8 * * *" "Summarize overnight news and post it."
chimera cron add "nightly-backup" "0 3 * * *" "Back up the important files."
chimera cron list
```

Docker内では:

```bash
docker compose exec chimera chimera cron add "morning-brief" "0 8 * * *" "..."
```

デーモンは `--cron-tick` 秒(デフォルト30)ごとにティックし、期限が来た各ジョブのアクションをエージェント経由でディスパッチします。失敗したジョブはログに記録され、デーモンを止めることは決してありません。

---

## 4. ヘルス、バックアップ、セキュリティ

- **ヘルス:** `GET /health` は `{"ok": true}` を返します。Composeにはヘルスチェックが組み込まれています。
- **バックアップ:** `chimera-data` ボリューム(Docker)または `CHIMERA_HOME` ディレクトリ(systemd)をバックアップしてください — それがすべての永続的な状態です。例: `docker run --rm -v chimera-data:/d -v $PWD:/b busybox tar czf /b/chimera-state.tgz -C /d .`
- **シークレット:** キーは `.env`(git管理外)に保管してください。決してイメージに焼き込まないでください。
- **公開範囲:** ゲートウェイを `0.0.0.0` にバインドするのはファイアウォール/リバースプロキシの背後のみにしてください。**`CHIMERA_SERVER_TOKEN`** を設定して、HTTPゲートウェイとデスクトップAPIで `Authorization: Bearer <token>` を必須にしてください(デスクトップUIには、ループバッククライアントに対してのみ自動的にトークンが渡されるため、リモートに公開されたインスタンスは独自の認証の背後にとどまります)。認証はオプトインでデフォルトは空なので、この変数がなければ認証はありません — ポートを制限するか、webhookパスのみを公開してください。
- **サンドボックス化:** `CHIMERA_SANDBOX=docker` を設定すると、シェル/コードツールをホストではなく使い捨てのコンテナ内で実行します。
- **無人でのホスト実行:** 2026-07-20以降、ヘッドレス実行はデフォルトの `CHIMERA_HOST_EXEC=ask` の下でホストコマンドを**拒否します**(確認できるTTYがないため)。エージェントが本当にホスト上でシェルを実行する必要があるデプロイでは、意図的に `CHIMERA_HOST_EXEC=allow` を設定します。より安全な選択肢は `CHIMERA_SANDBOX=docker` で、コンテナが実際に隔離するためこのゲートはスキップされます。同様に、APIサーバーは汚染絞り込み(`CHIMERA_TAINT_NARROW=1`)を武装します: エージェントが信頼できないコンテンツを読んだ後、実行/書き込み/送信ツールはフェイルクローズ(失敗時に閉じる)します。自律的に動作し続けるには `0` に設定してください。

---

## 5. 正直な状況

Chimeraは**アルファ版**です。これはデプロイされ動作し、cronデーモンによってプロアクティブになりますが、まだ**本番実績はありません**。低リスクなcronから始め、`logs` を監視し、実システムに触れる何かに対してはガバナンスのガードレール(`solve` の `--guard`、`CHIMERA_SANDBOX=docker`)を心に留めておいてください。

## これらのページが公開されている場所

これらのファイルは**chimeraagent.space**上のドキュメントのソースであり、ビルド時にこのディレクトリから直接レンダリングされます。ここのMarkdownを編集すればサイトに反映されます。同期を保つべき2つ目のコピーは存在しません。

かつて `mkdocs.yml` にあったMkDocsの設定は削除されました。それは完成していました — テーマ、ナビゲーション、10ページ — しかし一度も公開されたことはありませんでした: ワークフローも `gh-pages` ブランチも存在しなかったため、かつてこの場所にあったデプロイ手順は、存在しないサイトについて説明していたことになります。誰も実行しない設定は、設定が無いよりも悪いものです。なぜなら次の人がそのナビゲーションを編集しても、なぜ何も変わらないのか分からないからです。
