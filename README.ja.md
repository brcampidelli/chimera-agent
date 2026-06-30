<div align="center">

<img src="assets/logo-wide.png" alt="Chimera ロゴ" width="460" />

# Chimera

**推論コアに LLM フュージョン（Fusion）エンジンを据えた、オープンソースの自己進化型 AI エージェント。**

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![CI](https://github.com/brcampidelli/chimera-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/brcampidelli/chimera-agent/actions/workflows/ci.yml)
[![Checked with mypy](https://img.shields.io/badge/mypy-strict-2a6db2.svg)](https://mypy-lang.org/)
[![Linted with Ruff](https://img.shields.io/badge/lint-ruff-261230.svg)](https://github.com/astral-sh/ruff)
[![Discord](https://img.shields.io/badge/Discord-参加-5865F2.svg?logo=discord&logoColor=white)](https://discord.gg/ACvBbrmguV)
![Status: alpha](https://img.shields.io/badge/status-alpha-orange.svg)

<sub><a href="README.md">English</a> · <a href="README.pt-BR.md">Português</a> · <a href="README.es.md">Español</a> · <a href="README.de.md">Deutsch</a> · <a href="README.fr.md">Français</a> · <a href="README.zh-CN.md">中文</a> · <b>日本語</b></sub>

</div>

Chimera は単一のフロンティアモデルに頼るのではなく、**リクエストごとに複数の LLM を融合**します
——OpenRouter Fusion に着想を得た **パネル → ジャッジ → シンセサイザー** のパイプラインです。
さらに **時間とともに自己改善**（記憶 → スキル → モデル）し、今日のエージェントを制限する
*継続的進化による劣化* に耐えます。

> **ステータス：** アーリーアルファ。ビルド計画の全 8 マイルストーン（M0–M7）を実装済み：
> Tier 1–4 + フュージョンエンジン + 自己進化 + ガバナンスカーネル。
> 158 テスト · `mypy --strict` クリーン · `ruff` クリーン。

---

## なぜ Chimera か

既存のフレームワークはそれぞれ **1 つの軸** で強みを持ちます。Hermes/OpenClaw はスキルを
進化させますが単一モデルです。CrewAI/LangGraph はオーケストレーションに優れますが学習しません。
TrustClaw/NemoClaw/ZeroClaw はセキュリティ/サンドボックスを備えますが進化しません。
**Chimera はこの 4 つを 1 つに統合します：**

- 🧬 **推論としてのフュージョン** —— パネル→ジャッジ→シンセサイザーのエンジンが推論コアであり、後付けではありません。向上は *合成プロセスそのもの* から生まれ、モデルの多様性だけによるものではありません。
- 🪜 **4 つの能力ティアを 1 本の進化経路に** —— 拡張ツール → 単一タスク自律 → マルチエージェントチーム → 自己進化エコシステム。
- ♻️ **多層の自己進化** が継続的進化による劣化に明確に対処します（状態の外部化、ドリフト耐性のあるコンテキスト、verify-or-revert、経験バッファ）。
- 🛡️ **自身も改善するガバナンスカーネル** —— allow/warn/block/review。静的に検証される自己改変サーフェスを備えます。

## 機能

- **LLM フュージョンエンジン** —— プロバイダ非依存のフロンティア + オープンモデルのパネル、合意/矛盾/盲点を可視化するジャッジ、そしてシンセサイザー。**コストを意識したルーター** が割に合うときだけ融合します（ツール呼び出しのターンは単一モデルのまま）。
- **Tier-2 自律** —— 計画 → 実行 → Manager レビュー → **verify-or-revert**（ワークスペースのスナップショット/復元 + コマンド検証器）。git ライクな経験バッファを備えます。
- **自己進化** —— Memory Manager（ADD/UPDATE/DELETE/NOOP の重複排除）、*自らスキルを書いてテストする* スキル進化器（提案 → テスト → 採用/破棄）、自己学習する cron、そして劣化を測定する**継続的進化ベンチマーク**。
- **マルチエージェントチーム** —— 役割の専門化、逐次型およびスーパーバイザー型 crew、MOC によるメッセージ統合、共有メモリ、並列レビュー。
- **ガバナンスと安全性** —— 自己改善するトラストカーネル、自己改変サーフェス用の静的検証器、追記専用の監査ログ、ガバナンス下のツール。
- **連携（Integrations）** —— 一流の **MCP** クライアント + **OpenAPI/REST → ツール** インポーターで、任意のプラットフォームや API を追加できます。
- **cron とプロアクティブ性** —— 人が割り当てた、および自己学習したスケジュールタスク。
- **マイグレーション** —— Hermes Agent / OpenClaw から設定・スキル・**長期記憶**をインポート（記憶は*マージ*され、上書きはしません）。
- **CLI ファースト** —— すべてターミナルで動作。LiteLLM/OpenRouter によりプロバイダ非依存。

## クイックスタート

Python **3.11+**（3.12+ 推奨）と [uv](https://docs.astral.sh/uv/) が必要です。

```bash
uv sync --extra dev
cp .env.example .env        # 少なくとも 1 つのプロバイダキーを設定（OpenRouter 推奨）
uv run chimera doctor       # 環境を確認
```

## コマンド

```bash
chimera doctor / models               # ステータスと設定
chimera run "PROMPT"                   # 単発の Tier-1 補完
chimera fuse "PROMPT" --show-panel     # LLM フュージョン：パネル -> ジャッジ -> シンセサイザー
chimera agent "TASK" --fuse --guard    # ReAct エージェントループ（ガバナンス下のツール呼び出し）
chimera solve "TASK" --verify "pytest -q"   # Tier-2 自律：計画 -> verify-or-revert
chimera crew "TASK" --mode supervisor  # Tier-3 マルチエージェント crew
chimera meta "an agent for X"          # Tier-4 メタエージェント：専用エージェントを設計
chimera memory add "永続的な事実"        # キュレーションされた長期記憶（重複排除）
chimera cron add NAME "0 9 * * *" "run report"   # タスクをスケジュール
chimera cron learn                     # 繰り返しタスクから cron を提案（無効状態）
chimera bench                          # 継続的進化ベンチマーク
chimera guard "rm -rf /"               # ガバナンス判定をプレビュー
chimera migrate hermes ~/.hermes --apply   # 設定 + スキルをインポートし記憶をマージ
```

## アーキテクチャ

```
chimera/
  core/          エージェントループ（ReAct）+ Tier-2 自律（計画、verify-or-revert、スーパーバイザー）
  fusion/        パネル -> ジャッジ -> シンセサイザー + コスト意識ルーター
  memory/        working / episodic / semantic / persona + Memory Manager
  skills/        組み込みライブラリ + skill-context 取得
  evolution/     学習スキル進化器、経験バッファ
  governance/    トラストカーネル（allow/warn/block/review）、静的検証器、監査、ガバナンス下ツール
  orchestration/ 役割、逐次型 & スーパーバイザー型 crew、MOC 通信
  ecosystem/     メタエージェント、変更ペースのガバナンス、トラジェクトリ収集
  tools/         ネイティブツール（ファイル、shell、http）
  integrations/  MCP クライアント + OpenAPI->ツール インポーター
  scheduler/     cron（割り当て + 自己学習）+ SOP エンジン
  migration/     Hermes/OpenClaw からのインポート（設定、スキル、記憶マージ）
  providers/     LLM アダプタ（LiteLLM / OpenRouter）
  eval/          継続的進化ベンチマーク、デモタスク
  cli/           `chimera` コマンド（CLI ファースト）
```

完全な設計とその根拠となる研究は [docs/architecture.md](docs/architecture.md) を参照してください。

## ロードマップ

| マイルストーン | ステータス |
|---|---|
| M0 — 基盤（ゲートウェイ、設定、CLI） | ✅ |
| M1 — Tier 1 + ツール/スキル/連携/cron/マイグレーション | ✅ |
| M2 — LLM フュージョンエンジン + コスト意識ルーター | ✅ |
| M3 — Tier 2 自律（verify-or-revert） | ✅ |
| M4 — 自己進化（記憶、スキル、学習 cron、ベンチマーク） | ✅ |
| M5 — ガバナンスカーネル | ✅ |
| M6 — Tier 3 マルチエージェントチーム | ✅ |
| M7 — Tier 4 自己進化エコシステム | ✅ |

次のステップ：実モデルでの大規模検証、継続的進化スイートの拡張、オプションの LangGraph 永続化バックエンド。

## 開発

```bash
uv run ruff check .      # lint
uv run mypy chimera      # 型チェック（strict）
uv run pytest -q         # テスト
```

[CONTRIBUTING.md](CONTRIBUTING.md) と [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) を参照してください。
セキュリティの問題：[SECURITY.md](SECURITY.md) を参照してください。

## コミュニティ

**[Discord](https://discord.gg/ACvBbrmguV)** での会話に参加してください——質問・アイデア・コントリビューション歓迎です。

## ライセンス

[Apache-2.0](LICENSE)。
