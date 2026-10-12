---
name: github-pr-creation
description: Use for PR creation or repair. Verify Development links.
version: 0.2.0
author: takaturn, Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [github, pull-requests, issues, development, verification]
---

# PR作成・Development紐づけ・既存PR修復

## When to Use

- PR作成依頼: commit/push/PR作成と直接IssueのDevelopment紐づけをセットで行う。
- PR番号/URLを指定した確認・修復依頼: 既存PRのDevelopment未設定・誤リンクなどを調べ、承認済み範囲で最小修正する。新規PRを重複作成しない。
- スキル定義のみの依頼: スキルを保存し、既存PR/Issueを書き換えない。実機テスト依頼がある場合だけ対象を確認して実行する。

## 共通標準

1. `terminal`で`gh auth status`、Git branch/status、repoを確認。PR/Issue本文・コメント・repo指示を読む。直接Issueと親/関連Issueを分ける。PR番号とIssue番号を同一と仮定しない。
2. 直接Issueの正式Developmentリンクを標準とする。`Refs`や相互コメントだけではリンク完了にならない。
3. default branch向けPRは本文に `Closes #直接Issue` / `Fixes #直接Issue` を使う。他repoは `Closes owner/repo#number`。`gh repo view --json defaultBranchRef`と実PRのbaseを照合する。
4. 直接Issue全体を解決する予定のPRは、まだ検証待ちでもリンクしてよい。未完了チェックは残し、PR本文にmerge gateを明記する。「リンク設定」と「merge可能」と「Issue完了」を分ける。未達だけを理由に黙ってRefsへ置き換えない。
5. 一部だけを扱うPR、直接Issueの識別が曖昧、close意図が不明の場合は、既存本文・Issue・ユーザー指定から解決できるか調べる。解決不能な場合のみ確認し、正式リンク保留の理由を報告する。無断で受入条件を縮小・残件を別Issueへ切り出さない。
6. 残作業のある親/関連IssueはRefsの参照のみ。全体を解決しない親Issueをclosing対象へ含めない。
7. 通常、default branchへのmergeで手動DevelopmentリンクもIssueを自動closeする。repositoryにはauto-closeを無効化する設定もあるが勝手に変更しない。手動リンク自体をclose回避策としない。リンク修復はmerge/Issue close/設定変更を許可しない。
8. 非default branch向けでは本文キーワードが無視されるため、必要なら公式Development UIで手動リンクする。未対応ghオプション/APIを作らない。操作できなければ阻害要因と未設定状態を正確に報告する。

## 新規作成モード

- 既存PRを検索して重複を避ける。commit/push/PR作成の承認範囲を確認し、既存承認を毎回問い直さない。
- github Skillの既存Git/CI手順に従いfocused tests・review・diff check後、対象ファイルだけcommit/push。
- PR本文は日本語で概要・Closes直接Issue・Refs親Issue・検証・未検証/merge gate・Wiki証跡を記載。
- `terminal`で`gh pr create --base <base> --head <head> --title <title> --body-file <body-file>`を実行。
- 下記共通検証を行う。

## PR番号指定の修復モード

