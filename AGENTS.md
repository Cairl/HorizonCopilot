# HorizonCopilot — Agent Guide

## 项目定位

基于 `UdlrTui` 库构建的《极限竞速：地平线》拍卖场抢车自动化工具。

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

#### `tasks/auction/` — 拍卖场抢车任务

| 模块 | 职责 |
|------|------|
| `sniper.py` | `AuctionTask` 抢车任务实现、`FeatureType` 枚举、`SLOT_LABELS` 标签、默认步骤树、`_run_core_loop` 主循环 |
| `data/` | 任务数据（`config.json` + 模板 PNG） |

## 执行图模型

`StepConfig` 支持 `branches: list[Branch]`，每个 `Branch` 带 `condition` / `steps` / `loop`。`get_steps()` 返回分支树，两个分支点：

1. 有车/无车检测 → 有车状态继续购买，无车状态 Esc 循环
2. 成功/失败检测 → 成功结束，失败 Enter/Esc/Esc 循环

### 步骤类型

| type | 标签 | 可导航 | 说明 |
|------|------|--------|------|
| `keypress` / `click` | 点击 | 是 | 完整按下抬起动作，延迟显示在左侧（`等待 N ms 点击 X`） |
| `press` | 按下 | 是 | 预留，仅按下 |
| `release` | 抬起 | 是 | 预留，仅抬起 |
| `match` | 判断 | 否 | 结构性节点，**不渲染行**，分支提升到 match 自身层级 |

### 导航与编辑

- `_iter_nav_steps` 只产出可导航步骤（keypress/click/press/release），`match` 被跳过
- 光标停在可导航行上，←→ 直接调整延迟（每次 ±10ms），Shift+←→ 以 1000ms 为单位调整
- 上下键划走即可离开，无需 Enter 确认 / Esc 取消
- 调整后立即热保存到 config（序列化整棵树写回）
- `match` 步骤不渲染行，其分支（如 有车状态 / 无车状态）提升到 match 自身层级，以紫色（MAUVE）显示

### 运行时状态

`_build_node_map` 构建 `node_id → StepConfig/Branch` 映射，`_run_core_loop` 通过 `node_id` 寻址更新 `runtime_status`。运行态用光标高亮（`›` + 背景高亮）表示当前步骤，未走的分支整组置灰（`_ST_DIM`）。

## 焦点守卫

`core/focus.py` 提供游戏窗口焦点检测与激活：

- `is_game_focused()` — 检测地平线 6 是否在前台
- `find_game_window()` — 枚举顶层窗口返回游戏句柄
- `activate_game_window()` — 激活游戏窗口到前台（Alt 键 hack 绕过前台锁定）
- `FocusGuard.check()` — 失焦即返回 False，调用方应立即终止运行

点击"开始运行"时自动调用 `activate_game_window()` 切到游戏窗口。运行中每次按键前调用 `guard.check()`，失焦立即终止运行（抛出 `_PauseExit` 异常跳出循环），避免按键被送到控制台。

## 特征库

`FeatureStore` 参数化设计：槽位类型和标签由任务注入。auction 任务使用 4 个固定槽位：`car_present` / `car_absent` / `auction_success` / `auction_failure`。槽位标签见 `SLOT_LABELS`，与执行图分支条件名保持一致。

## 配置

`tasks/auction/data/config.json` 存储区域坐标、模板路径、阈值、步骤延迟。步骤延迟热保存：行内编辑提交后立即序列化整棵树写回。

## 按键读取

通过 UdlrTui 统一处理：
- `udlrtui.get_key()` — 阻塞读取（idle 循环）
- `udlrtui.try_get_key()` — 非阻塞读取（running 循环）
- `udlrtui.drain_keyboard()` — 清空缓冲区
