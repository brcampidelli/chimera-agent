<div align="center">

<img src="assets/logo-wide.png" alt="Chimera ロゴ" width="460" />

# Chimera

**推論コアが LLM フュージョン・エンジンである、オープンソースの自己進化型 AI エージェント。**

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![CI](https://github.com/brcampidelli/chimera-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/brcampidelli/chimera-agent/actions/workflows/ci.yml)
[![Checked with mypy](https://img.shields.io/badge/mypy-strict-2a6db2.svg)](https://mypy-lang.org/)
[![Linted with Ruff](https://img.shields.io/badge/lint-ruff-261230.svg)](https://github.com/astral-sh/ruff)
[![Discord](https://img.shields.io/badge/Discord-参加-5865F2.svg?logo=discord&logoColor=white)](https://discord.gg/ACvBbrmguV)
![Status: alpha](https://img.shields.io/badge/status-alpha-orange.svg)

<sub><a href="README.md">English</a> · <a href="README.pt-BR.md">Português</a> · <a href="README.es.md">Español</a> · <a href="README.de.md">Deutsch</a> · <a href="README.fr.md">Français</a> · <a href="README.zh-CN.md">中文</a> · <b>日本語</b></sub>

</div>

Chimera は単一のフロンティアモデルに頼るのではなく、**リクエストごとに複数の LLM を融合** します ——
OpenRouter Fusion に着想を得た **パネル → ジャッジ → シンセサイザー** のパイプライン ——
そして**時間とともに自己改善**し（記憶 → スキル → モデル）、今日のエージェントを縛る*継続的進化による劣化*に抗います。

> **ステータス：** 初期開発（0.1.x）。ビルド計画の全体（M0–M7）が実装済み —— 4 つの能力ティア
> + フュージョン・エンジン + 自己進化 + ガバナンス・カーネル —— に加えて、**インターフェース層**
> （chat、TUI、HTTP ゲートウェイ）、**オプトインのモデル進化**、そして**機能層**
> （Vision、Deliverable モード、Pets など）。
> 224 件のテスト（＋オプトインのライブ統合）· `mypy --strict` クリーン · `ruff` クリーン。

---

## なぜ Chimera か

既存のフレームワークはそれぞれ 1 つの軸で強いだけです：Hermes/OpenClaw はスキルを進化させますが単一モデル；
CrewAI/LangGraph はオーケストレーションは得意でも学習しない；TrustClaw/NemoClaw/ZeroClaw はセキュリティ/
サンドボックスを提供しますが進化しません。**Chimera はこの 4 つを統合します：**

- 🧬 **推論としてのフュージョン** —— パネル→ジャッジ→シンセサイザーのエンジンが推論コアであり、後付けではありません。向上は*合成*ステップそのものから生まれ、モデルの多様性だけではありません。
- 🪜 **一続きの進行における 4 つの能力ティア** —— 拡張ツール → 単一タスク自律 → マルチエージェント・チーム → 自己進化エコシステム。
- ♻️ **多層の自己進化**が、継続的進化による劣化に明確に対処します（外部化された状態、ドリフト耐性のあるコンテキスト、verify-or-revert、経験バッファ）。
- 🛡️ **自らも改善するガバナンス・カーネル** —— allow/warn/block/review、静的に検証される自己改変サーフェス付き。

## 機能

**推論と自律**
- **LLM-Fusion エンジン** —— プロバイダ非依存のフロンティア + オープンモデルのパネル、合意/矛盾/盲点を浮かび上がらせるジャッジ、そしてシンセサイザー；**コストを意識したルーター**は割に合うときだけ融合します（ツール呼び出しのターンは単一モデルのまま）。
- **ティア 2 自律** —— 計画 → 実行 → Manager レビュー → **verify-or-revert**（ワークスペースのスナップショット/復元 + コマンド検証器）、git 風の経験バッファ付き。
- **マルチエージェント・チーム** —— 役割の専門化、逐次およびスーパーバイザー crew、MOC メッセージ統合、共有メモリ、並列レビュー。

**自己進化とガバナンス**
- **自己進化** —— Memory Manager（ADD/UPDATE/DELETE/NOOP の重複排除）、*自分でスキルを書いてテストする*スキル進化器（提案 → テスト → 保持/破棄）、自己学習 cron、そして劣化を測る**継続的進化ベンチマーク**（さらに EvoClaw の naive 対 guarded ストレステスト）。
- **オプトインのモデル進化** —— `solve` が軌跡を収集し、`evolve` がそれらを SFT/DPO データセットに整え、実行可能な LoRA レシピを出力します。トレーニングは**外部かつオプトイン** —— 決して自動ではありません。
- **ガバナンスと安全性** —— 自己改善する信頼カーネル（allow/warn/block/review）、自己改変の編集サーフェス向け静的検証器、追記専用の監査ログ、そして統制されたツール。

**プロバイダ**
- **どのモデルでも 1 つのインターフェース** —— LiteLLM 経由でプロバイダ非依存（`provider/model` スラッグで 100+ モデル）；OpenRouter/OpenAI/Anthropic/Gemini/DeepSeek のファーストクラス・キー。
- **セルフホスト＆堅牢** —— **Ollama/vLLM** 向けのカスタム・エンドポイント（`CHIMERA_API_BASE`）、モデル横断の**フォールバック・チェーン**、ラウンドロビンのキー・ローテーションを伴う**クレデンシャル・プール**、そして `chat`/`tui` でのライブ **`/model`** 切替。

**インターフェースと統合**
- **CLI ファースト、さらにインターフェース** —— `chat` REPL、フルスクリーンの **TUI**（Textual）、そしてチャットごとに 1 つの会話（と記憶）を持つ **メッセージング・ゲートウェイ** HTTP サーバー。
- **統合** —— ファーストクラスの **MCP** クライアント（stdio）＋ **OpenAPI/REST → ツール** インポーター。任意のプラットフォームや API を追加できます。
- **Cron とプロアクティブ性** —— 人が割り当てる、または自己学習するスケジュール・ジョブ。
- **マイグレーション** —— Hermes Agent / OpenClaw から設定・スキル・**長期記憶**をインポート（記憶は*マージ*され、上書きされません）。

**組み込みエクストラ**
- **Vision**（画像の貼り付け）、**Deliverable モード**（洗練された自己完結の成果物を生成）、そして **Pet** コンパニオン —— さらに Web 検索・画像生成・TTS/音声などのプリセット資格情報スロット（`chimera features` が何が準備済みで各々に何が必要かを表示）。

## クイックスタート

Python **3.11+**（3.12+ 推奨）と [uv](https://docs.astral.sh/uv/) が必要です。

```bash
uv sync --extra dev
cp .env.example .env        # 少なくとも 1 つのプロバイダ・キーを設定（OpenRouter 推奨）
uv run chimera doctor       # 環境を確認
```

## コマンド

```bash
chimera doctor / models / features    # ステータス、構成、オプション機能
chimera chat                          # 対話型マルチターン・アシスタント（あなたの右腕）
chimera tui                           # フルスクリーン端末アプリ（Textual）
chimera serve                         # メッセージング・ゲートウェイ HTTP サーバー（チャットごとのセッション）
chimera run "PROMPT" --image pic.png   # ティア 1 単発（--image でビジョン対応）
chimera deliver "ローンチ計画" -o plan.md   # Deliverable モード：洗練された成果物を生成
chimera fuse "PROMPT" --show-panel     # LLM-Fusion：パネル -> ジャッジ -> シンセサイザー
chimera agent "タスク" --fuse --guard    # ReAct ツールループ（統制されたツール呼び出し）
chimera solve "タスク" --verify "pytest -q"   # ティア 2 自律：計画 -> verify-or-revert
chimera crew "タスク" --mode supervisor  # ティア 3 マルチエージェント crew
chimera meta "X のためのエージェント"          # ティア 4 メタエージェント：専用エージェントを設計
chimera memory add "永続的な事実"    # キュレーションされた長期記憶（重複排除）
chimera cron add 名前 "0 9 * * *" "レポート実行"   # ジョブをスケジュール
chimera cron learn                     # 繰り返しタスクから cron を提案（無効）
chimera bench                          # 継続的進化ベンチマーク
chimera guard "rm -rf /"               # ガバナンス判定のプレビュー
chimera migrate hermes ~/.hermes --apply   # 設定 + スキルをインポート + 記憶をマージ
chimera evolve status / recipe             # オプトインのモデル進化：SFT/DPO データ + LoRA レシピ
chimera pet new --name Chimi               # 仮想コンパニオンを迎える（ステータスは時間とともに減衰）
```

インストール、構成、各コマンドのコピペ例については **[使い方ガイド](docs/usage.md)** を参照してください。

## アーキテクチャ

```
chimera/
  core/          エージェント・ループ（ReAct）+ ティア 2 自律（計画、verify-or-revert、スーパーバイザー）
  fusion/        パネル -> ジャッジ -> シンセサイザー + コスト意識ルーター
  memory/        working / episodic / semantic / persona + Memory Manager
  skills/        組み込みライブラリ + skill-context 取得
  evolution/     学習スキル進化器、経験バッファ
  governance/    信頼カーネル（allow/warn/block/review）、静的検証器、監査、統制ツール
  orchestration/ 役割、逐次・スーパーバイザー crew、MOC 通信
  ecosystem/     メタエージェント、変更テンポのガバナンス、軌跡収集、モデル進化
  tools/         ネイティブ・ツール（ファイル、shell、http）
  integrations/  MCP クライアント（stdio）+ OpenAPI->ツール インポーター
  scheduler/     cron（割当 + 自己学習）+ SOP エンジン
  migration/     Hermes/OpenClaw からインポート（設定、スキル、記憶マージ）
  providers/     LLM ゲートウェイ（LiteLLM）—— フォールバック・チェーン、クレデンシャル・プール、カスタム・エンドポイント
  interface/     会話型 ChatSession（chat、TUI、ゲートウェイで共有）
  tui/           フルスクリーン Textual アプリ
  server/        メッセージング・ゲートウェイ + HTTP トランスポート（チャットごとのセッション）
  eval/          継続的進化 + EvoClaw ストレステスト + 日常シナリオ
  cli/           `chimera` コマンド（CLI ファースト）
```

完全な設計と、その基盤となる研究については [docs/architecture.md](docs/architecture.md) を参照してください。

## ロードマップ

| マイルストーン | ステータス |
|---|---|
| M0 — 基盤（ゲートウェイ、設定、CLI） | ✅ |
| M1 — ティア 1 + ツール/スキル/統合/cron/マイグレーション | ✅ |
| M2 — LLM-Fusion エンジン + コスト意識ルーター | ✅ |
| M3 — ティア 2 自律（verify-or-revert） | ✅ |
| M4 — 自己進化（記憶、スキル、学習 cron、ベンチマーク） | ✅ |
| M5 — ガバナンス・カーネル | ✅ |
| M6 — ティア 3 マルチエージェント・チーム | ✅ |
| M7 — ティア 4 自己進化エコシステム | ✅ |
| M8 — インターフェース（chat/TUI/ゲートウェイ）、EvoClaw ストレステスト、オプトインのモデル進化 | ✅ |
| プロバイダ層 —— セルフホスト・エンドポイント、フォールバック・チェーン、クレデンシャル・プール、`/model` | ✅ |
| 機能 —— Vision、Deliverable モード、Pets + プリセット機能スロット | ✅ |

M7 以降、エージェントは実際のプロバイダ・モデルに対して強化されました（ライブ検証済み：Fusion、
ティア 2 の `solve`、日常シナリオ・スイート、HTTP ゲートウェイ、OpenAPI インポーター、stdio MCP クライアント）。
次は：大規模での継続的進化のより深い検証、より多くのプロバイダ統合（OAuth ログイン、クレデンシャル・プールの
チューニング）、そしてオプションの LangGraph 永続化バックエンド。

## 開発

```bash
uv run ruff check .      # lint
uv run mypy chimera      # 型チェック（strict）
uv run pytest -q         # テスト
```

[CONTRIBUTING.md](CONTRIBUTING.md) と [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) を参照してください。
セキュリティ問題：[SECURITY.md](SECURITY.md) を参照してください。

## コミュニティ

**[Discord](https://discord.gg/ACvBbrmguV)** で会話に参加してください —— 質問、アイデア、貢献を歓迎します。

## ライセンス

[Apache-2.0](LICENSE)。
