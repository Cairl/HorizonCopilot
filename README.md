# HorizonCopilot

基于 `UdlrTui` 库构建的《极限竞速：地平线》自动化工具。

## 功能

- **拍卖场抢车** — 自动检测拍卖场搜索结果有车/无车，有车时执行购买流程并检测成功/失败，循环运行
- **图外循环蓝图赛事** — 执行按键序列进入赛事，每隔 N ms 检测比赛完成，识别到后执行后置按键并循环

## 特性

- **执行图可视化** — 树状步骤展示，运行时高亮当前步骤、置灰未走分支
- **行内编辑** ←→ / Shift+←→ 直接调整步骤延迟，立即热保存到 config
- **0ms 暂停** — 将步骤延迟设为 0ms 即在该步暂停，光标聚焦但不锁住，可继续移动 / 调整延迟，按 Enter 继续
- **特征库** — 拖拽框选截取特征模板，OpenCV 灰度模板匹配
- **焦点守卫** — 游戏失焦自动终止运行，避免按键误送到控制台
- **中文路径** — 任务目录中文名，OpenCV 通过 `np.fromfile` + `cv2.imdecode` 读取中文路径模板

## 依赖

- `UdlrTui` — TUI 库（独立项目）
- `pyautogui` — 键盘输入
- `opencv-python` / `numpy` — 模板匹配
- `msvcrt` — 原始按键读取（通过 UdlrTui 间接使用）

## 运行

```bash
pip install -r requirements.txt
python main.py
```

## 项目结构

```
HorizonCopilot/
├── main.py                 # 入口：任务菜单
├── core/                   # 通用基础设施
│   ├── task_base.py        # BaseTask 模板方法基类、StepConfig/Branch
│   ├── feature_store.py    # FeatureStore 参数化槽位
│   ├── feature_editor.py   # 特征截取工作流
│   ├── screen_capture.py   # 拖拽框选 + 区域截图
│   ├── template_match.py   # OpenCV 模板匹配
│   └── focus.py            # 游戏窗口焦点检测
└── tasks/                  # 任务目录（每个任务一个文件夹）
    ├── 拍卖场抢车/
    │   ├── sniper.py       # AuctionTask 实现
    │   └── data/           # config.json + 模板 PNG
    └── 图外循环蓝图赛事/
        ├── racer.py        # RaceLoopTask 实现
        └── data/           # config.json + 模板 PNG
```

## 操作

| 按键 | 作用 |
|------|------|
| ↑↓ | 移动光标 |
| ←→ | 调整步骤延迟 ±10ms |
| Shift+←→ | 调整步骤延迟 ±1000ms |
| Enter | 开始运行 / 截取特征 / 删除特征 / 暂停后继续 |
| Esc / Backspace | 退出 / 终止运行 |
| Tab | 切换焦点（预留） |
