---
name: github-issue-creation
description: Root-only procedure for creating a GitHub Issue, changing an Issue's title or labels (including creating a label), or drafting an Issue body that changes design - verify design-bearing drafts before presenting them, check the repository's label and title conventions first, pass the fixed content to the publication check, and read the result back. It grants no Issue creation, label creation or publish authority by itself.
---

# Issue の作成・タイトルとラベルの変更 — Root のみ

Root が Issue を作るとき、Issue のタイトルやラベルを変えるとき、ラベルを作るとき、設計を含む Issue 本文の案を書くときに使う。学びの Issue 化（`wiki-knowledge` の「進め方の学び」）に限らず、どの場面でも同じ手順にする。SPEC: Issue #52。

この Skill を読んでも、Issue の作成・ラベルの作成・外部への書き込みの権限は増えない。各書き込みは既存の承認と AGENTS.md の公開前チェックに従う。Issue 本文だけの書き直しは、下の「案を示す前の確かめ方」だけを適用し、それ以外は既存の運用（本文は仕様であり全体を書き直す）に従う。

**送る前にユーザーの承認を得る。** 作る Issue のタイトル・ラベル・本文、または変えるタイトル・ラベルと対象の Issue を示し、承認を得てから書き込む。ユーザーが内容まで指定して作成や変更を明示した場合は、その指定を承認とする。承認のあとに送る内容が変わったら、改めて承認を得る。

## 案を示す前の確かめ方

Issue の案（新しい Issue と、既存 Issue の本文の書き直し）をユーザーに示す前に行う。ユーザーが内容まで指定した場合も、下の対象に当たれば同じく確かめてから書き込む。SPEC: Issue #64。

1. **対象:** 案が、ファイル・関数・データの形式・手順の振る舞い・仕様・権限を変える内容を含むなら、この節の対象にする。すでに合意した設計を転記する場合も、その具体的な内容を含むなら対象にする。判断が割れるときは対象とする。
2. **対象外の例:** リンクや誤字の修正、タイトル・ラベルだけの変更、振る舞いを変えない文言の修正。対象外にするときは、その理由を1行で示す。
3. **確かめ方:**
   - 実行できる判定や形式の変更は、関係するコードを読み、scratchpad の試作で既存のテストに当てて確かめる。リポジトリは変えない。試作やテストで自分が作ったキャッシュなどの生成物だけを消す。
   - 文章の規則の変更は、期待する結果を付けた境界の事例（対象になる例・ならない例）で確かめる。
   - 確かめられない項目は、その理由を本文に書く。
   - 項目ごとに、実現できる根拠（ファイルと行、既存のテストへの影響）を本文に書く。
4. **検証:** 対象の案は、Reviewer の敵対的な検証を通してから、検証済みの案として示す。示すときは、指摘とそれぞれの判定（`review-findings` の FIX/SKIP/STOP）を添える。起動の承認は既存の手順（`launch-gate`）による。判定は `review-findings` のとおりに記録する。承認・Reviewer の枠・Herdr・承認済みの provider が使えない間は、未検証であることと、何が欠けているかを示したうえで案を見せてよい。built-in subagent をこの検証の代わりにしない。未検証であることを示したあとでユーザーが承認した場合は（内容の指定だけでは、未検証を承知した承認としない）書き込んでよいが、本文に「未検証」と欠けている検証を書き、検証済みとは扱わない。
5. **既存の規則との関係:** この節で省けるのは、この節が加える案の検証だけである。AGENTS.md と `orchestrate` の必須の独立レビューの条件に当たる案には、この節の対象外でも引き続き適用される。
6. **規模の確認:** 検証の指摘を直すときは、ユーザーと合意した範囲・受入条件・権限との差分を確かめる。範囲を超える変更（新しい仕組みや接点を加える、合意したものより権限や条件を緩める）が必要な指摘は `review-findings` の STOP とし、理由を示して、範囲を広げてよいかをユーザーに確かめる。合意した範囲の中で、エラーの事例を足す・表現を正すといった修正は、確かめずに行ってよい。

事例と期待する結果:

