# HorizonCopilot — Agent Guide

## 项目定位

基于 `UdlrTui` 库构建的《极限竞速：地平线》自动化工具，目前包含拍卖场抢车、图内循环蓝图赛事、图外循环蓝图赛事和购买斯巴鲁抽奖四个任务。

## 依赖关系

- `UdlrTui`（`s:\Github Repositories\UdlrTui`）— TUI 库，修改本项目时按需进化前者
- `pyautogui` — 键盘输入
- `opencv-python` / `numpy` — 模板匹配
- `msvcrt` — 原始按键读取（通过 UdlrTui 间接使用）

## 项目结构

### `core/` — 项目级通用基础设施

| 模块 | 职责 |
|------|------|
| `task_base.py` | `BaseTask` 模板方法基类、`StepConfig` / `Branch` dataclass、`calc_width` 工具函数、运行时状态常量 |
| `feature_store.py` | 通用 `FeatureStore` / `FeatureSlot`（参数化槽位类型，含 `match_slot` 模板匹配） |
| `feature_editor.py` | 通用特征截取工作流 `capture_slot_feature` |
| `screen_capture.py` | `select_region` 拖拽框选 + `capture_template_to` 区域截图 |
| `template_match.py` | `match_template` OpenCV 灰度模板匹配 |
| `focus.py` | `FocusGuard` 游戏窗口聚焦检测 |

### `tasks/` — 任务目录（每个任务一个文件夹）

任务发现机制：`tasks/__init__.py` 扫描子目录，每个子目录的 `__init__.py` 需导出 `task_info` 字典。

#### `tasks/拍卖场抢车/` — 拍卖场抢车任务

| 模块 | 职责 |
|------|------|
| `sniper.py` | `AuctionTask` 抢车任务实现、`FeatureType` 枚举、`SLOT_LABELS` 标签、默认步骤树、`_run_core_loop` 主循环 |
| `data/` | 任务数据（`config.json` + 模板 PNG） |

#### `tasks/图外循环蓝图赛事/` — 图外循环蓝图赛事任务

| 模块 | 职责 |
|------|------|
| `racer.py` | `RaceLoopTask` 赛事任务实现、`FeatureType` 枚举、`SLOT_LABELS` 标签、默认步骤树、`_run_core_loop` 主循环（含 `hold` 按住按键支持） |
| `data/` | 任务数据（`config.json` + 模板 PNG） |

#### `tasks/图内循环蓝图赛事/` — 图内循环蓝图赛事任务

| 模块 | 职责 |
|------|------|
| `racer.py` | `RaceInnerTask` 赛事任务实现、`FeatureType` 枚举（`race_start` / `race_finished`）、`SLOT_LABELS` 标签、默认步骤树、`_run_core_loop` 主循环（含 `press` 仅按下 / `release` 仅抬起支持） |
| `data/` | 任务数据（`config.json` + 模板 PNG） |

#### `tasks/购买斯巴鲁抽奖/` — 购买斯巴鲁抽奖任务

| 模块 | 职责 |
|------|------|
| `subaru.py` | `SubaruTask` 抽奖任务实现、`FeatureType` 枚举（`subaru_factory` / `subaru_car`）、`SLOT_LABELS` 标签、默认步骤树、`_run_core_loop` 主循环（含 `click_match` 模板点击支持） |
| `data/` | 任务数据（`config.json` + 模板 PNG） |

## 执行图模型

`StepConfig` 支持 `branches: list[Branch]`，每个 `Branch` 带 `condition` / `steps` / `loop`。三种结构：

1. **分支树**（拍卖场抢车）：`match` 步骤下挂分支，两个分支点——有车/无车检测、成功/失败检测。单次截图后取首个匹配分支（one-shot）
2. **循环分支判断**（图外循环蓝图赛事·检测赛事准备）：`match` 步骤带 `loop_until_match=True` 和分支，每次倒计时后依次检测各分支特征，取首个匹配者；无匹配时重新倒计时再检测。分支可用 `loop="回到本步骤"` 在执行后回到本 `match` 步骤继续检测
3. **扁平序列**（图外循环蓝图赛事·检测赛事完成）：`match` 步骤无分支，循环检测直到识别到才继续后续步骤

### 步骤类型

| type | 标签 | 可导航 | 说明 |
|------|------|--------|------|
| `keypress` / `click` | 点击 | 是 | 完整按下抬起动作，延迟显示在左侧（`等待 N ms 点击 X`） |
| `hold` | 按住 | 是 | 按下并保持 N ms 后抬起（`等待 N ms 按住 X`），延迟为按住时长 |
| `press` | 按下 | 是 | 仅按下按键不抬起（`keyDown`），用于跨步骤保持按键状态（如按住油门跑全程），延迟极小（通常 0.01s） |
| `release` | 抬起 | 是 | 仅抬起按键（`keyUp`），配合 `press` 使用，在匹配步骤完成后释放之前按下的按键 |
| `click_match` | 点击 | 是 | 模板匹配后鼠标点击匹配位置（`每隔 N ms 点击 {特征名}`），loop until found → pyautogui.click |
| `wait` | 等待 | 是 | 纯等待步骤（`等待 N ms`） |
| `match` | 判断 | 是 | 截图识别步骤（`每隔 N ms 检测 {特征名}`），延迟为截图前等待画面稳定的时间。三种模式：无分支（循环检测直到识别到）、单次分支（`fallback_key` 兜底，one-shot）、循环分支（`loop_until_match=True`，循环检测直到某分支匹配） |
| `Branch` | — | 否 | 分支条件行，渲染为纯条件名（如`有车状态`），紫色（MAUVE）显示，不可导航——判定后直接操作 |

