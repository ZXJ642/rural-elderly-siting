# 农村养老医疗点选址与均衡划片 QGIS插件

基于 QGIS Processing 框架的农村养老医疗服务设施选址与服务片区划分插件。
以农村居民点作为需求面、需求点坐标作为出行位置、已有及潜在医疗设施点作为候选服务点，
通过启发式算法求解服务均等、覆盖全区域的设施选址与片区划分方案。

## 功能

- 需求面（多边形）、需求点（点）、候选服务点（点）三类图层输入
- 坐标系统一、site_id 关联、候选点空间挂载、共边邻接计算
- 调用后端 EDP/ILS 启发式求解器完成设施选址与片区划分
- 输出三个图层：划片结果面、选中医疗设施点、服务片区面
- 自动配色、符号化并生成中文总结报告

## 重要说明（算法引用）

本插件核心算法 EDP/ILS、SSCFLP 求解脚本引用自老师的公开仓库：

> **[github.org/yfkong/pdp](https://github.com/yfkong/pdp)**

请在使用前从该仓库下载以下两个脚本，放入插件的 `backend/` 目录：
- `edp_ils31.py`
- `SSCFLP_MH31.py`

算法版权归原作者所有，本仓库仅提供 QGIS 调用接口，仅供学习研究使用。

## 安装说明

1. 将本插件文件夹复制到 QGIS 插件目录：
   `C:\Users\<你的用户名>\AppData\Roaming\QGIS\QGIS3\profiles\default\python\plugins\`
2. 从老师仓库下载 `edp_ils31.py`、`SSCFLP_MH31.py`，
   放入插件的 `backend/` 子文件夹。
3. 重启 QGIS，在"插件管理器"中启用本插件。
4. 首次运行需在参数面板指定：
   - PyPy2 解释器路径（如 `D:\pypy2.7-v7.3.17-win64\pypy2.exe`）
   - 后端脚本 `edp_ils31.py` 路径

## 环境要求

- QGIS 3.x（Windows）
- PyPy2.7（用于运行 Python 2 后端求解脚本）

## 版权声明

选址求解算法（EDP/ILS、SSCFLP）版权归原作者 yfkong 所有，仅供学习研究使用。
