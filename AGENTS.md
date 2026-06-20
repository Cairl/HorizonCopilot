# HorizonCopilot — Agent Guide

## 项目定位

基于 `UdlrTui` 库构建的《极限竞速：地平线》拍卖场抢车自动化工具。

## 依赖关系

- `UdlrTui`（`s:\Github Repositories\UdlrTui`）— TUI 库，修改本项目时按需进化前者
- `pyautogui` — 键盘输入
- `opencv-python` / `numpy` — 模板匹配
- `msvcrt` — 原始按键读取

## 模块结构

- `core/task_base.py` — `BaseTask` 模板方法基类、`StepConfig` / `Branch` dataclass、`FeatureStore` 槽位管理
- `core/focus.py` — `FocusGuard` 游戏窗口聚焦检测
- `core/keyboard.py` — `read_key` / `try_read_key` 按键读取
- `tasks/auction/sniper.py` — `AuctionTask` 抢车任务实现
- `tasks/auction/feature_store.py` — 特征槽位存储与模板加载

## 执行图模型

`StepConfig` 支持 `branches: list[Branch]`，每个 `Branch` 带 `condition` / `steps` / `loop`。`get_steps()` 返回分支树，两个分支点：

1. 有车/无车检测 → 有车状态继续购买，无车状态 Esc 循环
2. 成功/失败检测 → 成功结束，失败 Enter/Esc/Esc 循环

### 步骤类型

| type | 标签 | 可导航 | 说明 |
|------|------|--------|------|
| `keypress` / `click` | 点击 | 是 | 完整按下抬起动作，延迟显示在左侧（`等待 N ms  点击 X`） |
| `press` | 按下 | 是 | 预留，仅按下 |
| `release` | 抬起 | 是 | 预留，仅抬起 |
| `wait` | 等待 | 是 | 等待指定毫秒 |
| `match` | 判断 | 否 | 结构性节点，**不渲染行**，分支提升到 match 自身层级 |

### 导航与编辑

- `_iter_nav_steps` 只产出可导航步骤（keypress + wait），`match` 被跳过
- 光标停在可导航行上，←→ 直接调整延迟（每次 ±10ms），Shift+←→ 以 1000ms 为单位调整
- 上下键划走即可离开，无需 Enter 确认 / Esc 取消
- 调整后立即热保存到 config（序列化整棵树写回）
- `match` 步骤不渲染行，其分支（如 有车状态 / 无车状态）提升到 match 自身层级，以紫色（MAUVE）显示

### 运行时扁平化

`_build_full_tree` 构建完整分支树，`_flatten_tree` 展平为带 `prefix` 的线性列表。每个节点带唯一 `key`，`_run_core_loop` 通过 `idx_by_key` 字典按语义 key 寻址。未走的分支整组置灰（`_ST_DIM`）。

## 特征库

4 个固定槽位：`car_present` / `car_absent` / `auction_success` / `auction_failure`。槽位标签见 `FeatureStore.SLOT_LABELS`，与执行图分支条件名保持一致。

## 配置

`tasks/auction/data/config.json` 存储区域坐标、模板路径、阈值、步骤延迟。步骤延迟热保存：行内编辑提交后立即序列化整棵树写回。
