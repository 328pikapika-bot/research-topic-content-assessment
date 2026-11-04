---
name: talk-skill
description: 整理用户与AI的历史对话、问题讨论、方案推导和处理结论为Markdown文档。Use when the user asks to整理对话、整理讨论记录、生成讨论MD、复盘与Codex/Claude/ClaudeCode的历史交流、沉淀问题处理结论，尤其需要保留AI原文、添加AI思路摘要，并区分纯讨论与讨论后发生代码修改或优化的对话。
---

# Talk Skill

## Purpose

Use this skill to turn a user-AI conversation into a structured Markdown discussion document. Preserve the discussion path, the decisions made, and the AI's original responses for later review.

The output is not a code change report. If the conversation later enters implementation, testing, file edits, or optimization execution, use those later exchanges only to classify the document as a discussion-with-changes document, then omit the execution phase from the discussion body.

## Workflow

1. Identify the discussion source.
   - Use the current conversation when it is available.
   - If the conversation is not available, ask the user to provide the chat text, exported record, or source file.

2. Classify the conversation.
   - Use `【讨论】` when the conversation only contains problem analysis, solution design, rules, planning, or decision-making.
   - Use `【讨论修改】` when the discussion is followed by actual code edits, feature implementation, bug fixes, page adjustments, script changes, optimization, tests, or other execution work.

3. Keep only the discussion stage.
   - Include exchanges about what the problem is, how the feature should work, which solution to choose, what rules should apply, and what conclusion was reached.
   - Exclude exchanges after the AI starts implementation or execution, such as "I will modify it now", patch descriptions, changed file lists, test commands, build output, bug-fix iterations, or final implementation summaries.
   - In the overall summary, briefly state that follow-up operations were performed when classification is `【讨论修改】`, but do not name code files, paths, line numbers, commits, diffs, functions, or detailed implementation steps.

4. Preserve AI responses.
   - For every AI response included in the discussion stage, add `AI 思路简要总结` above it.
   - Keep `AI 原文` complete. Do not shorten, compress, merge, paraphrase, or replace the original AI response with a summary.
   - If an included AI discussion response itself contains code-related details, keep them in `AI 原文`; the omission rule only applies to the later execution stage.

5. Organize by one user-AI exchange.
   - Use one short heading for each round.
   - Prefer concise headings such as `问题提出`, `目标澄清`, `方案比较`, `规则确认`, `结论形成`.
   - Keep headings short and descriptive.
   - The user message may be lightly cleaned for readability, but preserve the user's meaning. If the user asks to preserve user wording, keep it verbatim.

## File Naming

Use this format for pure discussion:

```text
【讨论】基于XXX讨论 YYYY-MM-DD [Codex].md
```

Use this format when discussion was followed by code modification or optimization:

```text
【讨论修改】基于XXX讨论 YYYY-MM-DD [ClaudeCode].md
```

Rules:

- Replace `XXX` with a short topic extracted from the conversation.
- Use the actual date in `YYYY-MM-DD` format.
- Use the bracketed AI source requested by the user, such as `[Codex]`, `[Claude]`, or `[ClaudeCode]`.
- If the source is unclear, infer from the conversation context; otherwise ask briefly.
- Keep the topic short enough for a readable filename.

## Markdown Structure

Use this structure:

```markdown
# 基于XXX讨论

## 总体概括

本次讨论围绕「XXX」展开。用户希望解决的问题是……。经过讨论，最终确定采用……方式处理。

如果本文档属于【讨论修改】类，在这里补充一句：
讨论完成后，实际进行了相关功能调整、实现优化或问题修复，但本文档仅保留方案讨论过程，不整理具体修改过程。

## 讨论过程

### 1. 问题提出

**用户：**
……

**AI 思路简要总结：**
……

**AI 原文：**
……

**本轮整理结论：**
……

### 2. 方案澄清

**用户：**
……

**AI 思路简要总结：**
……

**AI 原文：**
……

**本轮整理结论：**
……
```

## Writing Rules

- Write in Chinese unless the source conversation or user request requires another language.
- Do not reduce the AI original response; the summary is additive, not a replacement.
- Do not include implementation-stage exchanges in the body once the conversation has moved from discussion to actual modification.
- Do not list modified files, code paths, functions, diffs, commands, or tests in the overall summary.
- Keep the overall summary concise and conclusion-oriented.
- If the final conclusion is not fully settled, label it as a stage conclusion and list pending questions briefly.
- If the user asks for an actual `.md` file, create it with the filename rules above. Otherwise, provide the Markdown content directly.
