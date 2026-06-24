# HorizonCopilot — 项目长期记忆

## 架构要点
- 基于 `UdlrTui`（`s:\Github Repositories\UdlrTui`，editable 安装）构建的《极限竞速：地平线 6》自动化工具。
- `core/task_base.py` `BaseTask` 是模板方法基类：idle↔running 状态机。子类提供 `_setup`/`get_steps`/`_run_core_loop`。`intro_text` 类属性定义任务介绍文本，idle 时在左栏菜单底部（特征库下方）以 muted 灰色渲染。
- **通用树执行器** `_execute_tree(start_node_id)`（2026-06-22 引入）取代手写主循环，驱动 keypress/hold/wait/match（分支型+无分支型）。`StepConfig.fallback_key` 用于分支 match 无匹配时的兜底按键。`branch.loop=="结束"` 结束运行并 `found++`。
- **三种 match 模式**（2026-06-22 扩展）：无分支（循环检测直到识别到）、单次分支 `_oneshot_branched_match`（one-shot + fallback_key 兜底）、循环分支 `_looping_branched_match`（`StepConfig.loop_until_match=True`，循环倒计时直到某分支匹配）。分支 `loop=="回到本步骤"` 在循环分支模式下回到本 match 继续检测。`_match_step` 按是否有分支 + `loop_until_match` 派发。
- **两栏书页布局**：左栏菜单（开始运行 / 设置分节 / 执行图 / 特征库），右栏常开默认展开执行图。`Tab` 切栏。执行图步骤行按 Enter = 从该行开始运行（`_start_node`）。特征缺失不阻碍启动，运行到缺失 match 行停止并提示。右栏执行图行数超过终端高度时自动启用视口滚动：`_viewport_start` 跟踪视口起始行，`render_idle` 中按终端高度 `max_content = term_h - 5` 切片渲染，选中行自动保持在视口内，上下溢出显示 `...` 指示器。
- 特征库 `FeatureStore` v4 JSON，槽位类型/标签由任务注入。`match_slot` 模板匹配。
- `tasks/__init__.py` `discover_tasks` 把 `tag=="auction"`（拍卖场抢车）钉在首位，其余按目录名排序。
- 焦点守卫 `core/focus.py`：失焦即终止运行。`activate_game_window` 用 Alt 键 hack 绕过前台锁定；窗口切换异步，执行器启动前重试 10×50ms 等焦点。
- 任务介绍文本 `intro_text`：类属性定义，`_wrap_intro` 按显示宽度自动折行（CJK 感知），idle 时在左栏菜单底部（特征库下方）以 muted 灰色渲染。

## 约定
- 步骤类型：keypress/click/hold/press/release/wait/match/click_match。Branch 不可导航（判定结果）。`press`=仅 keyDown（跨步骤保持按键，如按住油门跑全程），`release`=仅 keyUp（配合 press 释放）。`click_match`=模板匹配后鼠标点击匹配位置（loop until found → pyautogui.click）。0ms 延迟 = 暂停（运行状态归位，光标聚焦该行，Enter 继续/Esc 终止）。`StepConfig.loop_until_match` 仅对带分支的 match 有效。
- 延迟热保存：行内编辑提交后立即 `_step_to_dict` 整树写回 config。
- 任何地方都不要使用 emoji 符号（用户全局偏好）。

## 任务列表
- 拍卖场抢车（`tag=auction`，4 槽位：car_present/car_absent/auction_success/auction_failure）
- 图外循环蓝图赛事（`tag=race_loop`，3 槽位：race_prep/race_finished/race_lower_difficulty，含循环分支判断）
- 图内循环蓝图赛事（`tag=race_inner`，2 槽位：race_start/race_finished，扁平序列，使用 press/release 跨步骤保持 W 键）
- 购买斯巴鲁抽奖（`tag=subaru`，2 槽位：subaru_factory/subaru_car，65步4阶段扁平序列，含5个 click_match 步骤）
