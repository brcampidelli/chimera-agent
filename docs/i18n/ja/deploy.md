---
source_sha256: bec93da7acb7d3a5290f324d0095c1da6c043ab5acb014a8cbcf4a63ada31510
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

### スケジュールが黙っていることを尋ねる

```bash
chimera cron doctor
```

スケジュールは2通りの黙り方をしますが、尋ねるまではどちらも同じに見えます — 何も期限が来ていないスケジュールのように:

- **何も実行されなかった。** デーモンが死んだ、コンテナが一度も再起動されなかった、ホストが眠った。例外もログ行も判定もありません。ここにある他のあらゆる誠実さの仕組みは*実行が起きたことより下流*に位置するので、どれにも出番が回ってきません。
- **すべて実行されてすべて失敗した。** デーモンは生きていて、`last_run` は1分前、スケジュールは前に進んでいます — それでいて1か月のあいだディスパッチが毎回失敗しています。こちらのほうが1つ目より*健康そうに*読めます。健康に見えるフィールドが記録しているのは試行であって、結果ではないからです。

`cron doctor` は両方の問いを立て、異なる助言をします。直し方に共通点がまったくないからです: 遅延はデーモンの話、失敗はジョブの話です。何かが失敗しているとき `chimera cron list` が1行を出力するので、このコマンドの存在を知らなくても気づけます。

