---
name: ai-writing-lint
description: Root-only advisory check of Japanese text with textlint (preset-ai-writing and preset-ja-technical-writing) before it is committed or published - PR, Issue and comment bodies, review replies, Wiki pages, repository Markdown, code comments and commit messages. Not for child agents. It is not part of publication-check and grants no external access, publish or commit authority.
---

# AI 文章の textlint 検査 — Root のみ

日本語の文章に残りやすい、AI が書いたと分かる型を textlint で見つけて直す。SPEC: Issue #93。

## 使う場面

- この Skill を読んで使うのは Root だけとする。子のエージェント（Reader、Reviewer、Writer）には読ませず、実行もさせない。
- 下の表の日本語を出力する作業で、表の時点の前に検査する。

| 種類 | 検査する時点 |
| --- | --- |
| PR 本文、Issue 本文、コメント、レビューへの返信 | 公開前チェックの `prepare` の前 |
| Wiki のページ | Wiki の clone で commit する前 |
| リポジトリの `.md` ファイル（Skill、README、docs など） | commit する前 |
| コードのコメント | commit する前 |
| 日本語の commit message | commit する前 |

- 対象にしないものは、ユーザーへの会話の返答、子への task ファイル、英語の文章（日本語の混じらない英語の行）、Issue や PR のタイトルとする。
- この検査は文章の質を上げるための助言で、公開前チェック（`publication-check.py` と Gitleaks）の一部ではない。検査を省いても、検査で指摘が出ても、commit と `prepare`／`publish` の条件は変わらない。
- この Skill を読んでも、外部へのアクセス、commit、公開の権限は増えない。

## 子のエージェントとの関係

子の扱いは既存の規則のとおりとする。Reader と Reviewer はシェルとネットワークを使えない（`orchestrate/SKILL.md` の Reader の権限の項）。キャッシュを作るコマンドも実行できない（同じ Skill の、ファイルやキャッシュを作るコマンドの禁止の項）。

Writer が書いた日本語は、Root が Writer の差分を取り込んだあと（`orchestrate/SKILL.md` の Post-PR fixes の Integration）に検査する。Root が自分で commit する内容として、commit の前に検査する。これは子の成果の受け入れの手順ではなく、受け入れの判断には使わない。

## 設定

設定は `.agents/config/textlintrc.json` に置く。内容は ai-writing 1.7.0 の README の「推奨する組み合わせ」と同じにする。

- `preset-ja-technical-writing` を有効にする。
- `@textlint-ja/preset-ai-writing` を有効にし、`ai-tech-writing-guideline` を `severity: "info"` にする。

このファイルは保護対象ではないので、有効にするルールの組をここにも記録する。ルールの調整はしない。

## コマンド

```bash
cd <作業ディレクトリ>
npm_config_ignore_scripts=true npx -y -p textlint@15.8.0 -p @textlint-ja/textlint-rule-preset-ai-writing@1.7.0 -p textlint-rule-preset-ja-technical-writing@12.0.2 textlint --config <リポジトリ>/.agents/config/textlintrc.json -f json -o <作業ディレクトリ>/textlint-result.json <作業ディレクトリ>/<写し>.md
```

1. 作業ディレクトリは、リポジトリと Wiki の clone の外に `mktemp -d` で作る。Claude Code では、scratchpad の下の場所を名前の型で指定して作る（`mktemp -d "<scratchpad>/textlint.XXXXXX"`）。macOS の `mktemp` は、`TMPDIR` があると `-p` を使わないため。scratchpad そのものは使わない。
2. 上のコマンドの `<…>` を、空白を含むパスに置き換えるときは、パスごとに引用符で囲む。
3. 検査が終わったら、写しと `textlint-result.json` を作業ディレクトリごと消す。消すのは自分が `mktemp -d` で作ったディレクトリだけとする。npx のキャッシュは次の検査で使うので残す。
4. 直接指定する3つのバージョンは固定する。上げるときは、この Skill を変える PR で行う。
5. 推移的な依存は固定されない。これは受け入れたリスクで、安全とはいえない。
6. `npm_config_ignore_scripts=true` で、取得したパッケージのインストール時のスクリプトを止める。
7. 実行には npm registry からの取得が要り、取得したコードをローカルで動かす。これは外部へのアクセスなので、`AGENTS.md` の承認の規定（外部アクセスには承認が要る）のとおり、承認を得てから実行する。承認の範囲はユーザーが決め、この Skill は定めない。
8. 承認を求めるときは、コマンド、3つのバージョン、コードを取得してローカルで動かすことを1行で示す。キャッシュがあって取得が起きない実行にも承認が要るかは、ユーザーが承認の範囲として決める。

