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
> + フュージョン・エンジン + 多層の自己進化 + ガバナンス・カーネル —— に加えて、**閉じた行動学習ループ**、
> **運用層**（カンバン + ワーカー・レーン、SDLC クルー、宣言的ループ DSL）、**実行分離**（Docker サンドボックス
> + git worktree）、そして設計の基盤となった**論文の技術**（HORIZON、VIBEMed、Spec Growth、AgentTrust v2、
> AutoMegaKernel、Meta-Agent、MOC）。
> 332 件のテスト（＋オプトインのライブ統合）· `mypy --strict` クリーン · `ruff` クリーン。

---

## なぜ Chimera か

既存のフレームワークはそれぞれ 1 つの軸で強いだけです：Hermes/OpenClaw はスキルを進化させますが単一モデル；
CrewAI/LangGraph はオーケストレーションは得意でも学習しない；TrustClaw/NemoClaw/ZeroClaw はセキュリティ/
サンドボックスを提供しますが進化しません。**Chimera はこの 4 つを統合します：**

- 🧬 **推論としてのフュージョン** —— パネル→ジャッジ→シンセサイザーのエンジンが推論コアであり、後付けではありません。向上は*合成*ステップそのものから生まれます。
- 🪜 **一続きの進行における 4 つの能力ティア** —— 拡張ツール → 単一タスク自律 → マルチエージェント・チーム → 自己進化エコシステム。
- ♻️ **閉じた多層の自己進化ループ**が、継続的進化による劣化に明確に対処します（外部化された状態、ドリフト耐性のあるコンテキスト、verify-or-revert、計画に再投入される経験バッファ）。
- 🛡️ **自らも改善するガバナンス・カーネル** —— allow/warn/block/review、静的に検証される自己改変サーフェスと保護された判例付き。

## 機能

**推論と自律**
- **LLM-Fusion エンジン** —— プロバイダ非依存のフロンティア + オープンモデルのパネル、合意/矛盾/盲点を浮かび上がらせるジャッジ、そしてシンセサイザー；**コストを意識したルーター**は割に合うときだけ融合します（ツール呼び出しのターンは単一モデルのまま）。
- **ティア 2 自律** —— 計画 → 実行 → Manager レビュー → **verify-or-revert**（ワークスペースのスナップショット/復元 + コマンド検証器）、**git worktree 分離**（`solve --isolate`）付き —— 変更は検証された場合のみ反映されます。
- **SDLC ライフサイクル・クルー**（`chimera lifecycle`）—— 事前構成の **plan → build → test → review** パイプライン、test 段階で verify-or-revert。
- **マルチエージェント・チーム** —— 役割の専門化、逐次およびスーパーバイザー crew、MOC 統合、共有メモリ、並列レビュー。

**自己進化とガバナンス**
- **閉じた行動ループ** —— 過去の失敗が planner に教訓として入り、検証済みの成功は自動で記憶に書かれ、繰り返しのタスクは検証＆スモークテスト済みのスキルへ自動進化します —— すべて verify-or-revert でゲートされます。さらに継続的進化ベンチマークと EvoClaw の naive 対 guarded ストレステスト。
- **階層メモリ** —— working / episodic / semantic / persona **+ graph 層**（`memory graph`）。キーワードだけでなくエンティティで事実を想起します。
- **オプトインのモデル進化** —— `solve` が軌跡を収集し、`evolve` が SFT/DPO データセットに整え、実行可能な LoRA レシピを出力します。トレーニングは外部/オプトインのまま。
- **ガバナンス・カーネル** —— allow/warn/block/review（語彙ルール + 任意の意味ジャッジ、ルール蒸留と**保護された判例ストア**付き）、自己改変サーフェスの静的検証器、追記専用の監査ログ、統制ツール、**4 アクターの変更モデル**、そして **spec↔コードのドリフトゲート**（`chimera drift`）。

**プロバイダ**
- **どのモデルでも 1 つのインターフェース** —— LiteLLM 経由でプロバイダ非依存（`provider/model` スラッグで 100+ モデル）；OpenRouter/OpenAI/Anthropic/Gemini/DeepSeek のファーストクラス・キー。
- **セルフホスト＆堅牢** —— **Ollama/vLLM** 向けのカスタム・エンドポイント（`CHIMERA_API_BASE`）、モデル横断の**フォールバック・チェーン**、ラウンドロビンの**クレデンシャル・プール**、ライブ **`/model`** 切替、そして繰り返しの推論ターン向けの **プロンプト・キャッシュ**（`CHIMERA_CACHE`）。

**オーケストレーション、インターフェースと統合**
- **カンバン + ワーカー・レーン**（`chimera kanban`）—— タスクボード（backlog → doing → review → done）。カードは `solve` または `crew` レーンへ振り分けられ、`kanban learn` は繰り返しタスクをカードにします。
- **Loop Engineering**（`chimera workflow`）—— 自律ループを YAML で記述（スタックを `use` するステップ、`when` 条件と `repeat`/`until` ループ付き）。
- **インターフェース** —— `chat` REPL、フルスクリーンの **TUI**（Textual）、そしてチャットごとに 1 つの会話（と記憶）を持つ **メッセージング・ゲートウェイ** HTTP サーバー。
- **実行サンドボックス** —— シェルツールをローカルまたは隔離された **Docker** コンテナで実行（`CHIMERA_SANDBOX=docker`）。
- **統合** —— ファーストクラスの **MCP** クライアント（stdio）＋ **OpenAPI/REST → ツール** インポーター；**cron**（人による割当と自己学習、確認付き）；Hermes Agent / OpenClaw から設定/スキル/長期記憶を**マイグレーション**。

