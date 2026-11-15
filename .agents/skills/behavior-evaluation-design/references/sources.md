# 参考と採用範囲

2026-11-15に次の一次資料を参照して、このrepo向けに構成した。第三者のスキルをインストールしたものではない。

| 出典 | 取り入れた考え方 | そのまま採用しない部分 |
|---|---|---|
| [OpenAI skill-creator](https://github.com/openai/skills/blob/main/skills/.system/skill-creator/SKILL.md) | 説明を短くし、詳しい成果物の型を参照へ分ける | ローカルで提供されたskill-creatorとrepo規則を優先する |
| [Anthropic skill-creator](https://github.com/anthropics/skills/blob/main/skills/skill-creator/SKILL.md) | 現実の依頼で試し、比較対象と出力を分けて扱う | 一斉起動、実行中のassertion追加、repo内への実験保存はこのrepoの枠・実行前固定・保存規則に従わないため移植しない |
| [Anthropic grader](https://github.com/anthropics/skills/blob/main/skills/skill-creator/agents/grader.md) | 出力の根拠で採点し、採点条件自体の欠陥も点検する | 専用agentやJSON形式を新設せず、既存Reviewerと設計書を使う |
| [Superpowers writing-skills](https://github.com/obra/superpowers/blob/main/skills/writing-skills/SKILL.md) | 基準となる挙動と、実際の適用・不足材料の場面を確かめる | 未検証文書の削除や無条件の反復・起動は移植しない。再現しなかった場合はRouting 6の採否判断に従う |

参照したこと、形式が整ったこと、チェック項目を埋めたことは、このSkillの有効性の証拠ではない。スキルへの依存と比較の条件をそろえた別の観測が必要になる。