## 何を検査するか

検査するのは写しで、拡張子 `.md` で作業ディレクトリに置く。リポジトリや Wiki の clone の中では実行しない。

| 種類 | 写しの作り方 | 扱う指摘 |
| --- | --- | --- |
| Issue 本文、PR 本文、コメント（新しく書くもの） | `--body-file` に渡すファイルを写し、`cmp` で同じことを確かめる | すべて |
| 既存の Issue 本文、PR 本文の書き直し | 新しい本文を写す | 古い本文との `diff -U0` の `+` 側の行の指摘だけ。チェックボックスの切り替えだけの変更は検査しない |
| レビューへの返信 | 返信の文を先にファイルへ書いて写す。送り方は `address-pr-feedback/SKILL.md` の返信の手順のまま変えない。送る直前に、コマンドの中の文字列がそのファイルの内容と同じことを Root が見比べる（機械では確かめない） | すべて |
| リポジトリの `.md` | 変えたファイルを丸ごと写す（行番号を変えない） | `git diff -U0 HEAD -- <ファイル>` の `+` 側の行の指摘だけ。Git がまだ追跡していないファイルはすべて |
| Wiki のページ | 同じく丸ごと写す | Wiki の clone での `git diff -U0 HEAD -- <ファイル>` の `+` 側の行の指摘だけ。新しいページはすべて |
| コードのコメント | 下の「コメントの取り出し方」のとおり | すべて |
| commit message | 最後の message をファイルに書き、そのファイルを写して検査する。直してから、同じファイルで `git commit -F <ファイル>` する。`--amend` でも同じ | すべて（英語だけの trailer は除く） |

指摘が変えた行に入るかは、指摘の開始行（JSON の `line`）が `+` 側の行にあるかで決める。

コメントの取り出し方は、できる範囲で行う。

1. `git diff -U0 HEAD` の `+` 側の行から、日本語を含むコメントを探す。Git がまだ追跡していないファイルは差分に出ないので、ファイル全体から探す（`git ls-files --others --exclude-standard` で一覧を出す）。
2. `#` や `//` などのコメント記号は外す。続いた行のコメントは1つの段落にまとめる。
3. 段落の前に `<!-- path:line -->` を付けて、写しに書き出す。
4. 文字列のリテラルは対象にしない。
5. 取り出せなかったものがあれば、報告に書く。

英語だけの行に出た指摘は扱わない。例として引用した型（下の境界の事例の表など）は、引用として対象外にしてよい。

## 順序

1. 検査は表の時点の前に行う。指摘を直したら検査をやり直す。
2. commit のあとで直して検査をやり直すときは、差分の基準を `HEAD` にしない。基準は直す前の commit の親（`--amend` なら `HEAD~1`）か `<remote-base>` にし、元の変更行を落とさない。
3. `prepare` のあとで本文を変えたら、検査と `prepare` の両方をやり直す。検査記録は1回しか使えない（`github-issue-creation/SKILL.md` の検査記録の項）。
4. Wiki とリポジトリでは、commit のあとに `prepare` する（`wiki-knowledge/SKILL.md` の「公開の手順」）。commit を直したら、直した内容で検査をやり直す。

## 指摘の扱い

textlint の指摘は Reviewer の指摘ではない。`review-findings` は読まず、その記録の規則も当てはめない。