### 导航与编辑

- `_iter_nav_steps` 产出所有可导航节点：`keypress`/`click`/`hold`/`press`/`release`（delay）、`wait`（wait）、`match`/`click_match`（match_delay）。`Branch` 不可导航——它是判定结果，无需等待
- 光标停在可导航行上，←→ 直接调整延迟（每次 +-10ms），Shift+←→ 以 1000ms 为单位调整。无需 Enter 进入编辑模式
- 上下键划走即可离开，无需 Enter 确认 / Esc 取消
- 调整后立即热保存到 config（序列化整棵树写回）
- `match` 步骤渲染为「每隔 N ms 检测 {特征名}」（特征名来自 `feature_type` 经 `SLOT_LABELS` 映射），延迟为截图前等待画面稳定的时间。其分支条件行（如 有车状态 / 无车状态）渲染为纯条件名，以紫色（MAUVE）显示
- 运行时 `match` 步骤通过 `_countdown` 倒计时显示剩余毫秒，倒计时结束后瞬时截图判定
- `match` 步骤的 `loop_until_match=True` 时走循环分支模式（`_looping_branched_match`）：每次倒计时后依次检测各分支特征，取首个匹配者执行其 `steps`；无匹配时重新倒计时。分支 `loop` 取值：`"结束"` 结束运行、`"回到本步骤"` 回到本 `match` 继续检测、其他值（含 `None`）继续后续步骤
- **0ms = 暂停**：将步骤延迟设为 0ms 时，运行到该步骤不会立即执行，而是将运行状态归位（开始按钮变回"开始运行"、所有节点状态清零无高亮、光标聚焦到当前 0ms 行但不锁住——用户可继续移动光标 / 调整延迟），阻塞等待用户按 Enter 继续执行；Esc/Backspace 终止运行。暂停期间不检查焦点守卫

### 运行时状态

`_build_node_map` 构建 `node_id → StepConfig/Branch` 映射，`_run_core_loop` 通过 `node_id` 寻址更新 `runtime_status`。运行态用光标高亮（`›` + 背景高亮）表示当前步骤，未走的分支整组置灰（`_ST_DIM`）。`match` 步骤通过 `_countdown` 倒计时等待画面稳定后截图，`keypress` 步骤通过 `_countdown` 倒计时等待后按键，`hold` 步骤通过 `_countdown` 倒计时按住时长（`keyDown` → 倒计时 → `keyUp`，`finally` 保证抬起）。

## 焦点守卫

`core/focus.py` 提供游戏窗口焦点检测与激活：

- `is_game_focused()` — 检测地平线 6 是否在前台
- `find_game_window()` — 枚举顶层窗口返回游戏句柄
- `activate_game_window()` — 激活游戏窗口到前台（Alt 键 hack 绕过前台锁定）
- `FocusGuard.check()` — 失焦即返回 False，调用方应立即终止运行

点击"开始运行"时自动调用 `activate_game_window()` 切到游戏窗口。运行中每次按键前调用 `guard.check()`，失焦立即终止运行（抛出 `_PauseExit` 异常跳出循环），避免按键被送到控制台。

## 特征库

`FeatureStore` 参数化设计：槽位类型和标签由任务注入。拍卖场抢车任务使用 4 个固定槽位；图外循环蓝图赛事任务使用 3 个槽位；图内循环蓝图赛事任务使用 2 个槽位；购买斯巴鲁抽奖任务使用 2 个槽位。槽位标签见各任务的 `SLOT_LABELS`，与执行图分支条件名保持一致。

## 配置

各任务的 `data/config.json` 存储区域坐标、模板路径、阈值、步骤延迟。步骤延迟热保存：行内编辑提交后立即序列化整棵树写回。

## 交互模型

- 左栏 `Enter` 执行图/特征库 → 打开视图并切换焦点到右栏。`→` 不再切换焦点
- 右栏步骤选中行后 `←→` 直接调整延迟，`Enter` 从该行开始运行，`Esc` 退回左栏
- 右栏特征库选中行后 `←→` 切换截取/删除，`Enter` 执行操作，`Esc` 退回左栏
- 无编辑模式——所有操作在选中行上直接执行

## 视口滚动

右栏执行图行数超过终端高度时自动启用视口滚动：`_viewport_start` 跟踪视口起始行，`render_idle` 中按 `term_h - 5` 计算最大可见行数并切片渲染。选中行自动保持在视口内（上溢出时上移视口，下溢出时下移）。切换视图时重置到顶部。

## 执行图行号

每个可导航步骤前显示 `{num:>3} ` 右对齐行号（tree prefix 的一部分，灰色渲染）。不可导航行（分支条件）用 4 空格占位保持对齐。行号与 `_iter_nav_steps` 产出顺序一致。

## 按键读取

通过 UdlrTui 统一处理：
- `udlrtui.get_key()` — 阻塞读取（idle 循环）
- `udlrtui.try_get_key()` — 非阻塞读取（running 循环）
- `udlrtui.drain_keyboard()` — 清空缓冲区
