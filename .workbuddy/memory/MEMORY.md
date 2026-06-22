# HorizonCopilot — 项目长期记忆

## 架构要点
- 基于 `UdlrTui`（`s:\Github Repositories\UdlrTui`，editable 安装）构建的《极限竞速：地平线 6》自动化工具。
- `core/task_base.py` `BaseTask` 是模板方法基类：idle↔running 状态机。子类提供 `_setup`/`get_steps`/`_run_core_loop`。
- **通用树执行器** `_execute_tree(start_node_id)`（2026-06-22 引入）取代手写主循环，驱动 keypress/hold/wait/match（分支型+无分支型）。`StepConfig.fallback_key` 用于分支 match 无匹配时的兜底按键。`branch.loop=="结束"` 结束运行并 `found++`。
- **两栏书页布局**：左栏菜单（开始运行 / 设置分节 / 执行图 / 特征库），右栏常开默认展开执行图。`Tab` 切栏。执行图步骤行按 Enter = 从该行开始运行（`_start_node`）。特征缺失不阻碍启动，运行到缺失 match 行停止并提示。
- 特征库 `FeatureStore` v4 JSON，槽位类型/标签由任务注入。`match_slot` 模板匹配。
- `tasks/__init__.py` `discover_tasks` 把 `tag=="auction"`（拍卖场抢车）钉在首位，其余按目录名排序。
- 焦点守卫 `core/focus.py`：失焦即终止运行。`activate_game_window` 用 Alt 键 hack 绕过前台锁定；窗口切换异步，执行器启动前重试 10×50ms 等焦点。

## 约定
- 步骤类型：keypress/click/hold/wait/match。Branch 不可导航（判定结果）。0ms 延迟 = 暂停（运行状态归位，光标聚焦该行，Enter 继续/Esc 终止）。
- 延迟热保存：行内编辑提交后立即 `_step_to_dict` 整树写回 config。
- 任何地方都不要使用 emoji 符号（用户全局偏好）。