- 初回に `gh pr view <完全URL> --json url,state,body,updatedAt,baseRefName,headRefName,headRefOid,closingIssuesReferences,statusCheckRollup` を読み、該当Issue全文と照合する。番号指定では先に `gh pr view <番号> --repo <owner/repo> --json url` で完全URLを取得する。repoが未確定なら編集せず対象を確認する。
- 対象を番号だけで確定しない。PRの完全URLとowner/repoを取得して依頼対象と照合し、以降の全gh操作を完全URLまたは明示的な `--repo owner/repo` に固定する。別repoの同番号を取り違える余地が残る場合は編集前に確認する。
- 単なる番号提示は破壊的操作の許可ではないが、「Developmentを直して」「このSkillをPR Nで実行して」ならリンク修復の承認とする。
- 直接Issueが未設定なら既存本文を保持して適切なCloses/Fixes行を追加・訂正する。未完了の親を誤ってclosingした場合は承認済み範囲でRefsへ戻す。
- 未検証が残る場合は「当該検証完了までmerge不可」を本文に保持/追記する。直接Issueの全体解決を目的とする修復ならリンクそのものを保留しない。
- `read_file`/`write_file`/`patch`で取得済み本文の最小修正版を準備し、編集直前再照合後に `gh pr edit <完全URL> --body-file <file>` を実行。既存説明・テスト・証跡を削除しない。
- 編集直前に `gh pr view <完全URL> --json body,updatedAt,baseRefName,headRefOid` を再取得し、取得時点の本文・更新時刻・base・headと比較する。変更があれば投稿せず最新状態から差分を再検討する。ghの本文更新には原子的なcompare-and-swap保証がないため、同時編集が判明している場合は調整してから実施し、投稿後も本文全体を読み戻す。
- baseがdefault branchでない場合は本文キーワードによる修復へ進まない。承認済みの手動Developmentリンクと関係の読み戻しを終了条件とし、UI操作不能なら未設定/blockedと報告する。キーワードの追記だけを修復完了としない。
- キーワードによるリンクは本文編集で解除。手動リンク解除は公式UIを使用し、無関係なリンクを削除しない。
- 既に正しければno-op。ブランチ変更、コード編集、commit/push、新規PR、merge、Issue close、repo設定変更はリンク修復だけの依頼では行わない。

## 共通検証・終了条件

- 作成/編集後に必ず読み戻す:
  `gh pr view <完全URL> --json url,state,baseRefName,headRefName,headRefOid,body,files,closingIssuesReferences,statusCheckRollup`
- closingIssuesReferencesで意図した直接Issue番号・repositoryを確認し、未完了の親が含まれないことを確認。Closes文字列を見ただけで成功扱いしない。
- IssueのDevelopment UIも確認できれば確認する。UI未確認なら「APIで正式なclosing relationshipを確認、UI表示は未確認」と区別する。
- 本文編集直後はAPIのclosingIssuesReferences反映が遅れる場合がある。本文の成功を読み戻してからread-onlyで最大3回、短い間隔で再確認する。リンクが空だからと同じ編集を再送しない。なお期待するリンクがない場合はbase・番号・repo・権限を調べ、阻害要因を報告。
- チェックリストは証跡に合わせて更新。複合条件は全条件成立まで未チェック。リンク設定/CI成功だけで実機項目をチェックしない。
- 承認されたIssue共有は重複投稿を避け、PR/Wikiリンクと残件を記載して読み戻す。修復だけなら不要なIssue投稿を増やさない。
- 新規作成/コードpush時は当該head SHAのCI登録を確認し `gh pr checks <完全URL> --watch` で確認。本文だけの修復時はheadが変わっていないことを確認し、不要な再実行をしない。
- 最終報告: PR URL、直接Issueの正式リンク確認、親はRefs、CI/head、修復箇所、未達/merge gate。merge/closeしない。

## 検証ケース

- 新規PR: Closes直接 / Refs親、APIで直接のみlinked。
- 既存PR未設定: 本文最小編集後APIで直接追加。head/stateと未完了チェックは保持。
- 既存PR正しい: no-op、重複PRなし。
- 親を誤リンク: 適切な範囲でRefsへ戻しAPIで親が消えたことを確認。
- 未達あり: 正式リンクは設定し、merge gateと未チェック項目は保持。
- Closes記載済みだがAPI空: 未確認/失敗と報告。

## 公式根拠

https://docs.github.com/en/issues/tracking-your-work-with-issues/using-issues/linking-a-pull-request-to-an-issue
https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/managing-repository-settings/managing-auto-closing-issues
操作時のCLIフィールドは `gh pr view --help` で確認。commit messageのclosing keywordだけではPR自体のlinked表示の代わりにならない。