**組み込みエクストラ**
- **Vision**（画像の貼り付け）、**Deliverable モード**（洗練された成果物）、そして **Pet** コンパニオン —— さらに Web 検索・画像生成・TTS/音声などのプリセット資格情報スロット（`chimera features`）。

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
chimera deliver "計画" -o plan.md       # Deliverable モード：洗練された成果物を生成
chimera fuse "PROMPT" --show-panel     # LLM-Fusion：パネル -> ジャッジ -> シンセサイザー
chimera solve "タスク" --verify "pytest -q" --isolate   # ティア 2：verify-or-revert、git worktree 分離
chimera lifecycle "タスク" --verify "..."   # SDLC クルー：plan -> build -> test -> review
chimera workflow flow.yaml             # 宣言的ループを実行（Loop Engineering）
chimera crew "タスク" --mode supervisor  # ティア 3 マルチエージェント crew
chimera meta "X のためのエージェント"          # ティア 4 メタエージェント：専用エージェントを設計
chimera kanban add/board/run/learn     # ワーカー・レーン付きタスクボード（solve/crew）
chimera drift spec.yaml                # spec<->コードのドリフトゲート（ドリフト時は exit 1）
chimera memory add / graph             # キュレーション済み長期記憶 + エンティティ関係グラフ
chimera cron add / learn               # スケジュールジョブ（割当 + 自己学習、確認付き）
chimera bench                          # 継続的進化ベンチマーク
chimera migrate hermes ~/.hermes --apply   # 設定 + スキルをインポート + 記憶をマージ
chimera evolve status / recipe         # オプトインのモデル進化：SFT/DPO データ + LoRA レシピ
chimera pet new --name Chimi           # 仮想コンパニオンを迎える
```

インストール、構成、各コマンドのコピペ例については **[使い方ガイド](docs/usage.md)** を参照してください。

## アーキテクチャ

```
chimera/
  core/          エージェント・ループ（ReAct）+ ティア 2 自律（計画、verify-or-revert）+ git worktree 分離
  fusion/        パネル -> ジャッジ -> シンセサイザー + コスト意識ルーター
  memory/        working / episodic / semantic / persona + graph 層 + Memory Manager
  skills/        組み込みライブラリ + skill-context 取得
  evolution/     スキル進化器、自動進化フック、経験バッファ
  governance/    信頼カーネル（ルール + ジャッジ + 保護された判例）、静的検証器、ドリフトゲート、4 アクターモデル、監査
  orchestration/ 役割、逐次/スーパーバイザー crew、MOC 通信、SDLC ライフサイクル・クルー
  ecosystem/     メタエージェント、変更テンポのガバナンス、軌跡収集、モデル進化
  kanban/        タスクボード + ワーカー・レーン（crews / solve への振り分け）
  workflow/      宣言的ループ DSL（Loop Engineering）
  tools/         ネイティブ・ツール（ファイル、shell、http）
  sandbox/       実行バックエンド（local / docker 分離）
  integrations/  MCP クライアント（stdio）+ OpenAPI->ツール インポーター
  scheduler/     cron（割当 + 自己学習）+ SOP エンジン
  migration/     Hermes/OpenClaw からインポート（設定、スキル、記憶マージ）
  providers/     LLM ゲートウェイ（LiteLLM）—— フォールバック、クレデンシャル・プール、カスタム・エンドポイント、プロンプト・キャッシュ
  interface/     会話型 ChatSession（chat、TUI、ゲートウェイで共有）
  tui/  server/   フルスクリーン Textual アプリ · メッセージング・ゲートウェイ + HTTP トランスポート
  eval/          継続的進化 + EvoClaw ストレステスト + 日常シナリオ
  cli/           `chimera` コマンド（CLI ファースト）
```

完全な設計と、その基盤となる研究については [docs/architecture.md](docs/architecture.md) を参照してください。

## ロードマップ

| マイルストーン | ステータス |
|---|---|
| M0–M7 — 4 ティア + フュージョン + 自己進化 + ガバナンス | ✅ |
| M8 — インターフェース（chat/TUI/ゲートウェイ）、EvoClaw ストレステスト、オプトインのモデル進化 | ✅ |
| プロバイダ層 —— セルフホスト・エンドポイント、フォールバック、クレデンシャル・プール、`/model`、プロンプト・キャッシュ | ✅ |
| 閉じた行動ループ —— 経験→planner、自動記憶、自動スキル（統制下） | ✅ |
| 運用オーケストレーション —— カンバン + ワーカー・レーン、SDLC クルー、Loop DSL | ✅ |
| 実行分離 —— Docker サンドボックス + git worktree | ✅ |
| 論文の技術 —— HORIZON · VIBEMed · Spec Growth · AgentTrust v2 · AutoMegaKernel · Meta-Agent · MOC | ✅ |
| 論文の技術（II）—— MemGate · 多因子メモリ価値 · Data Recipes · OpenClaw-Skill · SkillAdaptor · DailyReport · OpenJarvis スペック探索 | ✅ |

次は：大規模での継続的進化のより深い検証、プロバイダの OAuth ログイン、そしてオプションの LangGraph
永続化バックエンド。モデル学習（LoRA/DPO）は設計上、外部/オプトインのままです。

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