**これが何ではないか。** これは問いであって、見張りではありません: プロセスが落ちているあいだ、ここでは何も気づきません — クラッシュしたプロセスが自分のクラッシュをログに残せないのと同じ理由です。何かが尋ねたその瞬間に正直に答えます — シェル、アプリ、次回の起動。本物の監視役には自分の時計と自分の生存性が要り、それは別個の決定であって、[issue #26として追跡されています](https://github.com/brcampidelli/chimera-agent/issues/26)。答えではなく警報がほしい場合は、ホスト自身のcronから実行してください:

```cron
*/30 * * * * cd /opt/chimera && .venv/bin/chimera cron doctor | mail -s "chimera" you@example.com
```

これが機能するのは、Chimera以外の何かに監督されているからです — それこそが要点です。

---

## 4. ヘルス、バックアップ、セキュリティ

- **ヘルス:** `GET /health` は `{"ok": true}` を返します。Composeにはヘルスチェックが組み込まれています。
- **バックアップ:** `chimera-data` ボリューム(Docker)または `CHIMERA_HOME` ディレクトリ(systemd)をバックアップしてください — それがすべての永続的な状態です。例: `docker run --rm -v chimera-data:/d -v $PWD:/b busybox tar czf /b/chimera-state.tgz -C /d .`
- **シークレット:** キーは `.env`(git管理外)に保管してください。決してイメージに焼き込まないでください。
- **公開範囲:** ゲートウェイを `0.0.0.0` にバインドするのはファイアウォール/リバースプロキシの背後のみにしてください。**`CHIMERA_SERVER_TOKEN`** を設定して、HTTPゲートウェイとデスクトップAPIで `Authorization: Bearer <token>` を必須にしてください(デスクトップUIには、ループバッククライアントに対してのみ自動的にトークンが渡されるため、リモートに公開されたインスタンスは独自の認証の背後にとどまります)。認証はオプトインでデフォルトは空なので、この変数がなければ認証はありません — ポートを制限するか、webhookパスのみを公開してください。デスクトップアプリからこのインスタンスに到達する方法は [§5](#5-reaching-this-instance-from-the-desktop-app) を参照してください。
- **サンドボックス化:** `CHIMERA_SANDBOX=docker` を設定すると、シェル/コードツールをホストではなく使い捨てのコンテナ内で実行します。
- **無人でのホスト実行:** 2026-07-20以降、ヘッドレス実行はデフォルトの `CHIMERA_HOST_EXEC=ask` の下でホストコマンドを**拒否します**(確認できるTTYがないため)。エージェントが本当にホスト上でシェルを実行する必要があるデプロイでは、意図的に `CHIMERA_HOST_EXEC=allow` を設定します。より安全な選択肢は `CHIMERA_SANDBOX=docker` で、コンテナが実際に隔離するためこのゲートはスキップされます。同様に、APIサーバーは汚染絞り込み(`CHIMERA_TAINT_NARROW=1`)を武装します: エージェントが信頼できないコンテンツを読んだ後、実行/書き込み/送信ツールはフェイルクローズ(失敗時に閉じる)します。自律的に動作し続けるには `0` に設定してください。

---

## 5. デスクトップアプリからこのインスタンスに到達する

デスクトップアプリはデフォルトでは、自分のマシン上で自ら起動したChimeraと話します。v0.44からは、自分で運用しているもの — このVPS — を指すこともできるようになり、アプリは夜通しあなたのcronジョブをこなしているエージェントを覗く窓になります。

**ポートを開ける前にこの部分を読んでください。** 公開されるのはダッシュボードではありません。このアプリのどの画面も操作面です: シェルを実行し、ファイルを編集し、自律タスクのボードをディスパッチし、設定を変更します。トークンなしでインターネットから到達できるインスタンスは「誰かが眺めるかもしれないChimera」ではありません — アドレスを見つけた誰もがコマンドを実行できるマシンであり、その支払いはあなたのプロバイダーキーです。

3つのことが成り立っている必要があり、最初の2つがなければアプリは接続を拒否します:

**1 — TLS.** 本物の証明書を持つリバースプロキシの背後に置いてください(Caddyなら取得してくれます):

```caddyfile
chimera.seudominio.com {
    reverse_proxy 127.0.0.1:8765
}
```

自分のマシンの外側では、アプリは `https` でないアドレスを拒否します。トークンは**すべての**リクエストで `Authorization` ヘッダーに載って旅をするからです — 素のhttpでは、それはあなたとサーバーのあいだのすべてのホップに手渡される資格情報であり、その最中に画面上でおかしく見えるものは何もありません。

**2 — トークン。** 認証はオプトインでデフォルトは空です:

```bash
CHIMERA_SERVER_TOKEN=$(openssl rand -hex 32)
```

それを `.env` に入れて再起動し、同じ値をアプリに貼り付けてください。アプリが上記の理由でトークンなしのリモートアドレスを拒否するのは、トークンのないインスタンスが見つけた者に対して開いているからです。

サーバーが意図的に**しない**ことに注目してください: リモートクライアントがUIを求めたとき、サーバーはトークン*なし*でページを配信します。トークンがネットワーク越しに渡されることは決してありません — 一度だけ、帯域外で、自分のクライアントにコピーします。アプリにそのための入力欄があるのはそのためです。

**3 — 自分のアプリのオリジン。** アプリは自身のローカルなsidecarから配信されるため、このインスタンスへのリクエストはクロスオリジンになり、このインスタンスがそのオリジンを名指ししない限りブラウザは応答を捨てます:

```bash
CHIMERA_ALLOWED_ORIGINS=http://127.0.0.1:45813
```

接続に失敗すると、アプリが正確な値を表示します — エラーメッセージに載っていて、そのままコピーできます。ポートはインストールごとに安定していて(v0.43から起動間で記憶されます)、接続元のマシンごとに一度設定すれば済みます。複数はカンマ区切りです。

**この設定はセキュリティ境界ではなく、そう読んではいけません。** CORSが決めるのはどの*ページ*が応答を読めるかであって、誰が*呼べる*かについては何も決めません。ゲートはトークンです。トークンを設定せずにオリジンを名指ししても何も守られません — 保護されていないインスタンスが `curl` に加えてブラウザからも到達できるようになるだけです。

デフォルトは空なので、誰も設定していないインスタンスは以前とまったく同じように振る舞います。

### 失敗したときアプリが伝えること

- **「トークンが拒否されました」** — アドレスもオリジンも正しく、値が間違っています。
- **「到達できませんでした」** — アドレスが間違っているか、オリジンが許可されていないかのどちらかです。ブラウザはどちらなのかを意図的に言わないため、アプリは推測せず両方を挙げ、許可すべきオリジンを渡します。
- **バージョン警告** — アプリは自分のバックエンドのバージョンをこちらと比べ、両方の数字を伝えます。拒否はしません: 1リリース遅れのサーバーはたいてい動きますし、拒否すればそれを直すのに必要な画面から締め出すことになるからです。古い側には存在しないエンドポイントがあるかもしれません。

### さらに安全に

公開ポートを完全に飛ばす手もあります: WireGuardかTailscaleのtailnet経由でVPSに到達し、アプリをプライベートアドレスに向けてください。それでもトークンは重要です — tailnetは小さい部屋であって、空の部屋ではありません。

---

## 6. 正直な状況

Chimeraは**アルファ版**です。これはデプロイされ動作し、cronデーモンによってプロアクティブになりますが、まだ**本番実績はありません**。低リスクなcronから始め、`logs` を監視し、実システムに触れる何かに対してはガバナンスのガードレール(`solve` の `--guard`、`CHIMERA_SANDBOX=docker`)を心に留めておいてください。

## これらのページが公開されている場所

これらのファイルは**chimeraagent.space**上のドキュメントのソースであり、ビルド時にこのディレクトリから直接レンダリングされます。ここのMarkdownを編集すればサイトに反映されます。同期を保つべき2つ目のコピーは存在しません。

かつて `mkdocs.yml` にあったMkDocsの設定は削除されました。それは完成していました — テーマ、ナビゲーション、10ページ — しかし一度も公開されたことはありませんでした: ワークフローも `gh-pages` ブランチも存在しなかったため、かつてこの場所にあったデプロイ手順は、存在しないサイトについて説明していたことになります。誰も実行しない設定は、設定が無いよりも悪いものです。なぜなら次の人がそのナビゲーションを編集しても、なぜ何も変わらないのか分からないからです。
