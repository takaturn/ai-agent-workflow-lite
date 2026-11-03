# 保護対象更新の運用見直し案

状態: 実装・初回移行済み（293da3d）。AIのローカルcommitを許可し、公開と実効guard更新には正確なcommitへの人の承認を要求する。

## 目的

AIが保護対象の修正・検証・独立レビュー・commitまで担当し、人は正確なcommitの承認と実効設定のtrustを担当する。未承認変更で診断用toolまで停止する循環を解消する。今回の旧hook互換性不具合を、設定再起動や人手commitの繰返しで回避しない。

## 実装した境界

- 検査を行うのは承認時に共通Git directoryへ配置したguard。作業treeの未承認guardを実効guardとして使わない。
- 作業treeの保護対象変更はPreToolUseで検出・報告するが、修正・検証・ローカルcommitのためのtool実行を一律拒否しない。承認記録破損、実効guard不一致、対象repository識別失敗は引き続き拒否する。
- push前のnative hookと公開ラッパーは、承認済みguardによる検査を必須にする。未承認の保護対象commitがあれば公開しない。作業treeのソースを書き換えて検査を通す運用を認めない。
- AIのcommitは承認ではない。人がレビュー結果・検証結果とcommitを確認し、正確なcommitをapproveした後に実効guardを更新する。hook定義の変更は別途実効source・trustを確認する。
- raw CLI等の意図的迂回を防ぐ隔離は保証しない。現在の協調的運用の範囲を維持し、公開ラッパー外の投稿を自動的に捕捉できるとは主張しない。

## 検証と移行

1. 現在の公開ラッパーが承認済みguardを呼ぶ経路を確定し、欠落している検査を追加する。PreToolUseの拒否を緩める変更だけを先行導入しない。
2. 未承認変更でも診断・試験・commitが可能で、公開・実効guard更新は拒否されることを検証する。変更、削除、途中commit、旧hook呼出し、linked worktree、承認state欠落を含める。
3. 独立レビューで権限変更と公開経路を確認する。AGENTS.mdとguardの変更は、この設計を承認した正確なcommitについて、現行手順で最後に一度導入する。

guard25件・公開ラッパー13件・native hook14件成功。独立レビューと実旧guardからの移行fixtureを実施し、人手で初回commit・再承認を完了した。公開receiptは承認stateと現在のsnapshotも固定する。Root Stop hookの実機受入とは別の検証である。