1. `textlint-result.json` の項目は、severity ではなくルール名で分ける。ルール名は `ruleId` の最後の `/` の後ろで比べる（例: `@textlint-ja/ai-writing/ai-tech-writing-guideline`）。textlint 15.8.0 では `ai-tech-writing-guideline` の `severity: "info"` が効かず、severity 2（error）で出るため。
2. `ai-tech-writing-guideline` 以外の項目は、severity が warning でも error でも、1件ずつ直すか対象外にするかを決める。
3. 対象外にしてよいのは、コードの識別子、引用、ファイルの行番号の参照、ルールの誤検知で、理由を1行で示す。
4. `sentence-length` は、文を分けて直すことを基本にする。表の行やコマンドのように分けられないものは、理由を1行で示して対象外にしてよい。
5. それ以外の指摘はすべて直す。
6. `ai-tech-writing-guideline` の項目は提案として扱い、理由を書かずに見送ってもよい。

## 報告

判断の一覧は Root の報告に書く。公開する文や commit には textlint の出力を貼らない。根拠は、ログの引用を最小限にする規定（AGENTS.md「外部公開前の秘匿情報確認」）で、scanner の生の出力を表示しない規定からの類推も添える。

報告では、終わり方を次のように区別する。

| 終わり方 | 判別 |
| --- | --- |
| 指摘が出た | exit 0 で、`textlint-result.json` に、扱う指摘のうち `ai-tech-writing-guideline` 以外の項目がある |
| 指摘が出なかった | exit 0 で、上の項目がない |
| 検査が動かなかった | 承認がない、または npx がパッケージの取得に失敗した（`npm error` を出して止まり、textlint が起動していない） |
| textlint が異常終了した | 上の行に当たらず、exit が 0 以外、または `textlint-result.json` がない・JSON として読めない |

- `-f json -o` を付けると、指摘があっても exit 0 になる。付けないと、指摘ありも設定の誤りも exit 1 になり、区別できない。
- 検査が動かなかったときと textlint が異常終了したときは、検査を省いて作業を止めない。どちらの場合も、省いたことと原因を報告に書く。異常終了の原因（設定の誤りなど）を直せたら、検査をやり直す。
- 指摘が0件でも、文章の質を確かめたとは報告しない。

## 境界の事例

| 事例 | 期待する扱い |
| --- | --- |
| `- **重要**: …` や `- ✅ …` の箇条書き、「革命的な技術」を含む新しい PR 本文 | 「指摘が出た」。型を平叙文に直して検査をやり直す |
| 平叙文だけの新しいコメント | 「指摘が出なかった」。0件でも質を確かめたとは報告しない |
| `ai-tech-writing-guideline` の項目だけが出る文書 | 「指摘が出なかった」に当たる。提案は理由なしで見送ってよい |
| コードの識別子（`no_mix_dearu` など）への指摘 | 対象外。理由を1行で示す |
| この表のように、例として引用した型への指摘 | 引用として対象外。理由を1行で示す |
| `SKILL.md:93` のような行番号の参照への指摘 | 対象外。理由を1行で示す |
| ルールの誤検知（文意から見て当たらない指摘） | 対象外。どう誤検知かを1行で示す |
| 表の行やコマンドの `sentence-length` | 分けられないので対象外にしてよい。理由を1行で示す |
| 本文の普通の文の `sentence-length`、「である。」の混在、ら抜き | 直すべき指摘。文を分ける、文体をそろえるなどで直す |
| 既存の `.md` で、変えていない行にだけ指摘が出る | 扱わない（`+` 側の行ではない） |
| 変えた行から始まり、変えていない行へ続く指摘 | 開始行が `+` 側にあるので扱う |
| 変えていない行から始まり、変えた行へ続く指摘 | 開始行が `+` 側にないので扱わない |
| 既存の Issue 本文で、チェックボックスだけを切り替える | 検査しない |
| Writer が書いた日本語の `.md` | Root が差分を取り込んだあと、自分の commit の前に検査する。子の成果の受け入れの判断には使わない |
| 英語だけの行に出た指摘（`AGENTS.md` の英語の行の `sentence-length` など） | 扱わない |
| 承認がない、または npx が `npm error` で取得に失敗した | 「検査が動かなかった」。省いたことと原因を報告に書き、作業は止めない |
| 設定ファイルが壊れていて exit 1、または `textlint-result.json` がない | 「textlint が異常終了した」。省いたことと原因を報告に書く。原因を直せたらやり直す |
