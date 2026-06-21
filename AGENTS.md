# HorizonCopilot — Agent Guide

## 项目定位

基于 `UdlrTui` 库构建的《极限竞速：地平线》自动化工具，目前包含拍卖场抢车和图外循环蓝图赛事两个任务。

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

## 执行图模型

`StepConfig` 支持 `branches: list[Branch]`，每个 `Branch` 带 `condition` / `steps` / `loop`。两种结构：

1. **分支树**（拍卖场抢车）：`match` 步骤下挂分支，两个分支点——有车/无车检测、成功/失败检测
2. **扁平序列**（图外循环蓝图赛事）：`match` 步骤无分支，循环检测直到识别到才继续后续步骤

### 步骤类型

| type | 标签 | 可导航 | 说明 |
|------|------|--------|------|
| `keypress` / `click` | 点击 | 是 | 完整按下抬起动作，延迟显示在左侧（`等待 N ms 点击 X`） |
| `hold` | 按住 | 是 | 按下并保持 N ms 后抬起（`等待 N ms 按住 X`），延迟为按住时长 |
| `press` | 按下 | 是 | 预留，仅按下 |
| `release` | 抬起 | 是 | 预留，仅抬起 |
| `wait` | 等待 | 是 | 纯等待步骤（`等待 N ms`） |
| `match` | 判断 | 是 | 截图识别步骤（`每隔 N ms 检测 {特征名}`），延迟为截图前等待画面稳定的时间 |
| `Branch` | — | 否 | 分支条件行，渲染为纯条件名（如`有车状态`），紫色（MAUVE）显示，不可导航——判定后直接操作 |

### 导航与编辑

- `_iter_nav_steps` 产出所有可导航节点：`keypress`/`click`/`hold`/`press`/`release`（delay）、`wait`（wait）、`match`（match_delay）。`Branch` 不可导航——它是判定结果，无需等待
- 光标停在可导航行上，←→ 直接调整延迟（每次 ±10ms），Shift+←→ 以 1000ms 为单位调整
- 上下键划走即可离开，无需 Enter 确认 / Esc 取消
- 调整后立即热保存到 config（序列化整棵树写回）
- `match` 步骤渲染为「每隔 N ms 检测 {特征名}」（特征名来自 `feature_type` 经 `SLOT_LABELS` 映射），延迟为截图前等待画面稳定的时间。其分支条件行（如 有车状态 / 无车状态）渲染为纯条件名，以紫色（MAUVE）显示
- 运行时 `match` 步骤通过 `_countdown` 倒计时显示剩余毫秒，倒计时结束后瞬时截图判定
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

`FeatureStore` 参数化设计：槽位类型和标签由任务注入。拍卖场抢车任务使用 4 个固定槽位：`car_present` / `car_absent` / `auction_success` / `auction_failure`；图外循环蓝图赛事任务使用 1 个槽位：`race_finished`。槽位标签见各任务的 `SLOT_LABELS`，与执行图分支条件名保持一致。

## 配置

各任务的 `data/config.json` 存储区域坐标、模板路径、阈值、步骤延迟。步骤延迟热保存：行内编辑提交后立即序列化整棵树写回。

## 按键读取

通过 UdlrTui 统一处理：
- `udlrtui.get_key()` — 阻塞读取（idle 循环）
- `udlrtui.try_get_key()` — 非阻塞读取（running 循环）
- `udlrtui.drain_keyboard()` — 清空缓冲区
