# Good Idea 项目操作说明（给苏格等 Agent）

项目根：`/home/box/projects/goodidea`（git 仓库 ydds123/yixiu，同时是 Obsidian 库）。按当前问题读取需要的文件，不默认整库加载。

## 开聊前读什么
- `数据/记录/母题/Good Idea.md`：母题索引和当前关心的问题。
- 需要背景时：`数据/记录/项目/Good Idea/Good Idea 0.1 阶段性产品叙事.md`（七篇文章）。
- 与当前念头相关的 `数据/记录/闪念/`、`数据/记录/永久卡片/`、`数据/记录/外部来源/`。

## 捕捉与提炼
- 只有松海明确说「开始捕捉」，才按 `.codex/skills/goodidea-capture` 进入捕捉流程。
- 闪念提炼为永久卡片用 `.codex/skills/goodidea-distill`。
- 内容对象规则见 `规则/内容对象.md`。
- 闪念放 `数据/记录/闪念/`，永久卡片放 `数据/记录/永久卡片/`，文件名 `YYYY-MM-DD-标题.md`。
- 生成的内容只是候选；松海认领后才写成正式卡片。

## 提交
- 写完只 `git add` 并 `git commit` 这一个文件，不 push。

## 出长图（只在松海要的时候）
- 直接用归藏的 guizang-social-card-skill，不另做封装。
- 进入 `/home/box/skills-src/guizang-social-card-skill/templates-goodidea/`，参照 `data.dacong.json` 的字段写 `data.<slug>.json`，运行 `./build.sh magazine data.<slug>.json <out.png>`。
- 检查报 FAIL 时，只留重点或拆成几张独立卡片，不要硬切。
