# CT Navigator — Color CT 阶段问题汇报

**日期**: 2026-05-27
**负责人**: Kimi Code (AI Assistant)
**状态**: Phase C/D 功能开发完成，存在一个未解决的 Krita API 限制

---

## 一、项目背景

CT Navigator 是 Krita 插件，用于帮助数字绘画从业者进行构图、明度、色彩观察。采用"快照 + 效果叠加"模式，非实时导航。

当前处于 **Phase C（Color 标签页重写）** 和 **Phase D（UI 优化）** 阶段。

---

## 二、已完成的功能

| 功能 | 状态 | 说明 |
|---|---|---|
| Structure 标签页 | ✅ 完成 | H-Flip / V-Flip / Desat / Invert / Binarize |
| Value 标签页 | ✅ 完成 | Auto-desat + Depth 滑块（0-8 级模糊） |
| Color 标签页预览 | ✅ 完成 | Original / Squint Blur 双模式 |
| Palette Board 涂抹 | ✅ 完成 | 自由手绘，QImage + QPainter |
| Board 宽度自适应 | ✅ 完成 | 画布宽度 = widget 宽度，高度固定 120px |
| Board 颜色读取 | ✅ 完成 | 通过 `colorForCanvas()` 读取 Krita 前景色 |
| Board Resize 不拉伸 | ✅ 完成 | 旧内容左上角对齐复制，不缩放 |
| Freeze / Hold | ✅ 完成 | 快照 + 对比，Board 画布同步冻结 |
| Brush Size 滑块 | ✅ 完成 | 2-20px |
| 右侧/底部 Resize Bar | ✅ 完成 | 8px 拖拽条，自定义面板尺寸 |
| 右侧 Splitter 移除 | ✅ 完成 | 改为固定布局 + 呼吸间隙 |
| 右侧/底部 Resize Bar | ✅ 完成 | 8px 拖拽条，自定义面板尺寸 |
| 颜色注入解耦 | ✅ 完成 | Board 不再直接调用 Krita API，通过 provider 注入 |
| 拾取功能（Shift+点击） | ⚠️ 部分完成 | 可读取 Board 像素，但无法设回 Krita 前景色 |

---

## 三、核心未解决问题

### 问题：Board 拾取的颜色无法自动设回 Krita 前景色

**现象**:
- Board 上 Shift+点击 → 正确读取像素颜色
- 但 `view.setForeGroundColor(managed)` 调用后，Krita 前景色不变
- 用户回到画布绘画时，画笔仍使用旧颜色

**已尝试的修复**（全部无效）:

| 方案 | 结果 |
|---|---|
| `ManagedColor("RGBA", "U8", "")` 全新构造 | ❌ 无效 |
| 使用 `doc.colorProfile()` 构造 | ❌ 无效 |
| 克隆 `view.foregroundColor()` 修改 components | ❌ 无效 |
| `managed.fromQColor(color)` | ❌ 无效 |
| 根据 `colorDepth`（U8/F16/F32）调整值范围 | ❌ 无效 |
| 直接传入 `QColor` | ❌ 无效 |

**根因判断**:
Krita Python API 暴露的 `view.setForeGroundColor()` 方法**存在但调用不生效**。可能是：
1. API 内部有状态保护，拒绝外部修改
2. 需要在特定事件循环/线程中调用
3. Krita 版本 bug（Python 3.13.5 + PyQt5 5.15.11）

---

## 四、需求确认

| 需求 | 优先级 | 当前状态 |
|---|---|---|
| Board 自由涂抹 | P0 | ✅ 已完成 |
| 颜色自动读取 Krita 前景色 | P0 | ✅ 已完成（`colorForCanvas`） |
| Board 拾取颜色设回 Krita | P1 | ⚠️ 卡住在 API 限制 |
| Board 作为调色板使用 | P1 | ⚠️ 依赖上一条 |
| UI 拖拽手感优化 | P2 | ✅ 已完成 |
| 冻结/对比功能 | P2 | ✅ 已完成 |

---

## 五、可选方案

### 方案 A：放弃自动设回，改为手动提示
- Shift+点击 Board 取色后，在状态栏显示 `"Picked: #ff8000"`
- 用户手动输入 HEX 到 Krita 色环
- **优点**: 零技术风险，立即可用
- **缺点**: 多一步操作，打断工作流

### 方案 B：继续攻坚 Krita API
- 深入研究 Krita 源码，寻找 `setForeGroundColor` 的正确调用方式
- 或尝试通过 `Krita.action()`、`QShortcut`、事件注入等绕过
- **优点**: 如果成功，体验最佳
- **缺点**: 时间不可控，可能最终发现是 Krita 限制，无法绕过

### 方案 C：降级为显示工具
- Board 只做"调色板/混合区"，不参与 Krita 颜色管道
- 拾取的颜色仅显示在 Board 内部（圆点 + 状态提示）
- 用户用 Krita 吸管在 Board 上取色（如果 Krita 吸管支持抓取 docker）
- **优点**: 最简单，不改变现有架构
- **缺点**: 拾取和涂抹分两步，体验割裂

---

## 六、推荐决策

**建议采用方案 A（手动提示）作为 Phase C 收尾**，原因：
1. 核心涂抹功能已完全可用
2. API 限制属于 Krita 框架层面，非插件可控
3. 手动提示虽然多一步，但不阻塞主工作流
4. 方案 B 的时间成本不可控，建议作为后续优化项

**如果总监批准方案 B**，需要：
- 投入 1-2 天深入研究 Krita Python API 源码
- 或联系 Krita 开发者确认 `setForeGroundColor` 的设计意图

---

## 七、技术债务清单

| 债务 | 影响 | 建议处理时机 |
|---|---|---|
| `docker.py` 中旧的 `_get_krita_foreground_color()` 死代码 | 低 | 已清理 |
| `effects.py` 使用 `Format_ARGB32`，Board 使用 `Format_RGB32` | 低 | 当前无交叉调用，暂不处理 |
| `_set_current_color` 中 `setForeGroundColor` 失效代码 | 中 | 决策后清理或保留注释 |
| palette_board.py `_get_brush_color()` 冗余 QColor 构造 | 低 | 可选优化 |

---

## 八、附件

- 相关文件: `docker.py`, `palette_board.py`
- Krita 版本: Python 3.13.5, PyQt5 5.15.11
- API 检查结果: `view.setForeGroundColor` 存在但调用无效（5 种构造方式均失败）
