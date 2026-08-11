---
source_sha256: c43eb27971827466c65af13024113757f691c30d3666c4aa73c60105c08c56ab
---

# ベンチマーク — 弱いモデルの底上げを証明する

Chimeraのテーゼは、構造が**弱くて安い**モデルを底上げするというものです。それを示す正直な方法は、標準ベンチマーク上での統制されたA/Bです: タスクのサブセットとモデルを固定し、**唯一の**変数をスキャフォールディング(足場)にし、デルタを信頼区間とともに報告する — 「良くなった」という裸の主張ではなく。(独立した研究によれば、同じモデルでもスキャフォールディングだけで約7ポイント揺れることが分かっており、修飾のないスコアは*あなたの*貢献について何も語りません。)

## 実験

**ベンチマーク:** [Terminal-Bench 2.0](https://www.tbench.ai/) — Dockerタスク+指示+検証テストで構成され、それらのテストによってpass/failで採点され、エージェント非依存の **Harbor** ハーネスによって駆動されます。

- **アームA(ベースライン):** Harborの中立的なスキャフォールド内の1つの無料モデル — 「弱いモデル単体」。
- **アームB(処置群):** **同じ**モデル、**同じ**タスクID、Chimeraによって駆動。
- **メトリック:** pass@1。**見出し:** Δ = rate(B) − rate(A)、95%信頼区間付き。
- **正直さのガード:** タスクIDのサブセットを固定し(公開する)、3シード以上を実行し、すべてのトランスクリプトを公開し、フロンティアモデルの行は*天井の参考値*としてのみ追加する — 決して比較対象としてではない。

このテーゼを証明する唯一の数字: **無料モデル単体 = X%、無料モデル + Chimera = Y%、同じタスク、Y ≫ X。**

## 実行方法

```bash
uv sync --extra bench            # installs terminal-bench (Harbor); also needs Docker
playwright install chromium      # only if a task needs the browser tool
```

Chimeraは `chimera/eval/terminal_bench.py` を通じて処置群エージェントとして組み込まれます(`make_chimera_tb_agent(model)` は、スキャフォールディングフラグ付きで `chimera solve` を実行するHarborの `BaseAgent` を構築します)。Harborを固定サブセットと各アームの無料モデルに向けてください。正確な `harbor run` の呼び出しと `--agent-import-path` については、[Harborのドキュメント](https://www.tbench.ai/)を参照してください。

## SWE-bench Verified(2つ目のスコアボード) — **2回実行**

Terminal-BenchはCLIタスクでテーゼを証明し、SWE-benchは実際のGitHubのバグ修正でそれを証明します — ベースコミット時点のリポジトリとIssueが与えられ、エージェントはインスタンスの `FAIL_TO_PASS` テストを通過させつつ `PASS_TO_PASS` を緑のまま保つパッチを生成しなければなりません。「Verified」は人間が検証したサブセットです。

### 結果

同じ固定された19インスタンスの `django/django` スライス(最も易しい難易度層)上での2回の事前登録された実行、`deepseek-chat-v3.1`、pass@1、Docker内の公式 `swebench` 4.1.0ハーネス**のみ**によって採点。全文: [`bench/swe_bench/RESULTS.md`](../bench/swe_bench/RESULTS.md)。

| 実行 | ベースライン | + Chimera | 対応差Δ | 95%信頼区間 | |
|---|---|---|---|---|---|
| 1 (`max_steps=8`) | 36.8% (7/19) | 36.8% (7/19) | +0.0% | [−8.5%, +8.5%] | 有意でない |
| 2 (`max_steps=30`) | 42.1% (8/19) | **57.9% (11/19)** | **+15.8%** | [−1.9%, +15.8%] | 有意でない |

実行1は**厳密なゼロ**であり、そのまま変更なく公開されています。実行2は*こちら側の*不備を2つ修正しました — スキャフォールドが最も強力な仕組みなしで動作していたことと、8ステップのツール呼び出しでは250MBのリポジトリをナビゲートするのに不十分だったことです — その結果、**3インスタンス勝利、0敗**となりました。このペアこそが発見です: スキャフォールドはエージェントがステップに飢えているときは*無価値*であり、そうでないときは*3インスタンス*の価値があり、それは編集を**より多く**行うことでではなく、編集を**より良く**行うことで勝ちます(編集時の精度69% vs 57%)。

> ⚠️ **57.9%はSWE-bench Verifiedのスコアではありません。** このスライスは測定に余地を持たせるため意図的に易しく単一リポジトリに限定されています。実際のVerifiedスコアには完全な500件が必要です。そしてこのデルタは**有意ではありません** — 両方が失敗するペアが8つある中で、n=19では情報量のあるペアは3つしか残りません。

実行2はまた**撤回**も伴います: 実行1の空のパッチについて追跡していたメカニズムは誤りでした(修正はステップ予算であって、当初非難したdiff-gateではありませんでした)。これは、主張されたのと同じくらい目立つ形で訂正されています。

### アダプター

アダプター(`chimera.eval.swe_bench`)はその境界について正直です: 純粋な部分 — インスタンスごとの `chimera solve` の呼び出し(処置群アーム)と公式評価レポートのパース — はここに存在し、ユニットテストされています。データセットとDocker評価ハーネスは**オプトインでバンドルされておらず**、pass/failの判定はSWE-bench自身のテストから得られ、自己申告は決してありません。

```bash
# 1. Curate a JSONL slice (one instance object per line): instance_id, repo, base_commit,
#    problem_statement, and (optionally) test_cmd. build_solve_command turns each into a
#    `chimera solve <issue> --verify <test_cmd> --repo-map --progress-ledger --replan --checklist`.
# 2. Run both arms through the official SWE-bench harness (model-only vs model+Chimera) on the
#    SAME instance ids, producing two evaluation reports.
# 3. Score the honest A/B:
chimera swe-bench-compare model_only_report.json chimera_report.json --instances mini.jsonl
```

両方のレポートは共有インスタンスリストに射影されます(欠落したidは未解決としてカウントされます)。そのため、両アームは常に同一のインスタンス上で比較され、同じNewcombe信頼区間の判定が適用されます。

## A/Bの採点(ベンチマーク不要)

各アームがタスクごとのpass/failを生成した後、統計処理は1コマンドで済みます — これには**追加パッケージが不要**なので、正直な報告エンジンは常に利用可能です。

```bash
chimera bench-compare baseline.json chimera.json --treatment-name chimera
```

各ファイルは**同じ**タスクID群にわたるブール値のJSONリスト(または `{task_id: bool}`)です。出力: 各アームのWilson区間による合格率、デルタ、そのNewcombe 95%信頼区間、そしてその差が**有意**かどうか(信頼区間がゼロを含まない)。有意でない場合、それはそのまま報告されます — より大きなサブセット/より多くのシードが必要か、その機能が本当に数字を動かさないかのどちらかです。

この同じ `bench-compare` が、以降のすべての機能の物差しです: M14の各追加は、同一のサブセット上でΔを動かすことを示さなければならず、示せなければ削除されます。

## 正直さの罠(避けるべきこと)

- **汚染** — 公開されているSWE-benchには解答の漏洩が文書化されています。汚染耐性のあるセットを優先し、その注意点を報告してください。
- **スキャフォールドの交絡** — 生の「X%のスコアを出した」を決して報告しないでください。A/Bのデルタだけが Chimeraの貢献を切り分けます。
- **誤ったベースライン/チェリーピッキング** — 弱いモデル+Chimeraを、*同じ*弱いモデル単体と、*同一の*タスクID上で、シードと完全なログとともに比較してください。フロンティアモデルは天井であって、対抗馬ではありません。
