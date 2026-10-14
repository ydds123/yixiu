# Codex Sidecar

只在 Codex 本地线程中使用本适配器。它读取当前 `CODEX_THREAD_ID` 对应的唯一会话 JSONL，只接受 `event_msg → item_completed → UserMessage / AgentMessage`，排除开发者指令、环境上下文、隐藏推理和工具调用。

脚本随本 Skill 保存，路径相对于 Skill 目录为 `scripts/codex_sidecar.py`。在本项目中的位置是：

```text
.codex/skills/goodidea-capture/scripts/codex_sidecar.py
```

## 开始

先确定本次原始思考记录的路径，再运行：

```bash
python3 .codex/skills/goodidea-capture/scripts/codex_sidecar.py start \
  --output '数据/记录/闪念/原始思考记录/YYYY-MM-DD-简短标题.md'
```

默认从当前轮次的 `task_started` 开始回溯，因此触发捕捉的用户消息不会因为脚本启动稍晚而丢失。命令同步已有消息后启动独立后台观察进程；正常运行不需要在对话里播报。

同一 Codex 线程同时只允许一份活动记录。运行状态、日志和游标写入 `.goodidea/sidecar/`，不进入 Git；原始思考 Markdown 才是项目内容。

项目根目录通过所在的 `.codex/skills/goodidea-capture/SKILL.md` 或已有的 `数据/记录/` 定位，不依赖产品定义或对象契约文件。独立复制到其他位置、且目标工作目录尚无上述结构时，从确定的目标项目根目录执行命令，脚本使用当前目录。脚本路径应改为复制后的准确位置，输出和运行状态仍属于目标项目。

## 恢复与检查

后台观察异常或需要核对时使用：

```bash
python3 .codex/skills/goodidea-capture/scripts/codex_sidecar.py status
python3 .codex/skills/goodidea-capture/scripts/codex_sidecar.py sync
```

`sync` 可以重复运行。脚本通过线程 ID、事件 ordinal、消息 ID 和 Markdown 中的隐藏来源标记避免重复追加。Codex 正在写入的最后一行如果尚不完整，会留到下一次同步，不会猜测或截断。

## 关闭

用户明确结束捕捉，或确认正式闪念并完成关联后运行：

```bash
python3 .codex/skills/goodidea-capture/scripts/codex_sidecar.py close
```

`close` 先把状态设为待关闭，继续观察到当前轮的实际最终回复和 `task_complete`，完成最后一次同步后才关闭。这样 Agent 不需要在回复前复制自己的答案。需要立即停止且不再记录当前轮后续输出时，才使用 `close --now`。

记录中的 `messages-end` 隐藏标记是对话区边界。Sidecar 始终把新消息插入这个边界之前，因此正式形成卡片后增加的“去向”不会被后续最后一条可见回复打乱。

## 当前边界

- 这是 Codex 内部 JSONL 的可替换适配器，不是稳定公开接口；结构变化时必须停止写入并暴露错误。
- Sidecar 不扫描消息正文来判断是否进入捕捉。开始和结束仍由用户授权、Skill 判断并调用命令。
- Sidecar 不改写已记录原文，不记录隐藏内容，不读取其他线程。
- 第一条触发消息已经由 Codex 宿主写入会话文件；Sidecar 从当前轮次回溯取得它。Sidecar 不宣称自己位于 Codex 模型推理之前。
