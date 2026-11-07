---
name: github-issue-creation
description: Root-only procedure for creating a GitHub Issue or changing an Issue's title or labels (including creating a label) - check the repository's label and title conventions first, pass the fixed content to the publication check, and read the result back. It grants no Issue creation, label creation or publish authority by itself.
---

# Issue の作成・タイトルとラベルの変更 — Root のみ

Root が Issue を作るとき、Issue のタイトルやラベルを変えるとき、ラベルを作るときに使う。学びの Issue 化（`wiki-knowledge` の「進め方の学び」）に限らず、どの場面でも同じ手順にする。SPEC: Issue #52。

この Skill を読んでも、Issue の作成・ラベルの作成・外部への書き込みの権限は増えない。各書き込みは既存の承認と AGENTS.md の公開前チェックに従う。Issue 本文だけの書き直しはこの Skill の対象外で、既存の運用（本文は仕様であり全体を書き直す）に従う。

**送る前にユーザーの承認を得る。** 作る Issue のタイトル・ラベル・本文、または変えるタイトル・ラベルと対象の Issue を示し、承認を得てから書き込む。ユーザーが内容まで指定して作成や変更を明示した場合は、その指定を承認とする。承認のあとに送る内容が変わったら、改めて承認を得る。

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