| 事例 | 期待する結果 |
| --- | --- |
| summary.json に項目を足す案 | 対象（形式の変更）。コードを読み、scratchpad の試作を既存のテストに当て、Reviewer の検証を通して示す |
| 会話で合意した判定の条件を Issue に転記する案 | 対象（合意済みの設計の転記）。条件の具体的な内容を含むので、同じく確かめる |
| 既存 Issue 本文の誤字だけの修正 | 対象外。「振る舞いを変えない誤字の修正」と1行で示す |
| Issue に `priority:P2` を付ける | 対象外。「ラベルだけの変更」と1行で示す |
| Herdr が使えず Reviewer を起動できない | 「未検証。Reviewer の検証が欠けている（Herdr 不可）」と示して案を見せてよい。ユーザーが未検証と承知して承認したら、本文に「未検証」と書いて作成する |
| 指摘を受けて新しい hook を加える修正 | 範囲を超えるので STOP とし、直す前にユーザーに範囲を広げてよいかを確かめる |
| 指摘を受けてエラーの事例を1つ足す修正 | 範囲の中なので、確かめずに直す |

## 作る前に確かめること

1. 用意されているラベルを `gh label list --limit 200 --json name,description` で確かめる（既定では30件で切れる）。
2. 最近の Issue の付け方を `gh issue list --state all --limit 15 --json number,title,labels` で確かめる。優先度は `priority:P1`／`priority:P2`／`priority:P3` のラベルで表し、タイトルに `[P2]` などの優先度の表記を付けない（2026-11-07 時点の付け方。古い Issue には両方を付けた例がある）。確かめた結果が上の記述と違うときは、最近の Issue の付け方に合わせ、違いをユーザーに伝える。
3. 優先度は、関係する Issue との依存と影響から Root が決め、その理由を1行で添えてユーザーに示す。ユーザーが優先度を指定した場合はそれに従う。
4. 重複を避けるため、`gh issue list --search '<語句>' --state all` で近い Issue を探す。あれば新しく作らず、追記先の候補として示す。
5. 合うラベルがないときは、ラベルを作るかをユーザーに確かめる。ラベルを作ることは、Root の判断だけではしない。
6. 上の確認の結果（ラベル・優先度とその理由・近い Issue）を、承認を求めるときに一緒に示す。

## 公開前チェック

1. 送る内容をすべてファイルに固定する。本文は `--body-file` に渡すファイルにする。タイトル・ラベル名・ラベルの説明など本文以外の値も、1つのファイルに書き出して固定する。`prepare` は `--file` か `--git-range` を少なくとも1つ必要とするので、ラベルやタイトルだけの変更でもこのファイルを渡す。
2. Root はファイルとコマンドを文脈確認してから、同じ入力・同じコマンドで `prepare --context-reviewed` と `publish` を実行する。コマンドの中のタイトルやラベル名は、固定したファイルの値と一致させる。`--body-file` のパスが `--file` に含まれることも同じく確かめる。この一致はスクリプトが確かめるものではなく、Root が文脈確認で確かめる（スクリプトはファイルとコマンドをそれぞれ検査する）。`prepare` と `publish` では、`--target`、`--file` の並び、コマンドを完全に同じにする。タイトルに `'` を含むときは、引用符の付け方に気をつける。
   ```bash
   python3 .agents/scripts/publication-check.py prepare --policy-root "$PWD" \
     --target <owner>/<repo>:issue:new --file <本文ファイル> --file <値のファイル> \
     --context-reviewed -- gh issue create --title '<タイトル>' --label 'priority:P2' --body-file <本文ファイル>
   python3 .agents/scripts/publication-check.py publish --policy-root "$PWD" \
     --target <owner>/<repo>:issue:new --file <本文ファイル> --file <値のファイル> \
     --receipt <prepareが出力した記録> -- gh issue create --title '<タイトル>' --label 'priority:P2' --body-file <本文ファイル>
   ```
   ラベルやタイトルだけの変更（例: `gh issue edit <番号> --add-label ...`、`gh label create ...`）は、`--target` を `<owner>/<repo>:issue:<番号>` や `<owner>/<repo>:label:<名前>` にし、`--file` には値のファイルだけを渡す。
3. 1回の外部書き込みごとに検査記録を作る。記録は使い回さない。`publish` は送る前に記録を使用済みにするので、送信に失敗したときも新しい `prepare` が必要になる。やり直す前に、`gh issue list --search '<タイトル>' --state all` や `gh label list` で、失敗に見えても実際には作られていないかを確かめ、二重に作らない。

## 作ったあとの読み戻し

1. `gh issue view <番号> --json number,title,body,labels` で、タイトル・本文・ラベルが送った内容と一致することを確かめる。本文は固定したファイルと比べる（`gh` の出力が末尾に付ける改行の違いは除く）。ラベルを作った場合は `gh label list --limit 200 --json name,description` で名前と説明を確かめる。
2. 一致を確かめるまで、作成や変更が済んだと報告しない。
3. 2026-11-07 時点の観測では、新しい Issue は Project に自動で加わり、反映が作成の少しあとになることがあった。作成直後に見えないことだけを理由に、Project にないと報告しない。
