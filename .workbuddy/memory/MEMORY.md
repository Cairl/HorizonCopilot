# HorizonCopilot — 项目长期记忆

## 架构要点
- 基于 `UdlrTui`（`s:\Github Repositories\UdlrTui`，editable 安装）构建的《极限竞速：地平线 6》自动化工具。
- `core/task_base.py` `BaseTask` 是模板方法基类：idle↔running 状态机。子类提供 `_setup`/`get_steps`/`_run_core_loop`。`intro_text` 类属性定义任务介绍文本，idle 时在左栏菜单底部（特征库下方）以 muted 灰色渲染。
- **通用树执行器** `_execute_tree(start_node_id)`（2026-06-22 引入）取代手写主循环，驱动 keypress/hold/wait/match（分支型+无分支型）。`StepConfig.fallback_key` 用于分支 match 无匹配时的兜底按键。`branch.loop=="结束"` 结束运行并 `found++`。
- **三种 match 模式**（2026-06-22 扩展）：无分支（循环检测直到识别到）、单次分支 `_oneshot_branched_match`（one-shot + fallback_key 兜底）、循环分支 `_looping_branched_match`（`StepConfig.loop_until_match=True`，循环倒计时直到某分支匹配）。分支 `loop=="回到本步骤"` 在循环分支模式下回到本 match 继续检测。`_match_step` 按是否有分支 + `loop_until_match` 派发。
- **两栏书页布局**：左栏菜单（开始运行 / 设置分节 / 执行图 / 特征库），右栏常开默认展开执行图。`Tab` 切栏。执行图步骤行按 Enter = 从该行开始运行（`_start_node`）。特征缺失不阻碍启动，运行到缺失 match 行停止并提示。右栏执行图行数超过终端高度时自动启用视口滚动：`_viewport_start` 跟踪视口起始行，`render_idle` 中按终端高度 `max_content = term_h - 5` 切片渲染，选中行自动保持在视口内，上下溢出显示 `...` 指示器。
- 特征库 `FeatureStore` v4 JSON，槽位类型/标签/类别由任务注入。槽位分两类：**监测特征**（`CATEGORY_MONITOR`，`match` 步骤用，固定区域内判断）和**定位特征**（`CATEGORY_LOCATOR`，`click_match` 步骤用，全屏搜索点击）。特征库视图按类别分组渲染（`iter_by_category()`，monitor→locator），每组带不可导航标题行。`match_slot` 模板匹配。`locate_template`（region 内）服务 match，`locate_template_fullscreen`（全屏）服务 click_match。
- `tasks/__init__.py` `discover_tasks` 把 `tag=="auction"`（拍卖场抢车）钉在首位，其余按目录名排序。
- 焦点守卫 `core/focus.py`：失焦即终止运行。`activate_game_window` 用 Alt 键 hack 绕过前台锁定；窗口切换异步，执行器启动前重试 10×50ms 等焦点。
- 任务介绍文本 `intro_text`：类属性定义，`_wrap_intro` 按显示宽度自动折行（CJK 感知），idle 时在左栏菜单底部（特征库下方）以 `C.SUBTEXT` 渲染（2026-06-24 由 GRAY+DIM 调亮，原双倍变暗看不清）。
- **置信度阈值**（2026-06-24）：运行时不再用全局 `global_threshold_fallback`，改用各槽位自身 `slot.threshold`（`_slot_threshold(ftype)`）。特征库每槽位 3 个操作 [截取]/[删除]/[置信度:N]（`_slot_action` 0/1/2，←→ 循环切换）。action==2 时 Enter 进调节子模式（`right_editing=True`），←→ 调值(±0.05, Shift ±0.10)，Enter 保存 persist，Esc 取消恢复（`_thr_backup`）。`_oneshot/_looping_branched_match` 已移除 fallback 参数。`feature_editor` 截取时保留槽位已调阈值。
- **循环次数**（2026-06-24）：执行图顶部第一行 `循环次数: N`（N=0 显示「无限」；运行时显示 `M/无限`）。`_loop_count_focused` 标记光标是否在该行（首步骤 Up 进入，Down 回首步骤），Enter 从头运行。持久化到 `store._settings["loop_count"]`。循环次数行作为 `fixed_header` 在视口滚动时始终置顶。**公式支持**：任务可定义 `loop_formula_template`+`loop_formula_default_terms`（非 None 即启用），**聚焦时**显示 `{N}={公式}`（高亮当前可编辑数字），**未聚焦只显示** `循环次数: N`。`Shift+←→` 切换可编辑数字（`_loop_active` 0=N/1..n=terms），普通 `←→` 调当前数字 ±1（active=0 自由编辑 N；active>=1 编辑该项后 `N=loop_formula_compute(terms)` 向下取整重算），持久化 `loop_formula_terms`。斯巴鲁=`{0}÷{1}`[357,30]→11；图内循环=`({0}−{1})÷{2}`[999,357,20]→32；拍卖场/图外循环无公式。
- **预计/实际/剩余用时**（2026-06-24）：执行图 title 为 `"执行图"`，用时信息作为 title 次行（`_build_timing_row`）以淡色(C.LABEL)独立显示，格式 `MM:SS`（`_fmt_mmss` 工具，MM 可超 60）。预计=`_estimate_loop_duration`（所有 nav 步骤 delay 之和）。实际=**上一次完整循环的耗时**（`_last_loop_time`，每轮 `_walk` 完成后记录，第一轮无数据不显示）。剩余=`实际 × 当前剩余次数`（仅有限循环 + 有实际数据时显示；无限循环不显示）。非运行只显示预计。用时行与循环次数行均作为 `fixed_header` 置顶。

## 约定
- 步骤类型：keypress/click/hold/press/release/wait/match/click_match。Branch 不可导航（判定结果）。`press`=仅 keyDown（跨步骤保持按键，如按住油门跑全程），`release`=仅 keyUp（配合 press 释放）。`match`=在 `slot.region` 固定区域内截图判断特征是否出现（特征位置已知），渲染「每隔 N ms 检测 {特征名}」。`click_match`=全屏截图搜索特征位置（特征位置不固定，不依赖 `slot.region`），找到后点击匹配中心，渲染「每隔 N ms 左键/右键/中键 {特征名}」（标签由 `step.button` 决定，`click_match_label()` 生成）。`FeatureStore.locate_template`（region 内匹配）vs `locate_template_fullscreen`（全屏匹配）分别服务二者。0ms 延迟 = 暂停（运行状态归位，光标聚焦该行，Enter 继续/Esc 终止）。`StepConfig.loop_until_match` 仅对带分支的 match 有效。
- 延迟热保存：行内编辑提交后立即 `_step_to_dict` 整树写回 config。
- 任何地方都不要使用 emoji 符号（用户全局偏好）。

## 任务列表
- 拍卖场抢车（`tag=auction`，4 槽位：car_present/car_absent/auction_success/auction_failure）
- 图外循环蓝图赛事（`tag=race_loop`，3 槽位：race_prep/race_finished/race_lower_difficulty，含循环分支判断）
- 图内循环蓝图赛事（`tag=race_inner`，2 槽位：race_start/race_finished，扁平序列，使用 press/release 跨步骤保持 W 键）
- 购买斯巴鲁抽奖（`tag=subaru`，3 定位槽位：subaru_factory_lower=小写厂牌 / subaru_factory_upper=大写厂牌 / subaru_car=车辆卡片，65步4阶段扁平序列，含5个 click_match 步骤。2026-06-24 标签由"斯巴鲁牌(小写/大写)/斯巴鲁车"改名）
